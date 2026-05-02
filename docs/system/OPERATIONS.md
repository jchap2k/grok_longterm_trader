# Long-Term Trader Operations

Run commands from `ai_trader/trading_agent`.

## Dry-Run A Ticker

```powershell
python scripts/run_longterm_research.py --symbol AAPL --company-name Apple --thesis "Services and ecosystem durability." --business-summary "Consumer technology platform." --dry-run
```

## Run Research

```powershell
python scripts/run_longterm_research.py --symbol AAPL --company-name Apple --thesis "Services and ecosystem durability." --business-summary "Consumer technology platform." --candidate-price 180 --benchmark-price 165
```

Use the expanded committee when a decision is high-value or borderline:

```powershell
python scripts/run_longterm_research.py --symbol AAPL --agent-preset decision_6
```

## Journal Commands

```powershell
python scripts/longterm_journal.py summary
python scripts/longterm_journal.py list --limit 10
python scripts/longterm_journal.py report --limit 10
python scripts/longterm_journal.py update-outcome --decision-id <id> --candidate-price 190 --benchmark-price 170 --notes "monthly review"
```

The recommendation table is derived from `DecisionJournal` rows through `RecommendationTableBuilder`. Volatile market/fundamental fields should be enriched at report time and cached daily; do not write transient enrichment directly into the journal unless it becomes part of a durable decision record. Markdown reports include shortened decision IDs so every recommendation row can be traced back to the durable journal entry.

Repeat recommendations are intentional signal, similar to Motley Fool-style
recommendation counts. The current recommendation row keeps the latest thesis
while incrementing `Times Rec'd`. If a repeat packet includes source notes marked
`New information: ...` or the thesis changed versus prior recommendations, the
report surfaces those notes in `New Info` so the stock profile can be enriched
before future research or paper-action decisions.

Durable symbol feedback profiles can be rebuilt from the journal and inspected
directly:

```powershell
python scripts/longterm_journal.py symbol-feedback-rebuild --journal-db path\to\journal.db
python scripts/longterm_journal.py symbol-feedback-show --journal-db path\to\journal.db --symbol NVDA
python scripts/longterm_journal.py symbol-feedback-apply-paper-preview --journal-db path\to\journal.db --paper-ledger-db path\to\paper_ledger.db
```

These profiles are research memory only. They track repeat recommendation count,
latest thesis, thesis-history changes, new-information notes, and paper-preview
readiness/blocker feedback. The one-cycle orchestration can inject that context
into future same-symbol research packets, but it does not change ranking weights,
sizing, paper preview eligibility, or broker behavior.

Review status is layered onto the same table with `ReviewStatusBuilder`. It reads stored packet JSON from the journal, applies the configured review cadence through `ThesisMonitor`, and returns `review_due`, `days_since_review`, and `thesis_state` fields for reports and next-action markdown.

Record a completed thesis review after checking earnings, business evidence, or portfolio context:

```powershell
python scripts/longterm_journal.py thesis-review-record --journal-db path\to\journal.db --symbol AAPL --thesis-state healthy --status reviewed --notes "Services thesis remains intact." --evidence "Services revenue still growing." --review-trigger manual --current-market-value 4200
```

List recent thesis reviews:

```powershell
python scripts/longterm_journal.py thesis-review-list --journal-db path\to\journal.db --limit 20
```

Recorded thesis reviews are audit events. They do not place orders, but they do feed future review status. A newer CGH decision supersedes an older review; otherwise, a recorded `broken` or `weakening` review remains visible in reports and next-actions until newer evidence or a newer decision changes the thesis.

Generate an operator review checklist from code by using `ReviewTemplateBuilder`
inside a supervised workflow. The checklist is anchored to `active_rules.txt`
and asks for business momentum, quality durability, valuation discipline, thesis
breakers, evidence to collect, and the operator decision.

## Discovery Queue

Discovery builds the stock universe that deserves research. It does not create
orders, recommendations, or account action plans. Good V1 source inputs include
S&P 500 / Russell lists, Nasdaq/NYSE listings filtered for quality, major ETF
holdings, quality/dividend/moat lists, manual watchlists, and Motley Fool
premium captures.

Run discovery from a candidate JSON file:

```powershell
python scripts/run_longterm_discovery.py --candidates path\to\candidates.json
```

Run discovery directly from a local universe source file:

```powershell
python scripts/run_longterm_discovery.py --source-file path\to\sp500.csv --source sp500
python scripts/run_longterm_discovery.py --source-file path\to\qqq_holdings.csv --source qqq
python scripts/run_longterm_discovery.py --source-file path\to\nasdaqlisted.txt --source nasdaq_listed
```

Optionally enrich those source rows from a local JSON/CSV metrics cache before
scoring:

```powershell
python scripts/run_longterm_discovery.py --source-file path\to\sp500.csv --source sp500 --enrichment-file path\to\fundamentals.json --enrichment-source fundamentals_cache
```

Research-packet enrichment is a second, pre-LLM readiness layer. It keeps
provider metrics transient, adds source notes, and reports
`completeness_score`, `completeness_bucket`, and `missing_fields` before ideas
consume committee calls. Enriched dictionaries remain compatible with
`ResearchPacket` intake; unknown transient keys are ignored by packet creation.

For wider-universe names where Motley Fool company pages are unavailable or too
thin, use Grok catalyst enrichment as a source-backed synthesis layer. Feed
cheap factual snapshots first, such as Finnhub profile/metric JSON, and let Grok
produce deeper catalyst, bull/bear, earnings, and thesis-watch context. Generated
ratings must remain labeled as `model_estimate`; they are research context, not
Motley Fool proprietary scores and not execution authority.

Before Grok synthesis, enrich the idea batch with high-signal ticker news. The
news pass filters duplicate URLs, generic price-action headlines, and peer-only
mentions where the target ticker is not a primary article subject. It then
keeps only thesis-relevant articles with catalyst categories such as earnings,
product/technology, contracts, regulatory events, M&A, or management changes.
Lower article counts are acceptable and often preferable; thin primary coverage
should surface as lower confidence rather than being padded with noisy articles.

Also enrich the batch with Python-computed fundamental metric tables before
Grok synthesis. This closes part of the gap with Fool company pages without
asking Grok to invent numbers. Snapshot mode accepts symbol-keyed raw
fundamentals from any provider; yfinance mode is a free fallback for non-Fool
tickers.

```powershell
python scripts/longterm_fundamental_metrics_enrichment.py --idea-batch path\to\research_ideas.json --snapshot-file path\to\fundamentals_raw.json --output path\to\research_ideas.fundamentals_enriched.json
python scripts/longterm_fundamental_metrics_enrichment.py --idea-batch path\to\research_ideas.json --provider yfinance --output path\to\research_ideas.fundamentals_enriched.json --limit 5
```

