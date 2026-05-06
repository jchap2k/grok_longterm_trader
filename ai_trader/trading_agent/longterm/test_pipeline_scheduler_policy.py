import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.decision_journal import LongTermDecisionJournal
from longterm.pipeline_scheduler_policy import (
    PipelineSchedulerPolicyConfig,
    build_pipeline_scheduler_policy_decision,
    build_pipeline_scheduler_policy_state,
)
from longterm.pipeline_scheduler_policy_cli import build_parser, run_cli
from research.research_packet import ResearchPacket


NOW = datetime(2026, 5, 5, 12, 0, tzinfo=timezone.utc)


def _rules(path: Path) -> Path:
    path.write_text("<rules>FXAIX protected</rules>", encoding="utf-8")
    return path


def _fresh_state() -> dict:
    return {
        "last_account_refresh_at": (NOW - timedelta(minutes=5)).isoformat(),
        "last_no_submit_preflight_at": (NOW - timedelta(hours=1)).isoformat(),
        "last_full_research_at": (NOW - timedelta(days=1)).isoformat(),
    }


def test_scheduler_policy_requires_active_rules_file(tmp_path):
    with pytest.raises(ValueError, match="rules_path"):
        build_pipeline_scheduler_policy_decision(
            rules_path=tmp_path / "missing_rules.txt",
            now=NOW,
            policy_state=_fresh_state(),
        )


def test_scheduler_policy_panic_regime_wins_over_stale_refresh(tmp_path):
    decision = build_pipeline_scheduler_policy_decision(
        rules_path=_rules(tmp_path / "active_rules.txt"),
        now=NOW,
        market_regime={"risk_regime": "normal", "vix_level": 35, "ten_year_yield_trend": "rising"},
        policy_state={},
    )

    assert decision["recommended_mode"] == "panic_regime_reassessment"
    assert decision["urgency"] == "high"
    assert decision["order_submission_enabled"] is False
    assert "vix_panic_threshold" in decision["reasons"]


