"""Run generated long-term committee batches as a no-submit artifact stage."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


FORBIDDEN_COMMAND_FRAGMENTS = ("--submit-paper-orders",)
CommandRunner = Callable[[str], tuple[int, str, str]]


def run_committee_batch_dir(
    *,
    committee_batch_dir: str | Path,
    output_dir: str | Path,
    journal_db: str | Path,
    portfolio_state: str | Path,
    market_regime_file: str | Path | None = None,
    motley_fool_config: str | Path | None = None,
    agent_preset: str = "decision_4",
    profile_config: str | Path | None = None,
    campaign_id: str = "",
    resume: bool = False,
    print_plan_only: bool = False,
    max_batches: int | None = None,
    summary_output: str | Path | None = None,
    command_runner: CommandRunner | None = None,
) -> dict[str, Any]:
    """Run each generated committee batch in sorted order.

    Resume is artifact-based: completed batch IDs in the existing summary are
    skipped, which keeps scheduler retries from duplicating journaled decisions
    when the same output directory is reused.
    """
    if max_batches is not None and max_batches < 1:
        raise ValueError("max_batches must be a positive integer when supplied.")
    batch_dir = Path(committee_batch_dir)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    summary_path = Path(summary_output) if summary_output else root / "committee_batch_run_summary.json"
    previous_completed = _completed_batches(summary_path) if resume else set()
    batches = sorted(batch_dir.glob("*.json"))
    runner = command_runner or _run_command
    batch_results: list[dict[str, Any]] = []
    status = "planned" if print_plan_only else "completed"
    generated_at = datetime.now().isoformat()
    completed_count = 0
    failed_count = 0
    skipped_count = 0
    planned_count = 0
    processed_pending_count = 0
    _write_summary(
        summary_path,
        _summary_payload(
            campaign_id=campaign_id,
            status="planning" if print_plan_only else "running",
            batch_dir=batch_dir,
            output_dir=root,
            summary_path=summary_path,
            batch_count=len(batches),
            completed_count=completed_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            planned_count=planned_count,
            generated_at=generated_at,
            batch_results=batch_results,
        ),
    )

    for index, batch in enumerate(batches, start=1):
        batch_id = batch.stem
        cycle_output = root / f"{batch_id}_cycle.json"
        if resume and batch_id in previous_completed:
            skipped_count += 1
            batch_results.append(
                {
                    "batch_id": batch_id,
                    "batch_path": str(batch),
                    "campaign_id": campaign_id,
                    "status": "skipped_resume",
                    "cycle_output": str(cycle_output),
                    "command": "",
                    "exit_code": None,
                    "log_path": "",
                    "blocker": "",
                }
            )
            _write_summary(
                summary_path,
                _summary_payload(
                    campaign_id=campaign_id,
                    status="running",
                    batch_dir=batch_dir,
                    output_dir=root,
                    summary_path=summary_path,
                    batch_count=len(batches),
                    completed_count=completed_count,
                    failed_count=failed_count,
                    skipped_count=skipped_count,
                    planned_count=planned_count,
                    generated_at=generated_at,
                    batch_results=batch_results,
                ),
            )
            continue
        if max_batches is not None and processed_pending_count >= max_batches:
            status = "partial"
            _write_summary(
                summary_path,
                _summary_payload(
                    campaign_id=campaign_id,
                    status=status,
                    batch_dir=batch_dir,
                    output_dir=root,
                    summary_path=summary_path,
                    batch_count=len(batches),
                    completed_count=completed_count,
                    failed_count=failed_count,
                    skipped_count=skipped_count,
                    planned_count=planned_count,
                    generated_at=generated_at,
                    batch_results=batch_results,
                ),
            )
            break
        command = build_cycle_command(
            batch_path=batch,
            journal_db=journal_db,
            portfolio_state=portfolio_state,
            market_regime_file=market_regime_file,
            motley_fool_config=motley_fool_config,
            agent_preset=agent_preset,
            profile_config=profile_config,
        )
        _validate_command(command)
        if print_plan_only:
            planned_count += 1
            processed_pending_count += 1
            batch_results.append(
                {
                    "batch_id": batch_id,
                    "batch_path": str(batch),
                    "campaign_id": campaign_id,
                    "status": "planned",
                    "cycle_output": str(cycle_output),
                    "command": command,
                    "exit_code": None,
                    "log_path": "",
                    "blocker": "",
                }
            )
            _write_summary(
                summary_path,
                _summary_payload(
                    campaign_id=campaign_id,
                    status="planning",
                    batch_dir=batch_dir,
                    output_dir=root,
                    summary_path=summary_path,
                    batch_count=len(batches),
                    completed_count=completed_count,
                    failed_count=failed_count,
                    skipped_count=skipped_count,
                    planned_count=planned_count,
                    generated_at=generated_at,
                    batch_results=batch_results,
                ),
            )
            continue
        processed_pending_count += 1
        exit_code, stdout, stderr = runner(command)
        log_path = root / "logs" / f"{index:03d}_{batch_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            f"COMMAND:\n{command}\n\nEXIT_CODE:\n{exit_code}\n\nSTDOUT:\n{stdout}\n\nSTDERR:\n{stderr}\n",
            encoding="utf-8",
        )
        if stdout:
            cycle_output.write_text(stdout, encoding="utf-8")
        batch_status = "passed" if exit_code == 0 else "failed"
        blocker = "" if exit_code == 0 else f"stage_failed:{batch_id}"
        if exit_code == 0:
            completed_count += 1
        else:
            failed_count += 1
            status = "failed"
        batch_results.append(
            {
                "batch_id": batch_id,
                "batch_path": str(batch),
                "campaign_id": campaign_id,
                "status": batch_status,
                "cycle_output": str(cycle_output),
                "command": command,
                "exit_code": exit_code,
                "log_path": str(log_path),
                "blocker": blocker,
            }
        )
        _write_summary(
            summary_path,
            _summary_payload(
                campaign_id=campaign_id,
                status="failed" if exit_code != 0 else "running",
                batch_dir=batch_dir,
                output_dir=root,
                summary_path=summary_path,
                batch_count=len(batches),
                completed_count=completed_count,
                failed_count=failed_count,
                skipped_count=skipped_count,
                planned_count=planned_count,
                generated_at=generated_at,
                batch_results=batch_results,
            ),
        )
        if exit_code != 0:
            break

    result = _summary_payload(
        campaign_id=campaign_id,
        status=status,
        batch_dir=batch_dir,
        output_dir=root,
        summary_path=summary_path,
        batch_count=len(batches),
        completed_count=completed_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        planned_count=planned_count,
        generated_at=generated_at,
        batch_results=batch_results,
    )
    _write_summary(summary_path, result)
    return result


def _summary_payload(
    *,
    campaign_id: str,
    status: str,
    batch_dir: Path,
    output_dir: Path,
    summary_path: Path,
    batch_count: int,
    completed_count: int,
    failed_count: int,
    skipped_count: int,
    planned_count: int = 0,
    generated_at: str,
    batch_results: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "committee_batch_runner",
        "campaign_id": campaign_id,
        "status": status,
        "committee_batch_dir": str(batch_dir),
        "output_dir": str(output_dir),
        "summary_output": str(summary_path),
        "batch_count": batch_count,
        "completed_count": completed_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "planned_count": planned_count,
        "remaining_count": max(batch_count - completed_count - failed_count - skipped_count - planned_count, 0),
        "order_submission_enabled": False,
        "generated_at": generated_at,
        "batches": batch_results,
    }


def _write_summary(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def build_cycle_command(
    *,
    batch_path: str | Path,
    journal_db: str | Path,
    portfolio_state: str | Path,
    market_regime_file: str | Path | None = None,
    motley_fool_config: str | Path | None = None,
    agent_preset: str = "decision_4",
    profile_config: str | Path | None = None,
) -> str:
    """Build the existing no-submit one-cycle command for a committee batch."""
    command = (
        "python scripts/run_longterm_cycle.py "
        f"--idea-batch {_quote(batch_path)} "
        f"--journal-db {_quote(journal_db)} "
        f"--portfolio-state {_quote(portfolio_state)} "
        f"--agent-preset {agent_preset} --quiet"
        f"{_optional_path_arg('--market-regime-file', market_regime_file)}"
        f"{_optional_path_arg('--motley-fool-config', motley_fool_config)}"
        f"{_optional_path_arg('--profile-config', profile_config)}"
    )
    _validate_command(command)
    return command


def _completed_batches(summary_path: Path) -> set[str]:
    if not summary_path.exists():
        return set()
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(payload, dict):
        return set()
    completed = set()
    for item in payload.get("batches") or []:
        if not isinstance(item, dict):
            continue
        if item.get("status") in {"passed", "skipped_resume"}:
            batch_id = str(item.get("batch_id") or "").strip()
            if batch_id:
                completed.add(batch_id)
    return completed


def _run_command(command: str) -> tuple[int, str, str]:
    completed = subprocess.run(command, shell=True, text=True, capture_output=True)
    return completed.returncode, completed.stdout, completed.stderr


def _validate_command(command: str) -> None:
    normalized = command.lower()
    for fragment in FORBIDDEN_COMMAND_FRAGMENTS:
        if fragment in normalized:
            raise ValueError(f"Unsafe submit command in committee batch runner: {fragment}")


def _quote(value: str | Path) -> str:
    return subprocess.list2cmdline([str(value)])


def _optional_path_arg(flag: str, value: str | Path | None) -> str:
    if not value:
        return ""
    return f" {flag} {_quote(value)}"


__all__ = ["build_cycle_command", "run_committee_batch_dir"]