After fundamentals and relevant news are attached, add the deterministic
quality-growth scorecard. This is a non-Fool, auditable Python scorecard that
summarizes quality, growth, valuation, safety, market attention, composite
superscore, investing type, and rough drawdown band.

```powershell
python scripts/longterm_quality_growth_scorecard.py --idea-batch path\to\research_ideas.news_enriched.json --output path\to\research_ideas.scorecard_enriched.json
```

Then add latest-earnings context from the same relevant-news and fundamentals
payload. This creates a structured recent-earnings section with key financial
takeaways, thesis-positive developments, thesis-negative developments, source
URLs, confidence, and warnings.

```powershell
python scripts/longterm_latest_earnings_enrichment.py --idea-batch path\to\research_ideas.scorecard_enriched.json --output path\to\research_ideas.earnings_enriched.json
```

When the enriched idea batch is loaded into the research cycle,
`research/research_evidence_brief.py` automatically compresses those transient
enrichment fields into a first-class `evidence_brief` on each `ResearchPacket`.
That brief is the handoff to the research committee: enrichment assembles
evidence, the brief summarizes it, and the CGH committee judges the thesis under
`active_rules.txt`. The brief is not a trade signal and does not affect paper
preview or execution eligibility.

Offline/snapshot mode for development:

```powershell
python scripts/longterm_news_relevance_enrichment.py --idea-batch path\to\research_ideas.fundamentals_enriched.json --snapshot-file path\to\raw_news.json --output path\to\research_ideas.news_enriched.json
```

Live Polygon mode, when `POLYGON_API_KEY` is configured:

```powershell
python scripts/longterm_news_relevance_enrichment.py --idea-batch path\to\research_ideas.fundamentals_enriched.json --cache-path path\to\polygon_news_cache.json --published-after 2026-04-01 --output path\to\research_ideas.news_enriched.json --rate-limit-batch-size 5 --rate-limit-pause-seconds 66
```

For broad universe work, prefer overnight batches over paid speed upgrades.
Polygon's free-tier cadence is acceptable when requests are paced in groups of
five with a little more than one minute of pause, cached, and resumable; a
20-minute enrichment job is fine for long-term research if it avoids unnecessary
recurring spend.

If Polygon's free tier is too restrictive or its structured feed is thin for
long-tail names, a Perplexity-style answer API can be added later as another
`NewsProvider`. It should return article candidates with title, URL, date,
source, and snippet, then let this same deterministic relevance scorer filter
noise before Grok synthesis.

Offline/snapshot mode for development:

```powershell
python scripts/longterm_grok_research_enrichment.py --idea-batch path\to\research_ideas.earnings_enriched.json --facts-file path\to\finnhub_facts.json --snapshot-file path\to\grok_snapshots.json --output path\to\research_ideas.grok_enriched.json
```

Live xAI mode, when `XAI_API_KEY` is configured:

```powershell
python scripts/longterm_grok_research_enrichment.py --idea-batch path\to\research_ideas.earnings_enriched.json --facts-file path\to\finnhub_facts.json --output path\to\research_ideas.grok_enriched.json --limit 5
```

When `relevant_news` is present, Grok enrichment should produce
`article_evidence_summaries` for the strongest primary-company articles. These
summaries are snippet-grounded: they summarize only the article title, provider
summary/snippet, source, date, URL, relevance, and impact category already in
the enrichment payload. They are useful for the research committee, but they are
not proof that the full article page was opened or read.

After the committee produces first-pass `BUY` / `ADD` rows, run them through the
buy-promotion review gate before treating them as account-planning candidates.
The gate checks protected symbols, whether the symbol is already held,
confidence, positive suggested size, valuation context, and whether the packet
has a versioned evidence brief with article-level support. Promotion output is
operator-facing only: `ACTIONABLE_BUY` means "ready for the next dry-run planning
stage," not "submit an order." Weak or thin-evidence names remain in watchlist or
existing-position review states until more evidence is collected.

Render the current promotion report from a journal and portfolio snapshot:

```powershell
python scripts/longterm_buy_promotion.py --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --output path\to\buy_promotion.md
python scripts/longterm_buy_promotion.py --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --json --output path\to\buy_promotion.json
```

Account-action plans and next-actions also consult the promotion review. A
first-pass `BUY` that is missing article evidence, has low confidence, or carries
an enrichment warning becomes a review/enrichment task with `order_intent=NONE`
instead of a dry-run buy. It is also excluded from rebalance targets until it
clears promotion. This keeps the sequence explicit:
research committee says "interesting buy" -> promotion gate says "actionable
enough" -> account planning sizes the candidate -> Stage 6B eligibility
revalidates again before any supervised paper submission.

When a research cycle or scheduler run has both a journal and portfolio state,
the result JSON also includes `buy_promotion_markdown` and
`buy_promotion_generated`. This makes promotion decisions visible in the same
operator artifact stream as the recommendation report, next-actions report, and
account action plan.

In these docs, `operator` means the control surface that is supervised by us
today and consumed by the autonomous long-term agent later. Operator artifacts
should therefore be both human-readable and machine-readable. They are evidence
and planning context, not trade authorization.

Write research-ready candidates as an idea batch for the existing research
cycle:

```powershell
python scripts/run_longterm_discovery.py --candidates path\to\candidates.json --research-ideas-output path\to\research_ideas.json
python scripts/run_longterm_cycle.py --idea-batch path\to\research_ideas.json --journal-db path\to\journal.db
```

For a broad universe, split the research-ready file into smaller batches before
running the LLM committee. This keeps research work reviewable and prevents one
oversized cycle from spending effort on too many names at once:

```powershell
python scripts/longterm_research_universe.py --research-ideas path\to\research_ideas.json --batch-size 5 --output-dir path\to\research_batches
python scripts/run_longterm_cycle.py --idea-batch path\to\research_batches\research-batch-001.json --journal-db path\to\journal.db
```

Track a multi-batch research campaign so broad-universe work survives across
runs:

```powershell
python scripts/longterm_research_campaign.py init --batch-dir path\to\research_batches --manifest-output path\to\research_campaign.json
python scripts/longterm_research_campaign.py next --manifest path\to\research_campaign.json --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json
python scripts/longterm_research_campaign.py mark --manifest path\to\research_campaign.json --batch-id research-batch-001 --status completed --notes "Processed and journaled."
python scripts/longterm_research_campaign.py summary --manifest path\to\research_campaign.json
```

The campaign command does not run research automatically. It tracks operator
progress and prints the supervised `run_longterm_cycle.py --idea-batch ...`
command for the next pending batch.

Or let a cycle build the discovery queue directly before research:

```powershell
python scripts/run_longterm_cycle.py --discovery-candidates path\to\candidates.json --journal-db path\to\journal.db
python scripts/run_longterm_cycle.py --discovery-source-file path\to\sp500.csv --discovery-source sp500 --journal-db path\to\journal.db
python scripts/run_longterm_cycle.py --discovery-source-file path\to\sp500.csv --discovery-source sp500 --discovery-enrichment-file path\to\fundamentals.json --discovery-enrichment-source fundamentals_cache --journal-db path\to\journal.db
```

Discovery buckets:
- `research_queue`: candidates that clear the mechanical quality-growth pre-filter.
- `watchlist`: interesting candidates that need more evidence before research.
- `rejected`: candidates failing basic liquidity, growth, leverage, or trend checks.

Before any idea reaches the LLM research runner, the cycle applies a minimum
`ResearchPacket` completeness gate. Each packet needs a company name, idea
source, and research context from `business_summary`, `thesis_summary`, or
`source_notes`. Thin ticker stubs are skipped, counted in `skipped_idea_count`,
and listed in `skipped_ideas` instead of consuming research calls.
The richer `deferred_research_queue` lists the missing fields and includes a
suggested `run_longterm_discovery.py --enrichment-file ...` command so those
ideas can be enriched before a later research run. When `--journal-db` is
provided, those deferred rows are also stored in the decision journal.

List open deferred research/enrichment tasks:

```powershell
python scripts/longterm_journal.py deferred-list --journal-db path\to\journal.db
```

After enriching/retrying an item, mark it resolved:

```powershell
python scripts/longterm_journal.py deferred-resolve --journal-db path\to\journal.db --deferred-id <id> --notes "Enriched from fundamentals cache."
```

Standalone next-actions reports also include open persisted deferred rows:

```powershell
python scripts/longterm_next_actions.py --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --limit 10
```

Hydrate next-actions with recorded paper preview status:

```powershell
python scripts/longterm_next_actions.py --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --paper-ledger-db path\to\paper_ledger.db
```

## Dry-Run Action Plan

```powershell
python scripts/longterm_action_plan.py --symbol NVDA --portfolio-state path\to\portfolio.json --decision-file path\to\decision.json
```

Dry-run account action plans include a machine-readable `risk_review` on each
intent. This is not broker execution; it is the local risk panel that checks
protected symbols, benchmark-gate pauses, stale or weakening thesis status,
oversized suggested positions, and active-sleeve cash warnings.

The research runner also sends a deterministic thesis challenge into the CGH
decision context. That challenge makes the bull case, bear case, key risks, and
kill criteria explicit before a final recommendation is parsed and journaled.

## Recommendation Rank History

Recommendation reports can record an explicit rank snapshot so future reports can
show previous rank and rank movement:

```powershell
python scripts/longterm_journal.py report --journal-db path\to\journal.db --limit 20 --record-rank-snapshot
```

The cycle also records a rank snapshot after generating a recommendation report.
Rank movement is dry-run/reporting metadata only; it does not place orders.

## Next-Actions Report

```powershell
python scripts/longterm_next_actions.py --portfolio-state path\to\portfolio.json --limit 10
```

The next-actions report is still dry-run only. It evaluates the FXAIX benchmark gate before surfacing new buys:

- If the active sleeve is not clearing the benchmark guard, new buy candidates are marked `paused_buy_candidate`.
- If a buy is attractive but active-sleeve cash is short, it is marked `capital_needed` so an email or dashboard can later notify the user.
- If a held position has a recorded `broken` or `weakening` thesis state, it is marked `urgent_review_holding` rather than an ordinary review.
- When generated from a cycle, it can include a `Deferred Research Queue` section listing incomplete symbols, missing fields, provenance, and the suggested enrichment command to run before those ideas should consume LLM research.
- Protected symbols such as `FXAIX` remain excluded from sell, trim, rebalance, and rotation logic.

You can pass current evidence into the report without mutating journal records:

```powershell
python scripts/longterm_next_actions.py --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --evidence-file path\to\evidence.json
```

Evidence JSON can be either a direct symbol-to-list mapping or a richer mapping:

```json
{
  "AAPL": {
    "evidence": ["Services revenue still growing."],
    "decision_id": "optional-decision-id"
  }
}
```

Evidence files may not suggest sell, trim, reduce, or rebalance actions for protected symbols.

## Alpaca Paper Account Snapshot

For long-term paper trading, use Alpaca's standard REST/API path as the account
state source. Websockets are unnecessary for the current long-horizon research
loop; they can be added later only for fill/account-update monitoring after a
separately approved execution layer exists.

Read the paper account and write a portfolio-state file for next-actions:

```powershell
python scripts/longterm_alpaca_paper_snapshot.py --portfolio-state-output path\to\portfolio.json
python scripts/longterm_next_actions.py --portfolio-state path\to\portfolio.json --journal-db path\to\journal.db --limit 10
```

The snapshot command is read-only and paper-only. It does not place, cancel, or
modify orders.

Before a fresh supervised paper smoke, confirm the paper account is reset:

```powershell
python scripts/longterm_paper_account_cleanliness.py --portfolio-state path\to\portfolio.json --expected-cash 74000
python scripts/longterm_paper_account_cleanliness.py --portfolio-state path\to\portfolio.json --expected-cash 74000 --json
```

The cleanliness check is narrower than reconciliation. It simply asks whether
the exported paper account has no unexpected non-protected holdings and whether
cash is near the optional expected baseline. It does not call Alpaca or alter
the account.

Build one pre-flight report before a supervised paper smoke:

```powershell
python scripts/longterm_paper_smoke_readiness.py --portfolio-state path\to\portfolio.json --expected-cash 74000 --scheduler-readiness path\to\scheduler_readiness.json
python scripts/longterm_paper_smoke_readiness.py --portfolio-state path\to\portfolio.json --expected-cash 74000 --required-order-model whole_share --workflow-smoke path\to\paper_workflow_smoke.json --report-output path\to\paper_smoke_readiness.json --json
```

The smoke-readiness report combines account cleanliness, broker-capability
compatibility, optional scheduler-readiness output, and optional workflow-smoke
evidence. If `--workflow-smoke` is provided and that audit-only rehearsal is not
ready, the readiness report blocks. The default `notional_fractional` model
intentionally blocks Schwab API live compatibility; use
`--required-order-model whole_share` only when the planned smoke/live path has
been adapted to whole-share sizing.

Generate an ordered Monday paper-trading runbook:

```powershell
python scripts/longterm_paper_runbook.py --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --portfolio-state path\to\portfolio.json --action-plan path\to\account_action_plan.json --output-dir path\to\paper_artifacts --profile-config path\to\profile.json --expected-cash 74000
python scripts/longterm_paper_runbook.py --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --portfolio-state path\to\portfolio.json --action-plan path\to\account_action_plan.json --output-dir path\to\paper_artifacts --profile-config path\to\profile.json --expected-cash 74000 --json
```

The runbook is a deterministic checklist and command generator only. It does
not read Alpaca, write ledgers, or submit orders. By default, the supervised
submit command is redacted so the operator cannot accidentally copy it before
reviewing the saved preflight artifacts. Reveal it only after review:

```powershell
python scripts/longterm_paper_runbook.py --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --portfolio-state path\to\portfolio.json --action-plan path\to\account_action_plan.json --output-dir path\to\paper_artifacts --profile-config path\to\profile.json --expected-cash 74000 --include-submit-command
```

Even when revealed, the supervised submit command still requires
`--confirm-paper-submit SUPERVISED_PAPER_BUY_ONLY`.
When `--profile-config` is supplied, the generated snapshot, workflow-smoke,
and supervised-submit commands reuse the same paper profile. After any future
supervised paper buy is observed, the runbook includes a manual cleanup reminder
to sell or cancel the temporary paper position in Alpaca before the next run.

Check saved pre-submit runbook artifacts:

```powershell
python scripts/longterm_paper_runbook_check.py --workflow-smoke path\to\paper_workflow_smoke.json --paper-smoke-readiness path\to\paper_smoke_readiness.json --action-plan path\to\account_action_plan.json
python scripts/longterm_paper_runbook_check.py --workflow-smoke path\to\paper_workflow_smoke.json --paper-smoke-readiness path\to\paper_smoke_readiness.json --action-plan path\to\account_action_plan.json --report-output path\to\paper_runbook_check.json --json
```

The check reads saved artifacts only. It blocks if the workflow smoke or
paper-smoke readiness artifact is missing, malformed, not ready, promotion
blocked, or older than the promotion-aware schema v2 contract. The saved check
includes the workflow plan ID, canonical action-plan hash, buy-promotion
summary, and generation timestamp.

After any supervised paper submit, save the read-only order-status refresh
artifact:

```powershell
python scripts/longterm_paper_order_status_refresh.py --ledger-db path\to\paper_ledger.db --report-output path\to\paper_order_status_refresh.json --json
```

The Monday runbook includes this artifact path automatically. Status refresh
does not submit, cancel, or modify orders; it only appends read-only broker
status events to the paper ledger and writes the JSON report when requested.
If the ledger has no submitted paper order IDs, the command writes an empty
refresh report without opening a broker connection.

Summarize the saved Monday paper artifacts before or after the smoke:

```powershell
python scripts/longterm_paper_monday_check.py --runbook path\to\paper_runbook.json --workflow-smoke path\to\paper_workflow_smoke.json --paper-smoke-readiness path\to\paper_smoke_readiness.json --runbook-check path\to\paper_runbook_check.json --status-refresh path\to\paper_order_status_refresh.json
python scripts/longterm_paper_monday_check.py --runbook path\to\paper_runbook.json --workflow-smoke path\to\paper_workflow_smoke.json --paper-smoke-readiness path\to\paper_smoke_readiness.json --runbook-check path\to\paper_runbook_check.json --status-refresh path\to\paper_order_status_refresh.json --report-output path\to\paper_monday_operator_check.json --json
```

The Monday operator check is artifact-only. It reports whether workflow smoke,
paper-smoke readiness, runbook-check evidence, action-plan hash, submit-command
redaction, promotion blockers, status-refresh errors, and paper-account
cleanliness look reviewable. It treats schema-v1 workflow/readiness/runbook-check
artifacts as stale safety evidence after the buy-promotion paper-boundary
upgrade.

Market-hours supervised paper-smoke sequence:

1. Generate the redacted runbook and review the artifact commands.
2. Run workflow smoke, paper-smoke readiness, and runbook check.
3. Run `longterm_paper_monday_check.py` against the redacted runbook; it should
   be review-ready with no blockers.
4. During market hours only, regenerate the runbook with
   `--include-submit-command`.
5. Submit only if the command still has
   `--confirm-paper-submit SUPERVISED_PAPER_BUY_ONLY` and the runbook-check
   hash matches the current action plan.
6. Immediately run status refresh, lifecycle, paper-trading verification, a
   fresh Alpaca snapshot, and account cleanliness.
7. If the paper order is pending after the smoke, cancel it before leaving the
   desk. If it filled, manually close the temporary paper position and verify
   the final snapshot shows no leftover position.

Reconcile the current paper snapshot against a dry-run action plan or expected
cash before considering any paper-execution feature:

```powershell
python scripts/longterm_paper_reconciliation.py --portfolio-state path\to\portfolio.json --action-plan path\to\account_action_plan.json --expected-cash 5000
python scripts/longterm_paper_reconciliation.py --portfolio-state path\to\portfolio.json --action-plan path\to\account_action_plan.json --json
python scripts/longterm_paper_reconciliation.py --portfolio-state path\to\portfolio.json --paper-ledger-db path\to\paper_ledger.db --json
```

The reconciliation report flags missing target symbols, unexpected non-protected
holdings, target-value mismatches, cash delta, and protected-symbol presence. It
can also use filled/rejected paper execution events to flag missing filled
symbols or unexpected holdings after rejected orders. It is read-only and does
not submit paper or live orders.

Build a non-submitting paper order preview from a dry-run action plan:

```powershell
python scripts/longterm_paper_price_map.py --action-plan path\to\account_action_plan.json --price-map-output path\to\price_map.json --json
python scripts/longterm_paper_order_preview.py --portfolio-state path\to\portfolio.json --action-plan path\to\account_action_plan.json
python scripts/longterm_paper_order_preview.py --portfolio-state path\to\portfolio.json --action-plan path\to\account_action_plan.json --json
python scripts/longterm_paper_order_preview.py --portfolio-state path\to\portfolio.json --action-plan path\to\account_action_plan.json --record-preview --ledger-db path\to\paper_ledger.db --json
python scripts/longterm_paper_order_preview.py --portfolio-state path\to\portfolio.json --action-plan path\to\account_action_plan.json --order-model whole_share --price-map path\to\price_map.json --record-preview --ledger-db path\to\paper_ledger.db --json
```

The preview converts `BUY` intents into buy-notional preview rows, `REBALANCE`
intents into paired sell/buy preview rows, and `REVIEW` / `BLOCKED` /
`CAPITAL_NEEDED` intents into `no_order` rows. It carries decision IDs, risk
metadata, blocked reasons, cash shortfall, and rebalance transaction IDs. It
does not import Alpaca and cannot submit orders.