def test_scheduler_policy_uses_review_status_builder_for_broken_thesis(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    packet = ResearchPacket(
        symbol="NVDA",
        company_name="Nvidia",
        idea_source="test",
        business_summary="AI platform.",
        thesis_summary="Durable accelerator demand.",
        review_cadence="monthly",
    )
    decision_id = journal.record_decision(packet, decision={"recommendation": "BUY", "confidence": 80})
    journal.record_thesis_review(
        symbol="NVDA",
        thesis_state="broken",
        decision_id=decision_id,
        evidence=["guidance cut"],
    )

    decision = build_pipeline_scheduler_policy_decision(
        rules_path=_rules(tmp_path / "active_rules.txt"),
        now=NOW,
        journal_db=tmp_path / "journal.db",
        policy_state=_fresh_state(),
    )

    assert decision["recommended_mode"] == "thesis_review_refresh"
    assert decision["review_summary"]["broken_count"] == 1
    assert decision["affected_symbols"] == ["NVDA"]
    assert "review_pressure" in decision["reasons"]


def test_scheduler_policy_excludes_protected_symbol_from_affected_review_symbols(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    packet = ResearchPacket(
        symbol="FXAIX",
        company_name="Fidelity 500 Index",
        idea_source="test",
        business_summary="Protected benchmark.",
        review_cadence="monthly",
    )
    decision_id = journal.record_decision(packet, decision={"recommendation": "BUY", "confidence": 80})
    journal.record_thesis_review(symbol="FXAIX", thesis_state="broken", decision_id=decision_id)

    decision = build_pipeline_scheduler_policy_decision(
        rules_path=_rules(tmp_path / "active_rules.txt"),
        now=NOW,
        journal_db=tmp_path / "journal.db",
        policy_state=_fresh_state(),
    )

    assert decision["affected_symbols"] == []
    assert decision["review_summary"]["protected_excluded_count"] == 1


def test_scheduler_policy_surfaces_benchmark_pause_before_normal_cadence(tmp_path):
    journal = LongTermDecisionJournal(tmp_path / "journal.db")
    for index in range(5):
        packet = ResearchPacket(
            symbol=f"TST{index}",
            company_name=f"Test {index}",
            idea_source="test",
            business_summary="Business.",
        )
        decision_id = journal.record_decision(
            packet,
            decision={"recommendation": "BUY", "confidence": 70},
            candidate_price=100,
            benchmark_price=100,
        )
        journal.update_outcome(decision_id, candidate_price=90, benchmark_price=110)

    decision = build_pipeline_scheduler_policy_decision(
        rules_path=_rules(tmp_path / "active_rules.txt"),
        now=NOW,
        journal_db=tmp_path / "journal.db",
        policy_state=_fresh_state(),
    )

    assert decision["recommended_mode"] == "benchmark_reassessment"
    assert decision["benchmark_guard"]["should_pause_new_buys"] is True
    assert "benchmark_guard_paused" in decision["reasons"]


def test_scheduler_policy_uses_completed_scheduler_summary_for_preflight_freshness(tmp_path):
    scheduler_summary = {
        "runs": [
            {
                "status": "completed",
                "finished_at": (NOW - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
            }
        ]
    }
    decision = build_pipeline_scheduler_policy_decision(
        rules_path=_rules(tmp_path / "active_rules.txt"),
        now=NOW,
        pipeline_scheduler_summary=scheduler_summary,
        policy_state={
            "last_account_refresh_at": (NOW - timedelta(minutes=5)).isoformat(),
            "last_full_research_at": (NOW - timedelta(days=1)).isoformat(),
        },
    )

    assert decision["recommended_mode"] == "account_refresh_only"
    assert "dashboard_freshness_floor" in decision["reasons"]


def test_scheduler_policy_infers_account_refresh_freshness_from_scheduler_summary(tmp_path):
    scheduler_summary = {
        "runs": [
            {
                "status": "completed",
                "finished_at": (NOW - timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
                "account_refresh_exit_code": 0,
            }
        ]
    }

    decision = build_pipeline_scheduler_policy_decision(
        rules_path=_rules(tmp_path / "active_rules.txt"),
        now=NOW,
        pipeline_scheduler_summary=scheduler_summary,
        policy_state={
            "last_no_submit_preflight_at": (NOW - timedelta(minutes=10)).isoformat(),
            "last_full_research_at": (NOW - timedelta(days=1)).isoformat(),
        },
    )

    assert decision["recommended_mode"] == "account_refresh_only"
    assert "dashboard_freshness_floor" in decision["reasons"]


def test_scheduler_policy_blocks_unbounded_paid_resource_controls(tmp_path):
    scheduler_summary = {
        "runs": [
            {
                "status": "planned",
                "finished_at": (NOW - timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
                "resource_controls": {
                    "provider_mode": "perplexity",
                    "paid_provider_enabled": True,
                    "research_max_pass_count": None,
                    "bounded": False,
                    "bounded_reason": "missing_research_max_pass_count",
                },
            }
        ]
    }

    decision = build_pipeline_scheduler_policy_decision(
        rules_path=_rules(tmp_path / "active_rules.txt"),
        now=NOW,
        pipeline_scheduler_summary=scheduler_summary,
        policy_state=_fresh_state(),
    )

    assert decision["recommended_mode"] == "resource_control_review"
    assert decision["urgency"] == "high"
    assert "scheduler_resource_controls_unbounded" in decision["blockers"]
    assert "paid_research_provider_planned" in decision["warnings"]
    assert decision["resource_controls"]["provider_mode"] == "perplexity"
    assert decision["next_safe_action"] == "review_scheduler_resource_controls_before_running_paid_work"


def test_scheduler_policy_warns_for_bounded_paid_resource_controls(tmp_path):
    scheduler_summary = {
        "runs": [
            {
                "status": "planned",
                "finished_at": (NOW - timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
                "resource_controls": {
                    "provider_mode": "perplexity",
                    "paid_provider_enabled": True,
                    "research_max_pass_count": 25,
                    "bounded": True,
                    "bounded_reason": "explicit_caps_present",
                },
            }
        ]
    }

    decision = build_pipeline_scheduler_policy_decision(
        rules_path=_rules(tmp_path / "active_rules.txt"),
        now=NOW,
        pipeline_scheduler_summary=scheduler_summary,
        policy_state=_fresh_state(),
    )

    assert decision["recommended_mode"] == "account_refresh_only"
    assert "paid_research_provider_planned" in decision["warnings"]
    assert "scheduler_resource_controls_unbounded" not in decision["blockers"]
    assert decision["resource_controls"]["research_max_pass_count"] == 25


def test_scheduler_policy_ignores_failed_account_refresh_when_inferring_freshness(tmp_path):
    scheduler_summary = {
        "runs": [
            {
                "status": "failed",
                "finished_at": (NOW - timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
                "account_refresh_exit_code": 1,
            }
        ]
    }

    decision = build_pipeline_scheduler_policy_decision(
        rules_path=_rules(tmp_path / "active_rules.txt"),
        now=NOW,
        pipeline_scheduler_summary=scheduler_summary,
        policy_state={
            "last_no_submit_preflight_at": (NOW - timedelta(minutes=10)).isoformat(),
            "last_full_research_at": (NOW - timedelta(days=1)).isoformat(),
        },
    )

    assert decision["recommended_mode"] == "account_refresh_only"
    assert "account_refresh_stale" in decision["reasons"]


def test_scheduler_policy_warns_when_active_rules_hash_changed(tmp_path):
    rules = _rules(tmp_path / "active_rules.txt")
    decision = build_pipeline_scheduler_policy_decision(
        rules_path=rules,
        now=NOW,
        policy_state={**_fresh_state(), "active_rules_sha256": "old-hash"},
    )

    assert "active_rules_changed" in decision["warnings"]
    assert decision["active_rules_sha256"] != "old-hash"
    assert decision["cadence_recommendations"]["final_planning_due"] is True
    assert "active_rules_changed" in decision["cadence_recommendations"]["final_planning_reasons"]


def test_scheduler_policy_cli_writes_json_report(tmp_path, capsys):
    rules = _rules(tmp_path / "active_rules.txt")
    report = tmp_path / "policy.json"
    state = tmp_path / "state.json"
    state.write_text(json.dumps(_fresh_state()), encoding="utf-8")

    code = run_cli(
        build_parser().parse_args(
            [
                "--rules-path",
                str(rules),
                "--policy-state",
                str(state),
                "--now",
                NOW.isoformat(),
                "--report-output",
                str(report),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(report.read_text(encoding="utf-8"))
    assert code == 0
    assert printed["order_submission_enabled"] is False
    assert saved["recommended_mode"] == "account_refresh_only"
    assert "--submit-paper-orders" not in json.dumps(saved)


def test_scheduler_policy_cli_treats_missing_optional_state_artifacts_as_empty(tmp_path, capsys):
    rules = _rules(tmp_path / "active_rules.txt")
    report = tmp_path / "policy.json"
    state_output = tmp_path / "state_next.json"

    code = run_cli(
        build_parser().parse_args(
            [
                "--rules-path",
                str(rules),
                "--policy-state",
                str(tmp_path / "missing_policy_state.json"),
                "--pipeline-scheduler-summary",
                str(tmp_path / "missing_scheduler_summary.json"),
                "--pipeline-summary",
                str(tmp_path / "missing_pipeline_summary.json"),
                "--now",
                NOW.isoformat(),
                "--report-output",
                str(report),
                "--state-output",
                str(state_output),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    state = json.loads(state_output.read_text(encoding="utf-8"))
    assert code == 0
    assert printed["recommended_mode"] == "account_refresh_only"
    assert "account_refresh_stale" in printed["reasons"]
    assert state["active_rules_sha256"] == printed["active_rules_sha256"]


def test_scheduler_policy_state_persists_scheduler_history_and_full_research_marker(tmp_path):
    committee_summary = tmp_path / "committee_batch_run_summary.json"
    committee_summary.write_text(
        json.dumps(
            {
                "status": "completed",
                "batch_count": 2,
                "completed_count": 2,
                "failed_count": 0,
                "skipped_count": 0,
                "remaining_count": 0,
            }
        ),
        encoding="utf-8",
    )
    scheduler_summary = {
        "runs": [
            {
                "status": "completed",
                "finished_at": (NOW - timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
                "account_refresh_exit_code": 0,
            }
        ]
    }
    pipeline_summary = {
        "status": "completed",
        "stages": [
            {
                "stage_id": "generated_committee_batches",
                "status": "passed",
                "artifact_paths": {"generated_committee_batch_run_summary": str(committee_summary)},
            },
            {"stage_id": "final_planning_refresh", "status": "passed"},
            {"stage_id": "extract_final_action_plan", "status": "passed"},
        ],
    }
    decision = build_pipeline_scheduler_policy_decision(
        rules_path=_rules(tmp_path / "active_rules.txt"),
        now=NOW,
        policy_state={"custom_note": "keep"},
        pipeline_scheduler_summary=scheduler_summary,
    )

    state = build_pipeline_scheduler_policy_state(
        decision,
        previous_state={"custom_note": "keep"},
        pipeline_scheduler_summary=scheduler_summary,
        pipeline_summary=pipeline_summary,
    )

    assert state["custom_note"] == "keep"
    assert state["active_rules_sha256"] == decision["active_rules_sha256"]
    assert state["last_account_refresh_at"] == (NOW - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    assert state["last_no_submit_preflight_at"] == (NOW - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    assert state["last_full_research_at"] == decision["generated_at"]
    assert state["last_final_planning_at"] == decision["generated_at"]


def test_scheduler_policy_marks_final_planning_due_after_full_research_without_planning(tmp_path):
    decision = build_pipeline_scheduler_policy_decision(
        rules_path=_rules(tmp_path / "active_rules.txt"),
        now=NOW,
        policy_state={
            **_fresh_state(),
            "last_full_research_at": (NOW - timedelta(minutes=5)).isoformat(),
            "last_final_planning_at": (NOW - timedelta(hours=1)).isoformat(),
        },
    )

    cadence = decision["cadence_recommendations"]
    assert cadence["final_planning_due"] is True
    assert "final_planning_older_than_full_research" in cadence["final_planning_reasons"]
    assert decision["recommended_mode"] == "final_planning_refresh"
    assert decision["next_safe_action"] == "run_final_planning_refresh_no_submit"


def test_scheduler_policy_state_does_not_mark_final_planning_for_timeout_or_missing_extract(tmp_path):
    decision = build_pipeline_scheduler_policy_decision(
        rules_path=_rules(tmp_path / "active_rules.txt"),
        now=NOW,
        policy_state=_fresh_state(),
    )

    state = build_pipeline_scheduler_policy_state(
        decision,
        pipeline_summary={
            "status": "failed",
            "blocker_count": 1,
            "stages": [
                {
                    "stage_id": "final_planning_refresh",
                    "status": "failed",
                    "blocker": "stage_timeout:final_planning_refresh",
                },
            ],
        },
    )

    assert "last_final_planning_at" not in state


def test_scheduler_policy_state_does_not_mark_full_research_for_partial_generated_committee_run(tmp_path):
    committee_summary = tmp_path / "committee_batch_run_summary.json"
    committee_summary.write_text(
        json.dumps(
            {
                "status": "partial",
                "batch_count": 10,
                "completed_count": 1,
                "failed_count": 0,
                "skipped_count": 0,
                "remaining_count": 9,
            }
        ),
        encoding="utf-8",
    )
    decision = build_pipeline_scheduler_policy_decision(
        rules_path=_rules(tmp_path / "active_rules.txt"),
        now=NOW,
    )

    state = build_pipeline_scheduler_policy_state(
        decision,
        pipeline_summary={
            "status": "completed",
            "stages": [
                {
                    "stage_id": "generated_committee_batches",
                    "status": "passed",
                    "artifact_paths": {"generated_committee_batch_run_summary": str(committee_summary)},
                }
            ],
        },
    )

    assert "last_full_research_at" not in state


def test_scheduler_policy_state_does_not_mark_full_research_for_preflight_only_pipeline(tmp_path):
    scheduler_summary = {
        "runs": [
            {
                "status": "completed",
                "finished_at": (NOW - timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
                "account_refresh_exit_code": 0,
            }
        ]
    }
    decision = build_pipeline_scheduler_policy_decision(
        rules_path=_rules(tmp_path / "active_rules.txt"),
        now=NOW,
        pipeline_scheduler_summary=scheduler_summary,
    )

    state = build_pipeline_scheduler_policy_state(
        decision,
        pipeline_scheduler_summary=scheduler_summary,
        pipeline_summary={
            "status": "completed",
            "stages": [{"stage_id": "paper_preview", "status": "passed"}],
        },
    )

    assert "last_full_research_at" not in state


def test_scheduler_policy_cli_writes_state_output_with_current_pipeline_summary(tmp_path, capsys):
    rules = _rules(tmp_path / "active_rules.txt")
    report = tmp_path / "policy.json"
    state_output = tmp_path / "state_next.json"
    scheduler_summary = tmp_path / "scheduler_summary.json"
    pipeline_summary = tmp_path / "pipeline_summary.json"
    scheduler_summary.write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "status": "completed",
                        "finished_at": (NOW - timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
                        "account_refresh_exit_code": 0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    pipeline_summary.write_text(
        json.dumps(
            {
                "status": "completed",
                "stages": [{"stage_id": "committee_batch_001", "status": "passed"}],
            }
        ),
        encoding="utf-8",
    )

    code = run_cli(
        build_parser().parse_args(
            [
                "--rules-path",
                str(rules),
                "--now",
                NOW.isoformat(),
                "--pipeline-scheduler-summary",
                str(scheduler_summary),
                "--pipeline-summary",
                str(pipeline_summary),
                "--report-output",
                str(report),
                "--state-output",
                str(state_output),
                "--json",
            ]
        )
    )

    printed = json.loads(capsys.readouterr().out)
    state = json.loads(state_output.read_text(encoding="utf-8"))
    assert code == 0
    assert printed["state_output"] == str(state_output)
    assert state["last_full_research_at"] == printed["generated_at"]
