"""CLI helpers for inspecting and updating the long-term decision journal."""

from __future__ import annotations

import argparse
import json

from longterm.decision_journal import LongTermDecisionJournal
from longterm.paper_preview_status import PaperPreviewStatusBuilder
from longterm.paper_trade_ledger import PaperTradeLedger
from longterm.report_builder import build_markdown_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect long-term decision journal.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    summary = subparsers.add_parser("summary", help="Summarize benchmark outcomes.")
    summary.add_argument("--journal-db", default=None)

    list_cmd = subparsers.add_parser("list", help="List recent decisions.")
    list_cmd.add_argument("--journal-db", default=None)
    list_cmd.add_argument("--limit", type=int, default=20)

    report = subparsers.add_parser("report", help="Render a markdown decision report.")
    report.add_argument("--journal-db", default=None)
    report.add_argument("--limit", type=int, default=20)
    report.add_argument("--record-rank-snapshot", action="store_true")
    report.add_argument("--paper-ledger-db", default=None)

    deferred_list = subparsers.add_parser("deferred-list", help="List deferred research items.")
    deferred_list.add_argument("--journal-db", default=None)
    deferred_list.add_argument("--limit", type=int, default=20)
    deferred_list.add_argument("--include-resolved", action="store_true")

    deferred_resolve = subparsers.add_parser("deferred-resolve", help="Resolve a deferred research item.")
    deferred_resolve.add_argument("--journal-db", default=None)
    deferred_resolve.add_argument("--deferred-id", required=True)
    deferred_resolve.add_argument("--notes", default="")

    thesis_review_record = subparsers.add_parser(
        "thesis-review-record", help="Record a thesis review event."
    )
    thesis_review_record.add_argument("--journal-db", default=None)
    thesis_review_record.add_argument("--symbol", required=True)
    thesis_review_record.add_argument("--thesis-state", required=True)
    thesis_review_record.add_argument("--status", default="reviewed")
    thesis_review_record.add_argument("--notes", default="")
    thesis_review_record.add_argument("--evidence", action="append", default=[])
    thesis_review_record.add_argument("--decision-id", default=None)
    thesis_review_record.add_argument("--trade-id", default=None)
    thesis_review_record.add_argument("--review-trigger", default="manual")
    thesis_review_record.add_argument("--current-market-value", type=float, default=None)

    thesis_review_list = subparsers.add_parser(
        "thesis-review-list", help="List thesis review events."
    )
    thesis_review_list.add_argument("--journal-db", default=None)
    thesis_review_list.add_argument("--limit", type=int, default=20)

    feedback_rebuild = subparsers.add_parser(
        "symbol-feedback-rebuild", help="Rebuild durable symbol feedback profiles."
    )
    feedback_rebuild.add_argument("--journal-db", default=None)

    feedback_show = subparsers.add_parser(
        "symbol-feedback-show", help="Show one durable symbol feedback profile."
    )
    feedback_show.add_argument("--journal-db", default=None)
    feedback_show.add_argument("--symbol", required=True)

    feedback_apply_preview = subparsers.add_parser(
        "symbol-feedback-apply-paper-preview",
        help="Apply paper preview status feedback to symbol profiles.",
    )
    feedback_apply_preview.add_argument("--journal-db", default=None)
    feedback_apply_preview.add_argument("--paper-ledger-db", required=True)

    update = subparsers.add_parser("update-outcome", help="Update active-vs-benchmark outcome.")
    update.add_argument("--journal-db", default=None)
    update.add_argument("--decision-id", required=True)
    update.add_argument("--candidate-price", type=float, required=True)
    update.add_argument("--benchmark-price", type=float, required=True)
    update.add_argument("--notes", default="")

    # Thesis Re-underwriting (Phase 1+)
    reunderwrite_list = subparsers.add_parser(
        "reunderwrite-list", help="List recent re-underwriting events and durability status."
    )
    reunderwrite_list.add_argument("--journal-db", default=None)
    reunderwrite_list.add_argument("--limit", type=int, default=30)
    reunderwrite_list.add_argument("--symbol", default=None)

    reunderwrite_run = subparsers.add_parser(
        "reunderwrite-run",
        help="Run (or dry-run) thesis re-underwriting for a symbol or decision. Uses delta evidence when possible.",
    )
    reunderwrite_run.add_argument("--journal-db", default=None)
    reunderwrite_run.add_argument("--symbol", default=None, help="Symbol to re-underwrite (latest decision used)")
    reunderwrite_run.add_argument("--decision-id", default=None, help="Specific parent decision_id to re-underwrite")
    reunderwrite_run.add_argument("--dry-run", action="store_true", help="Show what would be recorded without writing")
    reunderwrite_run.add_argument("--force", action="store_true", help="Force re-underwrite even if not due")
    reunderwrite_run.add_argument("--thesis-durability", default=None, choices=["strong", "stable", "weakening", "broken"], help="Manual override for durability (requires --dry-run or --force)")
    reunderwrite_run.add_argument("--notes", default="", help="Operator notes for this re-underwriting")
    reunderwrite_run.add_argument("--use-kronos", action="store_true", help="Include optional Kronos signal (default: off for MVP)")
    reunderwrite_run.add_argument("--all-holdings", action="store_true", help="Re-underwrite all current BUY/ADD/HOLD positions (portfolio mode)")
    reunderwrite_run.add_argument("--max-symbols", type=int, default=20, help="Limit for --all-holdings mode")

    return parser