Use `--order-model whole_share` with an explicit JSON price map when the paper
workflow should mirror a whole-share live broker such as Schwab API. The preview
floors BUY quantities to whole shares, records the requested notional, estimated
price, executable quantity, estimated notional, and size variance, and blocks
rows when the price is missing or the target value cannot buy at least one share.
The price map is caller-supplied on purpose; this command does not make hidden
market-data calls.

`longterm_paper_price_map.py` is an explicit read-only helper for the same
workflow. It reads the action plan, skips protected symbols, fetches quotes for
orderable BUY/REBALANCE symbols through the configured Alpaca paper data path,
and writes the plain `{symbol: price}` JSON consumed by `--price-map`.

Run an audit-only whole-share workflow smoke before any supervised paper submit:

```powershell
python scripts/longterm_paper_workflow_smoke.py --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --portfolio-state path\to\portfolio.json --action-plan path\to\account_action_plan.json --report-output path\to\paper_workflow_smoke.json --json
```

The workflow smoke fetches a read-only price map, records a whole-share preview
to the paper ledger, and runs the paper execution boundary with submission
disabled. It returns `ready_for_supervised_submit=true` only when the price map,
preview, buy-promotion state, and execution audit are all clean. The JSON
includes `promotion_summary`; any missing or non-actionable buy promotion blocks
the workflow with `buy_promotion_blocked_rows`. `--report-output` can persist
the JSON artifact for operator review before a supervised submit.
Because promotion-aware workflow artifacts use schema v2, older saved schema-v1
workflow or paper-smoke-readiness files should be regenerated before any
supervised submit attempt.

Inspect recorded preview rows:

```powershell
python scripts/longterm_paper_preview_ledger.py list --ledger-db path\to\paper_ledger.db
python scripts/longterm_paper_preview_ledger.py executions --ledger-db path\to\paper_ledger.db
python scripts/longterm_paper_preview_ledger.py summary --ledger-db path\to\paper_ledger.db
python scripts/longterm_paper_preview_ledger.py summary --ledger-db path\to\paper_ledger.db --json
```

Recommendation reports can also show the latest paper preview status:

```powershell
python scripts/longterm_journal.py report --journal-db path\to\journal.db --paper-ledger-db path\to\paper_ledger.db
```

Recommendation reports, next-actions, and position intelligence reports can also
surface latest paper execution status from the same ledger when execution/status
events exist. Original decision rows remain immutable; these surfaces join by
`decision_id` at report time.

Evaluate pre-6B paper execution eligibility from the same action plan and
preview ledger:

```powershell
python scripts/longterm_paper_execution_eligibility.py --portfolio-state path\to\portfolio.json --action-plan path\to\account_action_plan.json --ledger-db path\to\paper_ledger.db
python scripts/longterm_paper_execution_eligibility.py --portfolio-state path\to\portfolio.json --action-plan path\to\account_action_plan.json --ledger-db path\to\paper_ledger.db --paper-execution-enabled --json
```

This command is still non-submitting. It checks decision-id traceability,
explicit paper-execution gate state, protected symbols, actionable buy-promotion
state for stock BUYs, intent blockers, preview freshness, and ready/blocked/no-order
preview status. A ready result is only permission for a future Stage 6B submit
boundary to revalidate the same facts; it is not a broker order.

Paper execution events are persisted by `PaperTradeLedger` for
submit-blocked/submitted/rejected audit trails, with future room for
filled/reconciled states. Execution events require `decision_id` for
traceability and do not mutate the original decision rows.

Run the supervised Stage 6B paper execution boundary in audit-only mode:

```powershell
python scripts/longterm_paper_execution.py --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --portfolio-state path\to\portfolio.json --action-plan path\to\account_action_plan.json
python scripts/longterm_paper_execution.py --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --portfolio-state path\to\portfolio.json --action-plan path\to\account_action_plan.json --audit-output path\to\paper_execution_audit.json --json
```

Submit eligible simple BUY previews to Alpaca paper only when explicitly
intended:

```powershell
python scripts/longterm_paper_execution.py --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --portfolio-state path\to\portfolio.json --action-plan path\to\account_action_plan.json --submit-paper-orders --confirm-paper-submit SUPERVISED_PAPER_BUY_ONLY --runbook-check path\to\paper_runbook_check.json --audit-output path\to\paper_execution_audit.json
```

Stage 6B is deliberately narrow:

- It submits only simple `BUY` previews.
- `--submit-paper-orders` also requires the exact `--confirm-paper-submit SUPERVISED_PAPER_BUY_ONLY` latch; without it, the command exits before refreshing broker state or constructing the submit adapter.
- `--submit-paper-orders` also requires a ready, fresh `--runbook-check` artifact whose plan ID and canonical action-plan hash match the action plan being submitted.
- The runbook-check artifact must be schema v2 or newer, proving the saved
  evidence came from the promotion-aware workflow/readiness path.
- `--submit-paper-orders` blocks when the Alpaca paper market clock is closed, so market BUY smoke orders are not left pending after hours.
- Rebalance, sell, and sell-to-fund-buy previews are hard-blocked with `rebalance_blocked_v1`.
- It revalidates protected symbols, actionable buy-promotion state, benchmark guard, thesis/review status, decision confidence/recommendation, preview freshness, cash, active-rules hash, and duplicate submission state immediately before paper submission.
- A stock BUY missing `ACTIONABLE_BUY` promotion is blocked with `missing_buy_promotion_review` or `buy_promotion_not_actionable`, even if a stale or hand-edited action plan otherwise looks executable.
- The real submit path refreshes Alpaca paper account state before broker calls; the portfolio JSON remains useful for audit/dry-run mode.
- It uses deterministic `client_order_id` values for broker idempotency and records `submission_attempt_id` on every event.
- It is not scheduler-wired and cannot submit live orders.

Refresh statuses for already-submitted Alpaca paper orders:

```powershell
python scripts/longterm_paper_order_status_refresh.py --ledger-db path\to\paper_ledger.db
python scripts/longterm_paper_order_status_refresh.py --ledger-db path\to\paper_ledger.db --json
```

Status refresh is read-only with respect to the broker. It looks up submitted
paper order IDs, calls Alpaca paper order-status reads, and appends status events
such as `filled`, `partially_filled`, `rejected`, or `status_refresh_error` to
`PaperTradeLedger`. It does not submit, cancel, replace, or modify orders.
Because the ledger is append-only, historical status-refresh errors remain
visible even after a later healthy status is recorded. Lifecycle/operator
artifacts distinguish this by surfacing whether the current/latest status is
still an error.

Generate live-readiness evidence that paper trading has been verified:

```powershell
python scripts/longterm_paper_trading_verification.py --ledger-db path\to\paper_ledger.db
python scripts/longterm_paper_trading_verification.py --ledger-db path\to\paper_ledger.db --observed-output path\to\paper_trading_observed.json --json
```

This report is read-only. It marks `paper_trading_verified=true` only when the
paper ledger contains at least one filled paper execution and no current
status-refresh errors.

Summarize paper fill outcomes versus `FXAIX` from explicit current prices:

```powershell
python scripts/longterm_paper_outcomes.py --ledger-db path\to\paper_ledger.db --price-map path\to\prices.json
python scripts/longterm_paper_outcomes.py --ledger-db path\to\paper_ledger.db --price-map path\to\prices.json --json
```

The price map is provider-free evidence, for example:

```json
{
  "NVDA": {"current_price": 120.0},
  "FXAIX": {"current_price": 55.0}
}
```

Paper outcomes do not mutate decision rows. They compare filled paper orders
against the benchmark baseline stored in the fill event when available, and mark
rows as `pending_price` when required prices are missing.

Summarize the paper lifecycle across previews, execution events, and optional
paper outcomes:

```powershell
python scripts/longterm_paper_lifecycle.py --ledger-db path\to\paper_ledger.db
python scripts/longterm_paper_lifecycle.py --ledger-db path\to\paper_ledger.db --price-map path\to\prices.json --json
python scripts/longterm_paper_lifecycle.py --ledger-db path\to\paper_ledger.db --report-output path\to\paper_lifecycle.json --json
```

Lifecycle summaries are read-only and classify symbols as `preview_ready`,
`preview_blocked`, `submitted_pending_fill`, `filled_outcome_pending`,
`outcome_evaluated`, `execution_rejected`, or `execution_status_error`.
The Monday runbook saves this artifact after status refresh and cleanup so the
operator can see whether the latest paper order is filled, canceled, rejected,
or still pending.

Run the feedback refresh maintenance loop:

```powershell
python scripts/longterm_feedback_refresh.py --journal-db path\to\journal.db --paper-ledger-db path\to\paper_ledger.db --profile-config path\to\profile.json --portfolio-state path\to\portfolio.json --action-plan path\to\account_action_plan.json
python scripts/longterm_feedback_refresh.py --journal-db path\to\journal.db --paper-ledger-db path\to\paper_ledger.db --outcome-price-map path\to\prices.json --record-eligibility-events --json
```

Feedback refresh is still dry-run maintenance. It can rebuild symbol profiles,
apply paper-preview, paper execution, and reconciliation feedback, refresh
outcomes only from an explicit price map, compute outcome freshness, summarize
review status and benchmark guard state, and persist idempotent eligibility
evaluation events.
Eligibility events include `requires_revalidation=true`; they are audit records,
not authorization to submit.

The generated `feedback_tuning_inputs` payload is explicitly analysis-only. It
may inform human review and future LLM planning, but it must not mutate
recommendation ranks, rebalance weights, position sizing, or action planning.
It can include paper execution counts/status so research follow-ups know whether
a recommendation has been filled, rejected, or hit a status-refresh error.

Build the advisory scheduler-readiness checklist from existing artifacts:

```powershell
python scripts/longterm_scheduler_readiness.py --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --action-plan path\to\account_action_plan.json --feedback-summary path\to\feedback_summary.json --paper-lifecycle-summary path\to\paper_lifecycle.json
python scripts/longterm_scheduler_readiness.py --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --action-plan path\to\account_action_plan.json --json
```

Scheduler readiness is not automation. V1 always reports
`scheduler_submission_enabled=false` and `ready_for_scheduler_paper_submit=false`.
It surfaces blockers and warnings around protected symbols, benchmark guard,
review/thesis state, buy-promotion state, active rules, feedback freshness,
lifecycle errors, and decision traceability before any future scheduler-submit
design is considered. A stock `BUY` order intent without an actionable promotion
review is a blocker; pending promotion follow-up with `order_intent=NONE`
remains visible as a warning.

Build the full read-only operator status bundle:

```powershell
python scripts/longterm_operator_status_bundle.py --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --paper-ledger-db path\to\paper_ledger.db --action-plan path\to\account_action_plan.json --price-map path\to\prices.json --feedback-summary path\to\feedback_summary.json
python scripts/longterm_operator_status_bundle.py --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --paper-ledger-db path\to\paper_ledger.db --action-plan path\to\account_action_plan.json --json
```

The bundle combines paper lifecycle, advisory scheduler readiness, and position
intelligence into one operator surface. It is intended for manual review before
any later scheduler automation design and keeps order submission disabled.

## Position Intelligence Report

Generate an on-demand monthly or quarterly position intelligence report before
Stage 6B paper execution work:

```powershell
python scripts/longterm_position_report.py --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --paper-ledger-db path\to\paper_ledger.db
python scripts/longterm_position_report.py --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --period quarterly --paper-ledger-db path\to\paper_ledger.db
python scripts/longterm_position_report.py --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --paper-ledger-db path\to\paper_ledger.db --paper-outcome-price-map path\to\prices.json
```

The report is read-only and not scheduler-wired yet. It summarizes cash, active
sleeve value, protected/core value, and tracked portfolio value, then adds a
per-position intelligence section using collected journal/research context:
latest recommendation, rank, repeat recommendation count, thesis, review status,
paper preview status, eligibility feedback, reconciliation notes, outcome versus
`FXAIX`, paper fill outcome versus `FXAIX` when an explicit price map is
supplied, outcome freshness, new-information notes, invalidation conditions, and
knowledge gaps.

Send the same report through the local Brevo-compatible email config only when
explicitly intended:

```powershell
python scripts/longterm_position_report.py --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --period monthly --paper-ledger-db path\to\paper_ledger.db --email-config path\to\email_notifications.json --send
python scripts/longterm_position_report.py --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --period quarterly --paper-ledger-db path\to\paper_ledger.db --recipient-email you@example.com --send
```

Position intelligence emails are informational only. They do not authorize
orders, mutate ranking, change sizing, refresh prices, or trigger broker calls.
Automatic monthly/quarterly scheduling is intentionally deferred until the
future scheduler block owns cadence and delivery policy.

## Capital-Needed Email Payloads

Capital-needed emails are informational only. The long-term trader can build provider-agnostic email payloads and send them through a Brevo-compatible SMTP sender, but email sending should remain off until explicitly enabled in a local ignored config file. A capital request should only be sent when a high-conviction candidate lacks active-sleeve cash and existing non-protected holdings do not already have sell/reduce recommendations that should fund the idea first.

Use this template:

```text
ai_trader/trading_agent/config/email_notifications.example.json
```

Copy it locally to `ai_trader/trading_agent/config/email_notifications.json` and fill in the Brevo SMTP login/key if you want delivery. It is fine to reuse `jchap2k.swingtrader@gmail.com` as both recipient and verified sender.