def run_cli(args: argparse.Namespace) -> int:
    journal = LongTermDecisionJournal(args.journal_db)

    if args.command == "summary":
        print(json.dumps(journal.summarize_benchmark_performance(), indent=2, sort_keys=True))
        return 0

    if args.command == "list":
        print(json.dumps(journal.list_recent_decisions(limit=args.limit), indent=2, sort_keys=True))
        return 0

    if args.command == "report":
        paper_status = (
            PaperPreviewStatusBuilder(PaperTradeLedger(args.paper_ledger_db)).build()
            if args.paper_ledger_db
            else None
        )
        print(
            build_markdown_report(
                journal,
                limit=args.limit,
                paper_preview_status_by_decision=paper_status.by_decision_id if paper_status else None,
                paper_preview_status_by_symbol=paper_status.by_symbol if paper_status else None,
            ),
            end="",
        )
        if args.record_rank_snapshot:
            snapshot_id = journal.record_recommendation_rank_snapshot(
                journal.list_recommendation_table(limit=args.limit)
            )
            print(f"\nrecorded rank snapshot {snapshot_id}")
        return 0

    if args.command == "deferred-list":
        print(
            json.dumps(
                journal.list_deferred_research_items(
                    limit=args.limit,
                    include_resolved=args.include_resolved,
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if args.command == "deferred-resolve":
        journal.resolve_deferred_research_item(args.deferred_id, notes=args.notes)
        print(f"resolved {args.deferred_id}")
        return 0

    if args.command == "thesis-review-record":
        review_id = journal.record_thesis_review(
            symbol=args.symbol,
            thesis_state=args.thesis_state,
            status=args.status,
            review_notes=args.notes,
            evidence=args.evidence,
            decision_id=args.decision_id,
            trade_id=args.trade_id,
            review_trigger=args.review_trigger,
            current_market_value=args.current_market_value,
        )
        print(f"recorded thesis review {review_id}")
        return 0

    if args.command == "thesis-review-list":
        print(json.dumps(journal.list_thesis_reviews(limit=args.limit), indent=2, sort_keys=True))
        return 0

    if args.command == "symbol-feedback-rebuild":
        print(json.dumps(journal.rebuild_symbol_feedback_profiles(), indent=2, sort_keys=True))
        return 0

    if args.command == "symbol-feedback-show":
        profile = journal.get_symbol_feedback_profile(args.symbol)
        print(json.dumps(profile or {}, indent=2, sort_keys=True))
        return 0

    if args.command == "symbol-feedback-apply-paper-preview":
        status = PaperPreviewStatusBuilder(PaperTradeLedger(args.paper_ledger_db)).build()
        print(
            json.dumps(
                journal.apply_paper_preview_feedback(status.by_symbol),
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if args.command == "update-outcome":
        journal.update_outcome(
            args.decision_id,
            candidate_price=args.candidate_price,
            benchmark_price=args.benchmark_price,
            notes=args.notes,
        )
        print(f"updated {args.decision_id}")
        return 0

    # --- Thesis Re-underwriting commands (MVP) ---
    if args.command == "reunderwrite-list":
        latest = journal.latest_reunderwriting_by_symbol()
        if args.symbol:
            sym = args.symbol.upper()
            if sym in latest:
                print(json.dumps(latest[sym], indent=2, sort_keys=True))
            else:
                print(f"No re-underwriting found for {sym}")
        else:
            items = list(latest.values())[: args.limit]
            print(json.dumps(items, indent=2, sort_keys=True))
        return 0

    if args.command == "reunderwrite-run":
        from longterm.reunderwriting_engine import run_reunderwriting  # lazy import

        if args.all_holdings:
            # Portfolio mode: re-underwrite recent actionable holdings
            recent = journal.list_recent_decisions(limit=100)
            seen: set[str] = set()
            results = []
            for row in recent:
                sym = str(row.get("symbol") or "").upper()
                rec = str(row.get("recommendation") or "").upper()
                if sym and rec in {"BUY", "ADD", "HOLD"} and sym not in seen:
                    seen.add(sym)
                    if len(results) >= args.max_symbols:
                        break
                    res = run_reunderwriting(
                        journal,
                        symbol=sym,
                        dry_run=args.dry_run or True,  # default to safe dry-run in portfolio mode
                        force=args.force,
                        manual_durability=args.thesis_durability,
                        notes=args.notes or "Portfolio re-underwrite sweep",
                        use_kronos=args.use_kronos,
                    )
                    results.append({"symbol": sym, "result": res})
            print(json.dumps({"portfolio_sweep": True, "processed": len(results), "results": results}, indent=2, sort_keys=True))
            return 0

        result = run_reunderwriting(
            journal,
            symbol=args.symbol,
            decision_id=args.decision_id,
            dry_run=args.dry_run,
            force=args.force,
            manual_durability=args.thesis_durability,
            notes=args.notes,
            use_kronos=args.use_kronos,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    raise ValueError(f"Unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run_cli(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