Dry-run the alert markdown:

```powershell
python scripts/longterm_capital_alert.py --active-sleeve-value 34000 --available-cash 500 --portfolio-state path\to\portfolio.json
```

Send through the local Brevo-compatible config only when explicitly intended:

```powershell
python scripts/longterm_capital_alert.py --active-sleeve-value 34000 --available-cash 500 --portfolio-state path\to\portfolio.json --send
```

## Motley Fool Idea Capture

Motley Fool premium tables are treated as a high-quality idea source, not a buy authority. Captured rows become investigation ideas that still require independent long-term research, valuation review, thesis critique, and portfolio-fit checks.

Capture the full recommendation/ranking sources:

```powershell
python scripts/longterm_motley_fool_capture.py
```

Capture one source:

```powershell
python scripts/longterm_motley_fool_capture.py --source new_recommendations
python scripts/longterm_motley_fool_capture.py --source analyst_rankings
python scripts/longterm_motley_fool_capture.py --source quant_rankings
python scripts/longterm_motley_fool_capture.py --source dashboard
```

The capture uses the logged-in Chrome profile at `~/.grok3api_chrome_profile`.
Use one capture process at a time for that profile. The default full capture runs
the pages sequentially so the profile is not opened by multiple Playwright
sessions at once.

Captured Motley Fool ideas include `motley_fool_company_url` / `source_url`
when the premium table exposes a per-company link. Some Fool tables use numeric
company URLs such as `https://www.fool.com/premium/company/202816`; later
enrichment can navigate those URLs and let Fool resolve the detailed financials
page before summarizing the ticker context.

Enrich captured company URLs before sending thin source rows into the research
committee:

```powershell
python scripts/longterm_motley_fool_company_enrich.py `
  --idea-batch path\to\research-batch-001.json `
  --output path\to\research-batch-001.enriched.json `
  --snapshot-output-dir path\to\snapshots
```

The default backend is `scrapling_stealthy`, uses the logged-in Chrome profile,
and runs headless unless `--no-headless` is supplied. Use one browser process at
a time for `~/.grok3api_chrome_profile`. The output preserves structured
metrics, section summaries, and URLs; it should not be treated as trade
authority.

Scheduler-facing Motley Fool settings live at:

```text
ai_trader/trading_agent/config/motley_fool_capture.json
```

Use the committed `.example.json` as the safe template. The local config is
ignored because it records whether this machine has a subscribed/logged-in Fool
profile available. Current scheduler-facing behavior is:

- If `enabled` is `false`, skip Motley Fool intake without warning or failure.
- If `enabled` is `true` and `cookie_ready` is `false`, open the configured
  Chrome profile at `login_url` for interactive login/setup, then set
  `cookie_ready` once verified.
- If `enabled` and `cookie_ready` are both `true`, call the capture API/scripts
  directly and continue treating Fool rows as research ideas, not trade orders.

Run interactive setup when a subscribed profile needs fresh cookies:

```powershell
python scripts/longterm_motley_fool_setup.py
```

This opens Chrome with the configured `profile_dir`, waits for you to complete
login, verifies access by capturing the configured verification source
(`dashboard` by default), then persists `cookie_ready=true` into the local
ignored config file.

## One-Cycle Long-Term Orchestration

The repo now has a first dry-run orchestration entrypoint that can combine:
- manual idea input
- optional Motley Fool capture
- research packet normalization
- CGH decision recording
- markdown recommendation report generation
- optional next-actions report generation when a portfolio snapshot is supplied

Minimal smoke with Motley Fool disabled or config-missing:

```powershell
python scripts/run_longterm_cycle.py --motley-fool-config path\to\missing_or_optional.json --quiet
```

Allow a one-cycle run to launch setup if Fool is enabled but cookies are not
ready:

```powershell
python scripts/run_longterm_cycle.py --launch-login-if-needed --journal-db path\to\journal.db
```

Run one cycle from a single idea file:

```powershell
python scripts/run_longterm_cycle.py --idea-file path\to\idea.json --journal-db path\to\journal.db
```

Run one cycle and also generate next-actions output using a portfolio snapshot:

```powershell
python scripts/run_longterm_cycle.py --idea-batch path\to\ideas.json --portfolio-state path\to\portfolio.json --journal-db path\to\journal.db
```

Add dry-run capital-needed alert markdown to the same cycle result:

```powershell
python scripts/run_longterm_cycle.py --idea-batch path\to\ideas.json --portfolio-state path\to\portfolio.json --journal-db path\to\journal.db --active-sleeve-value 35000 --available-cash 500
```

Optionally provide a market-regime file so the dry-run account action plan can
park leftover active-sleeve cash instead of leaving it idle:

```powershell
python scripts/run_longterm_cycle.py --idea-batch path\to\ideas.json --portfolio-state path\to\portfolio.json --journal-db path\to\journal.db --market-regime-file path\to\market_regime.json
```

Example `market_regime.json`:

```json
{
  "vix_level": 35,
  "spy_above_200d": false,
  "ten_year_yield_trend": "falling"
}
```

If `risk_regime` is supplied directly, it is used as the explicit operator
classification. Otherwise, the file is classified from the supplied signals.
This remains dry-run planning only; it does not submit broker orders.

Stage 6B supervised paper execution remains narrower than the account action
plan. `PARK_IDLE_CASH` and `PARK_DEFENSIVE_CASH` intents are rendered for
operator visibility but are excluded from V1 paper submission readiness. The
V1 submit boundary still submits only explicit simple `BUY` previews; rebalances
remain hard-blocked.

Cycle result JSON includes operator artifacts:
- `idea_provenance_summary`
- `packet_completeness_warnings`
- `decision_journal_refs`
- `report_generated`
- `next_actions_generated`
- `capital_alert_markdown` and `capital_alert_generated`
- `rebalance_markdown` and `rebalance_generated`
- `account_action_plan` and `account_action_plan_generated`

`account_action_plan` is the structured dry-run contract for the future
autonomous account manager. It includes a schema version, plan id, dry-run mode,
benchmark gate reason, blocked reasons, and machine-readable intents such as
`BUY`, `REBALANCE`, `REVIEW`, `CAPITAL_NEEDED`, and `BLOCKED`. It is not an
order ticket and does not place broker orders.
When the journal supports it, each generated account action plan is also stored
in `longterm_action_plan_journal` so later paper/live reconciliation can compare
intended actions against outcomes.

Recommendation table ranks are action-aware. The journal emits a `ranking_score`
and `rank_reason` for each row so actionable `BUY` / `ADD` candidates with
meaningful suggested size can outrank passive high-confidence `HOLD` rows. The
markdown report exposes both fields for operator auditability.

Rebalance markdown is an explanatory dry-run artifact. It includes the funding
source, target, proposed sell value, source and target ranks, rank gap, source
current value, source target value, suggested target size, decision IDs when
available, review/thesis status context when supplied, and the benchmark gate
reason. Protected holdings remain excluded as funding sources.
When review context is available, stale, deteriorating, broken, or review-due
source holdings receive a small dry-run rebalance-score adjustment so the
proposal can favor rotating from names with higher thesis-review risk. This is
still advisory only and does not place orders.

Analyze evaluated outcomes before changing any rebalance-score weights:

```powershell
python scripts/longterm_rebalance_outcomes.py --journal-db path\to\journal.db
python scripts/longterm_rebalance_outcomes.py --journal-db path\to\journal.db --json
```

This report groups evaluated decisions by shared thesis/review-risk buckets and
shows excess return versus `FXAIX`, beat rate, confidence-weighted excess return,
and pending outcome counts. It is evidence for future tuning only; it does not
change `RebalancePlanner` behavior.

Current limitations of this first cycle:
- still dry-run only
- recommendation, next-actions, and capital-alert outputs are returned as markdown strings in the cycle result JSON

## Dry-Run Long-Term Scheduler

The dry-run scheduler repeatedly calls the same one-cycle orchestration path.
It does not place orders. Each cycle reloads profile, Motley Fool settings, idea
input, and portfolio state from disk so cash/holding changes are not frozen in
memory during a longer run.

Print the intended cadence model:

```powershell
python scripts/longterm_scheduler_operating_model.py
python scripts/longterm_scheduler_operating_model.py --json
```

The operating model is guidance for daily/weekly/as-needed routines. It is not a
cron daemon and it does not execute broker orders.

Print live-readiness gates:

```powershell
python scripts/longterm_broker_capabilities.py
python scripts/longterm_broker_capabilities.py --required-order-model whole_share --observed-output path\to\broker_capability_observed.json
python scripts/longterm_live_readiness.py
python scripts/longterm_live_readiness.py --observed-file path\to\live_readiness_observed.json
python scripts/longterm_live_readiness.py --observed-file path\to\base_observed.json --observed-fragment path\to\broker_capability_observed.json
python scripts/longterm_live_readiness_bundle.py --observed-file path\to\base_observed.json --paper-ledger-db path\to\paper_ledger.db --paper-smoke-readiness path\to\paper_smoke_readiness.json --required-order-model whole_share --report-output path\to\live_readiness_bundle.json
```

The live-readiness checklist is intentionally conservative. It reports unmet
gates and does not enable live mode. The broker-capability command is a static,
advisory helper for the `broker_capability_match` gate; it does not call a
broker. The default check blocks Alpaca paper notional/fractional sizing from
being treated as Schwab API live-ready. Use `--required-order-model whole_share`
only after the live plan has deliberately been adapted to whole-share sizing.
Observed fragments are merged after the base observed file, so later fragments
can intentionally override earlier gate values.
The bundle command assembles broker-capability, paper-trading verification, and
optional paper-smoke readiness evidence from local artifacts, but it is still
evidence-only and does not enable live execution.

Run exactly one scheduled cycle:

```powershell
python scripts/run_longterm_scheduler.py --run-once --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --quiet
```

Run one scheduled cycle with a refreshed discovery-candidate file:

```powershell
python scripts/run_longterm_scheduler.py --run-once --discovery-candidates path\to\candidates.json --journal-db path\to\journal.db --quiet
python scripts/run_longterm_scheduler.py --run-once --discovery-source-file path\to\sp500.csv --discovery-source sp500 --journal-db path\to\journal.db --quiet
python scripts/run_longterm_scheduler.py --run-once --discovery-source-file path\to\sp500.csv --discovery-source sp500 --discovery-enrichment-file path\to\fundamentals.json --discovery-enrichment-source fundamentals_cache --journal-db path\to\journal.db --quiet
```

Run a bounded recurring dry-run:

```powershell
python scripts/run_longterm_scheduler.py --max-runs 3 --interval-seconds 3600 --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --quiet
```

Write the structured scheduler summary to disk:

```powershell
python scripts/run_longterm_scheduler.py --run-once --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --summary-output path\to\scheduler_summary.json --quiet
```

Allow setup if Motley Fool is enabled but cookies are stale:

```powershell
python scripts/run_longterm_scheduler.py --run-once --launch-login-if-needed --journal-db path\to\journal.db
```

The scheduler summary JSON includes per-run status, capture/setup status,
decision IDs, idea provenance summary, packet completeness warnings,
recommendation markdown, next-actions markdown, capital-alert markdown,
rebalance markdown, account action plan, and any error message. Use
`--continue-on-error` only for supervised dry-run testing where a later cycle
should still run after a failed cycle.

## Calendar-Flow Concept Research

There is now a research-only tool for evaluating the monthly `TLT` calendar-flow
idea that came from the X thread. This is not wired into long-term decision
rules or live trading.

Run the summary view:

```powershell
python scripts/run_tlt_calendar_flow_research.py --symbol TLT --start 2004-01-01 --end 2024-12-01
```

Add a simple round-trip cost assumption:

```powershell
python scripts/run_tlt_calendar_flow_research.py --symbol TLT --start 2004-01-01 --end 2024-12-01 --round-trip-cost-bps 10
```

Show every trade only when needed:

```powershell
python scripts/run_tlt_calendar_flow_research.py --symbol TLT --start 2004-01-01 --end 2024-12-01 --include-trades
```

Current conclusion from the rebuilt test:
- the social-media backtest claims were not credible as shown
- the cleaned-up test still suggests a plausible `TLT` calendar effect
- this should be treated as optional research context, not as a direct active-sleeve rule for the long-term quality-growth trader

## Grok Project Review

The repo-safe project config is:

```text
ai_trader/trading_agent/config/grok_project_config.json
```

It points browser-based Grok review tooling at the long-term trader project:

```text
https://grok.com/project/e397a91c-e647-4c3b-868f-ff0d0ed6c175?tab=conversations
```

`GrokPlanReviewer.review(..., trading_mode="auto")` reads that config and uses the `longterm` context. You can still override the project URL for one run with `GROK_PROJECT_URL`.

## Minimal Portfolio Snapshot

```json
{
  "cash": 5000,
  "holdings": [
    {"symbol": "FXAIX", "market_value": 34000, "quantity": 120.5},
    {"symbol": "AAPL", "market_value": 3000, "quantity": 12}
  ]
}
```

## Minimal Decision File

```json
{
  "recommendation": "BUY",
  "confidence": 86,
  "suggested_size_pct": 6,
  "key_thesis": "Durable long-term compounder."
}
```
