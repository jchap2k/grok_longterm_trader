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

The default committee is `decision_4` to control Grok 4.3 cost. It is the
normal path for routine add, hold, review, and high-conviction/low-risk
decisions because the stronger 4.3 model plus `ThesisCritic` and
`DecisionIntegrator` provide enough guardrails for most research.

Use the expanded `decision_6` committee only when the extra depth is worth the
cost and latency:

| Decision context | Recommended preset | Why |
|---|---|---|
| Routine add, hold, or review | `decision_4` | Fastest and cheapest; enough for normal monitoring. |
| Large position size above roughly 5-10% of the active sleeve | `decision_6` | Adds valuation and portfolio-allocation rigor. |
| New or unproven thesis | `decision_6` | Extra challenge reduces first-thesis blind spots. |
| Borderline valuation or unclear edge | `decision_6` | `ValuationEdgeAnalyst` adds useful pushback. |
| Choppy macro, Fed pivot/recession risk, or unusual uncertainty | `decision_6` | More perspectives reduce regime mistakes. |
| Very high conviction and low operational risk | `decision_4` | Strong model plus critic is usually sufficient. |

Manual expanded-committee example:

```powershell
python scripts/run_longterm_research.py --symbol AAPL --agent-preset decision_6
```

Before spending the wider committee, the scheduler/operator can generate an
advisory-only preset artifact from saved context. This helper does not call
Grok, does not alter recommendations, and does not submit orders; it simply
recommends whether the next committee pass should stay on `decision_4` or
escalate to `decision_6`.

```powershell
python scripts/longterm_committee_preset_policy.py --action-plan path\to\account_action_plan.json --market-regime path\to\market_regime.json --research-items path\to\research_queue_selected.json --active-sleeve-value 100000 --report-output path\to\committee_preset_policy.json --json
```

## Account Tax Mode

Set `account_strategy_mode` explicitly in the portfolio profile before relying
on parking or rebalance guidance. `roth_ira`, `paper`, `paper_non_taxable`, and
other non-taxable modes can treat broad SPY/SGOV/TLT parking and dry-run
rebalance review as non-taxable planning surfaces. `taxable` and unspecified
profiles suppress broad idle-cash parking and broad rebalance churn in
`account_action_plan` before any Stage 6B preview or execution boundary sees
the plan.

This tax-mode guard does not ban symbol-specific sells. If a stock thesis is
broken, weakening, or otherwise clearly sell-worthy, that should be represented
as a research/review decision with explicit symbol-level rationale rather than a
generic "sell everything to cash" or frequent tax-inefficient rebalance.

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

Run discovery directly from a remote universe source URL when a hand-downloaded
file is not needed. This is the starting point for wider non-Fool universe
coverage:

```powershell
python scripts/run_longterm_discovery.py --source-url https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt --source nasdaq_listed --watchlist-ideas-output path\to\nasdaq_watchlist_ideas.json --watchlist-limit 100
python scripts/run_longterm_discovery.py --source-url https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt --source nyse_amex_listed --watchlist-ideas-output path\to\otherlisted_watchlist_ideas.json --watchlist-limit 100
```

The remote source loader only turns public listing/holding files into candidate
rows. It does not enrich, research, rank, or trade those names. After the
watchlist idea output is written, pass it through the evidence enrichment
pipeline before committee review. Broad listing sources intentionally start as
watchlist/enrichment candidates unless fundamentals, quality, and source metrics
are added; they should not become research-ready from ticker presence alone.
The listing loader also filters obvious non-operating security rows such as
ETFs, test issues, warrants, rights, units, preferred shares, notes, blank-check
companies, and SPAC/acquisition-company rows before they can consume enrichment
calls.

One-cycle and scheduler runs can also load a remote discovery source directly:

```powershell
python scripts/run_longterm_cycle.py --discovery-source-url https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt --discovery-source nasdaq_listed --journal-db path\to\journal.db
python scripts/run_longterm_scheduler.py --run-once --discovery-source-url https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt --discovery-source nasdaq_listed --journal-db path\to\journal.db --quiet
```

Use those direct cycle/scheduler URL inputs sparingly. For large listing sources,
the safer normal workflow is still: fetch discovery watchlist ideas, enrich a
capped subset with fundamentals/news/earnings/scorecards, then feed the
evidence-ready idea batch into the research cycle.

For the safer broad-universe workflow, use the extended-universe preparer. It
fetches or loads a public source, applies the discovery/listing filters, exports
a capped watchlist idea batch, writes optional batch files, and prints the next
evidence-enrichment command:

```powershell
python scripts/longterm_extended_universe.py --source-url https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt --source nasdaq_listed --watchlist-limit 100 --batch-size 10 --ideas-output path\to\extended_watchlist_ideas.json --batches-output-dir path\to\extended_batches --summary-output path\to\extended_universe_summary.json
```

For a targeted smoke slice, preserve a chosen symbol order with
`--include-symbols`:

```powershell
python scripts/longterm_extended_universe.py --source-url https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt --source nasdaq_listed --include-symbols AAPL,MSFT,NVDA --watchlist-limit 10 --batch-size 5 --ideas-output path\to\extended_watchlist_ideas.json --summary-output path\to\extended_universe_summary.json
```

For a broad source, do not send thousands of tickers directly into news/Grok or
committee research. Run the pure-Python first-pass scan first. It attaches or
loads deterministic fundamentals, builds the non-proprietary quality-growth
scorecard, ranks the universe by an explicit first-pass rank score, and advances
the top relative slice such as 5-10%. The rank score is `70%` Moneyball-style
quality-growth `superscore` plus `30%` deterministic quant/fundamental blend
from quality, growth, valuation, and safety. This avoids the swing-trader
failure mode where hard gates block every name while still keeping expensive
enrichment focused on the best available candidates.

```powershell
python scripts/longterm_extended_universe_scan.py --idea-batch path\to\extended_watchlist_ideas.json --provider yfinance --fundamentals-cache path\to\extended_fundamentals_cache.json --top-percent 10 --min-pass-count 10 --max-pass-count 300 --min-coverage-percent-for-enrichment 80 --passed-output path\to\extended_watchlist.python_scan_passed.json --deferred-output path\to\extended_watchlist.python_scan_deferred.json --scanned-output path\to\extended_watchlist.python_scan_scanned.json --passed-jsonl-output path\to\extended_watchlist.python_scan_passed.jsonl --deferred-jsonl-output path\to\extended_watchlist.python_scan_deferred.jsonl --scanned-jsonl-output path\to\extended_watchlist.python_scan_scanned.jsonl --summary-output path\to\extended_watchlist.python_scan_summary.json --markdown-output path\to\extended_watchlist.python_scan_report.md
```

The first-pass scan cache is symbol-keyed and resumable. If a prior overnight
run already fetched `MSFT`, a later run will reuse that cache row and only fetch
missing symbols. The summary reports `fundamentals_cache_hits` and
`fundamentals_cache_fetches` so the operator can tell whether a run is mostly
reusing local data or still filling the cache. Individual provider failures are
reported as `fundamentals_fetch_errors`; those symbols remain in the scanned
set with missing-metric warnings instead of aborting the whole batch.

To fill a very large cache gradually, add `--fetch-limit`. The scan still writes
pass/defer artifacts, but the summary's `fundamentals_coverage_percent`,
`fundamentals_fetch_skipped_count`, and `fundamentals_fetch_skipped_symbols`
show whether the current ranking is based on broad coverage or only a partial
cache. Prefer the final enrichment pass after coverage is high enough for the
universe slice being evaluated.

```powershell
python scripts/longterm_extended_universe_scan.py --idea-batch path\to\extended_watchlist_ideas.json --provider yfinance --fundamentals-cache path\to\extended_fundamentals_cache.json --fetch-limit 100 --top-percent 10 --min-pass-count 10 --max-pass-count 300 --passed-output path\to\extended_watchlist.python_scan_passed.json --deferred-output path\to\extended_watchlist.python_scan_deferred.json --scanned-output path\to\extended_watchlist.python_scan_scanned.json --passed-jsonl-output path\to\extended_watchlist.python_scan_passed.jsonl --deferred-jsonl-output path\to\extended_watchlist.python_scan_deferred.jsonl --scanned-jsonl-output path\to\extended_watchlist.python_scan_scanned.jsonl --summary-output path\to\extended_watchlist.python_scan_summary.json --markdown-output path\to\extended_watchlist.python_scan_report.md
```

Use the markdown scan report as the quick human review artifact after overnight
runs. It shows the scan totals, cache/fetch health, provider errors, skipped
symbols, the top passed candidates, deferred names, and the next evidence
enrichment command. The JSON and markdown reports also include an enrichment
readiness call. If fundamentals coverage is below
`--min-coverage-percent-for-enrichment` (default `80`), the recommendation is
`continue_fundamentals_cache_fill`; once coverage clears the threshold, it is
`run_evidence_enrichment_on_passed`.

The same report shows remaining cache-fill work. `Remaining fetches` is the
number of scanned symbols still missing fundamentals; `estimated runs remaining`
uses the current `--fetch-limit` to estimate how many more repeat runs are
needed before that specific watchlist slice is fully covered.

The preferred operator command combines source loading, watchlist export, cache
fill, Python first-pass scan, and markdown reporting into one artifact folder:

```powershell
python scripts/longterm_extended_universe_first_pass.py --source-url https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt --source nasdaq_listed --watchlist-limit 100 --batch-size 10 --provider yfinance --fundamentals-cache path\to\extended_fundamentals_cache.json --fetch-limit 25 --top-percent 10 --min-pass-count 5 --max-pass-count 20 --min-coverage-percent-for-enrichment 80 --output-dir path\to\extended_universe_first_pass
```

For unattended overnight-style work, use the research automation campaign
wrapper. It advances a campaign folder through universe preparation,
fundamentals cache fill, Python first-pass ranking, optional evidence
campaigning, and a deterministic committee research queue. It writes
`campaign_state.json` plus append-only `campaign_events.jsonl` so the command
can be resumed safely.

```powershell
python scripts/longterm_research_automation_campaign.py --source-url https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt --source nasdaq_listed --campaign-dir path\to\research_campaign --watchlist-limit 3042 --run-until scan_ready --max-fundamental-fetches 500 --fundamental-fetch-chunk-size 500
python scripts/longterm_research_automation_campaign.py --source-url https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt --source nasdaq_listed --campaign-dir path\to\research_campaign --resume --run-until evidence_ready --max-fundamental-fetches 500 --polygon-news --skip-grok --evidence-batch-size 25 --max-evidence-batches 2 --rate-limit-batch-size 5 --rate-limit-pause-seconds 69 --campaign-batch-pause-seconds 69
python scripts/longterm_research_automation_campaign.py --source-url https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt --source nasdaq_listed --campaign-dir path\to\research_campaign --resume --run-until research_queue_ready --max-fundamental-fetches 500 --polygon-news --skip-grok --evidence-batch-size 25 --selection-top-percent 20 --selection-min-count 10 --selection-max-count 50 --rate-limit-batch-size 5 --rate-limit-pause-seconds 69 --campaign-batch-pause-seconds 69
```

Default behavior is dry-run and research-only. The automation command does not
submit paper or live orders. Paid synthesis remains explicit: use
`--perplexity-research` for broad Sonar-backed catalyst/article enrichment, or
`--xai-grok` only for smaller high-value enrichment batches. If no research
provider flag is supplied, the automation uses skip-Grok evidence enrichment as
the safer free/cached default.

Example explicit Perplexity campaign run after the Python first-pass scan is
ready:

```powershell
python scripts/longterm_research_automation_campaign.py --source-url https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt --source nasdaq_listed --campaign-dir path\to\research_campaign --resume --run-until research_queue_ready --max-fundamental-fetches 500 --polygon-news --perplexity-research --perplexity-search-context-size low --perplexity-credits-purchased-to-date 12 --evidence-batch-size 10 --max-evidence-batches 1 --selection-top-percent 20 --selection-min-count 10 --selection-max-count 50 --rate-limit-batch-size 5 --rate-limit-pause-seconds 69 --campaign-batch-pause-seconds 69
```

When the campaign reaches `research_queue_ready`, the selected committee queue
is written under `research_selection\`:

- `research_queue_selected.json` / `.jsonl`: the evidence-rich idea batch for
  CGH committee research.
- `research_queue_ranked_all.json` / `.jsonl`: the full scored backlog in rank
  order, including selected and deferred names.
- `research_queue_deferred.json` / `.jsonl`: scored names below the selected
  relative slice.
- `research_queue_summary.json`: formula version, counts, selected output path,
  and protected-symbol skip information.
- `research_queue_report.md`: human-readable top queue and defer reasons.

The selection stage is Python-only and relative. It uses the deterministic
quality-growth scorecard, valuation/safety discipline, article evidence,
earnings context, evidence-brief completeness, and warning penalties. It does
not place trades, does not write the decision journal, and hard-skips protected
symbols such as `FXAIX`.

The combined workflow writes `extended_watchlist_ideas.json`,
`python_scan_passed.json`, `python_scan_deferred.json`,
`python_scan_scanned.json`, matching `.jsonl` sidecars for the passed/deferred/
scanned per-symbol records, `python_scan_report.md`, and
`extended_universe_first_pass_summary.json` in the chosen output directory.
Use JSON as the canonical nested artifact for downstream scripts, and JSONL for
large-universe inspection, streaming, diffs, and resume/debug tooling. Increase
`--fetch-limit` over repeated runs, or remove it for a deliberate long run once
you are ready to fill the entire cache.

Then run the evidence pipeline on the Python-scan survivors before committee
research:

```powershell
python scripts/longterm_evidence_enrichment_pipeline.py --idea-batch path\to\extended_watchlist.python_scan_passed.json --fundamentals-provider yfinance --polygon-news --news-cache-path path\to\polygon_news_cache.json --rate-limit-batch-size 5 --rate-limit-pause-seconds 66 --output path\to\extended_watchlist.evidence_ready.json --summary-output path\to\extended_watchlist.evidence_summary.json
```

For larger survivor slices, prefer the campaign wrapper. It runs the same
evidence pipeline in resumable batches, writes per-batch input/output/summary
files, and continuously refreshes combined JSON and JSONL outputs. Use
`--max-batches` for a bounded smoke or to advance an overnight run in chunks;
use `--resume` to skip batches already completed in the campaign folder. When
using Polygon's free-tier rolling limit, keep both provider and campaign batch
pauses enabled so consecutive campaign batches do not start inside the same
one-minute request window.

```powershell
python scripts/longterm_evidence_enrichment_campaign.py --idea-batch path\to\extended_watchlist.python_scan_passed.json --fundamentals-provider yfinance --polygon-news --news-cache-path path\to\polygon_news_cache.json --skip-grok --batch-size 25 --max-batches 1 --rate-limit-batch-size 5 --rate-limit-pause-seconds 69 --campaign-batch-pause-seconds 69 --output-dir path\to\evidence_campaign
python scripts/longterm_evidence_enrichment_campaign.py --idea-batch path\to\extended_watchlist.python_scan_passed.json --fundamentals-provider yfinance --polygon-news --news-cache-path path\to\polygon_news_cache.json --xai-grok --batch-size 25 --resume --rate-limit-batch-size 5 --rate-limit-pause-seconds 69 --campaign-batch-pause-seconds 69 --output-dir path\to\evidence_campaign
```

To select a committee queue from an already completed evidence campaign without
rerunning earlier stages:

```powershell
python scripts/longterm_research_selection.py --evidence-file path\to\evidence_campaign\campaign_enriched.json --output-dir path\to\research_selection --campaign-id extended_universe_YYYYMMDD --top-percent 20 --min-count 10 --max-count 50
```

Before sending a selected queue to the committee, run source reconciliation.
This compares wide-universe selections against optional comparison lists such
as Motley Fool coverage or symbols recently researched by the journal. Overlap
is allowed and often useful: a symbol that appears in both the broad universe
and Motley Fool should not be dropped. Instead, the row is annotated with
`source_convergence`, source notes, and a suggested research mode so the
committee can treat it as either fresh research or an update to an existing
thesis.

```powershell
python scripts/longterm_research_queue_reconciliation.py --research-queue path\to\research_selection\research_queue_selected.json --comparison-source motley_fool=path\to\fool_evidence_ready.json --recent-symbols-file path\to\recent_researched_symbols.json --output-dir path\to\committee_preflight --batch-size 5
```

The preflight writes `research_queue_reconciled.json` / `.jsonl`,
`research_queue_reconciliation_summary.json`,
`research_queue_reconciliation_report.md`, a `committee_batches\` folder, and a
`research_campaign_manifest.json`. Use those committee batches, in order, for
the paced CGH research campaign. This keeps overlap auditable without letting
duplicate sources inflate an idea into an automatic buy.

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

Future roadmap: add Kronos as an optional local market-language pass after the
scheduler/no-submit loop is stable. Kronos should not block scheduler readiness
and should not be wired to broker actions first. The planned sequence is:

1. Clone and smoke-test Kronos locally against 2-3 saved daily/weekly OHLCV
   histories, using yfinance/Polygon/Alpaca read data or cached bar artifacts.
2. Save a compact JSON signal per symbol, such as expected-return range,
   volatility regime, trend divergence, confidence, and warnings.
3. Use that signal before deep enrichment to prioritize which top first-pass
   candidates deserve Perplexity/Grok and committee spend.
4. Reuse the same signal in the daily current-position scan so unusual
   price/volume regime changes can queue off-schedule LLM review when the
   thesis may need attention.
5. Only after side-by-side validation, surface a compact Kronos context block
   to `decision_6`; keep `decision_4` routine decisions cheap unless the signal
   is conflicting or position impact is large.

Kronos is advisory. It must not create order intents, override active rules,
sell/rebalance positions by itself, bypass `FXAIX` protection, or replace the
buy-promotion, benchmark, scheduler, and Stage 6B execution gates.

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

Daily portfolio/watchlist news monitoring is separate from broad enrichment.
It should be cheap and deterministic: read the current portfolio, optional
watchlist/evidence ideas, and cached/snapshot news rows; then write an
`enrichment_needed_queue` only for high-signal articles. Queue rows are review
triggers, not trade intents. They keep `order_submission_enabled=false` and
`llm_escalation_allowed=false` by default so the later scheduler can decide
whether deeper enrichment is justified. Use `--published-after` for daily
cadence so the monitor only counts articles at or after the previous watermark;
articles without parseable timestamps are retained rather than silently
dropped.

```powershell
python scripts/longterm_portfolio_news_monitor.py --portfolio-state path\to\portfolio.json --watchlist-ideas path\to\research_queue_selected.json --snapshot-file path\to\raw_news.json --journal-db path\to\journal.db --output path\to\portfolio_news_monitor.json --as-of-date 2026-05-06 --json
```

For live daily use, first refresh or reuse the same Polygon/news cache that the
evidence pipeline uses, then feed the symbol-keyed cache/snapshot into this
monitor. The monitor itself does not call Perplexity, Grok, Alpaca, or any
broker. When the scheduler runs it, the report is written to
`run_00N\portfolio_news_monitor.json`, ingested by the no-submit pipeline, and
durably timestamped in scheduler policy-state as `last_news_monitor_at`.
The pipeline exposes monitor counts under
`artifact_rollup.portfolio_news_monitor`, including queue count, high-impact
count, review-trigger count, affected symbols, and top triggers.
It also writes `portfolio_news_followup_ideas.json`, a grouped idea batch that
is compatible with the research-packet intake path. Treat that file as a
future bounded enrichment/review input, not as permission to call LLMs or change
account actions automatically.
When you are ready to stage those follow-ups for later committee review, add
`--portfolio-news-followup-batches` to the no-submit pipeline or scheduler
preset. That only splits the validated follow-up ideas into normal research
batch JSON files and records `last_followup_batch_split_at`; it does not run
committee agents, call paid providers, mutate action plans, or submit orders.
When the operator intentionally wants committee review of those follow-up
batches, add the separate capped runner flags. This journals no-submit
committee decisions from at most the requested number of pending batches, but
still does not refresh final account actions or authorize broker orders:

```powershell
python scripts/longterm_research_to_paper_pipeline.py --output-dir path\to\pipeline_artifacts --action-plan path\to\account_action_plan.json --portfolio-state path\to\portfolio.json --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --portfolio-news-followup-batch-dir path\to\portfolio_news_followup_batches --run-portfolio-news-followup-committee-batches --portfolio-news-followup-max-batches 1 --skip-price-map --json
```

Use `--portfolio-news-followup-agent-preset decision_6` only for unusually
complex holdings or large possible portfolio implications. The default remains
`decision_4` so daily news follow-up review does not become an expensive hidden
LLM loop. Final-planning refresh remains a separate explicit action after the
operator reviews the new committee decisions.

After a capped follow-up committee run, the pipeline rollup and pipeline-health
report expose reviewed follow-up symbols, decision IDs, reviewed count, and the
next safe action
`inspect_portfolio_news_followup_reviews_before_final_planning_refresh`. The
localhost dashboard displays the reviewed count and follow-up step from
`/api/pipeline-health.json`. Treat that as an inspection checkpoint: review the
newly journaled follow-up decisions first, then run a separate final-planning
refresh if the decisions should affect buy-promotion, account planning, or
paper-readiness artifacts.

For broad universe work, prefer overnight batches over paid speed upgrades.
Polygon's free-tier cadence is acceptable when requests are paced in groups of
five with a little more than one minute of pause, cached, and resumable; a
20-minute enrichment job is fine for long-term research if it avoids unnecessary
recurring spend.

If Polygon's free tier is too restrictive or its structured feed is thin for
long-tail names, use Perplexity Sonar for broad research enrichment and reserve
Grok 4.3 for final committee decisions. Perplexity should still return
source-backed article/catalyst context, not unsourced rankings.

Offline/snapshot mode for development:

```powershell
python scripts/longterm_grok_research_enrichment.py --idea-batch path\to\research_ideas.earnings_enriched.json --facts-file path\to\finnhub_facts.json --snapshot-file path\to\grok_snapshots.json --output path\to\research_ideas.grok_enriched.json
```

Live Perplexity mode, when `PERPLEXITY_API_KEY` is configured. This is the
preferred broad-enrichment mode after the Grok 4.1 fast deprecation:

```powershell
python scripts/longterm_grok_research_enrichment.py --idea-batch path\to\research_ideas.earnings_enriched.json --facts-file path\to\finnhub_facts.json --perplexity-research --output path\to\research_ideas.research_enriched.json --limit 25
```

Live xAI mode, when `XAI_API_KEY` is configured. Reserve this for smaller,
high-value enrichment batches or direct decision support:

```powershell
python scripts/longterm_grok_research_enrichment.py --idea-batch path\to\research_ideas.earnings_enriched.json --facts-file path\to\finnhub_facts.json --output path\to\research_ideas.grok_enriched.json --limit 5
```

For repeatable batch work, use the combined evidence pipeline instead of
hand-chaining each enrichment command. It runs the same stages in order:
fundamentals, relevant news, latest earnings, deterministic scorecard, optional
Grok catalyst/article synthesis, and final versioned `evidence_brief` creation.
This is the preferred path when expanding a small smoke run into a wider Motley
Fool, S&P 500, or custom-universe research batch.

Snapshot/offline example:

```powershell
python scripts/longterm_evidence_enrichment_pipeline.py --idea-batch path\to\research_ideas.json --fundamentals-snapshot-file path\to\fundamentals_raw.json --news-snapshot-file path\to\raw_news.json --grok-snapshot-file path\to\grok_snapshots.json --output path\to\research_ideas.evidence_ready.json --summary-output path\to\evidence_summary.json
```

Live/provider example with free-tier pacing:

```powershell
python scripts/longterm_evidence_enrichment_pipeline.py --idea-batch path\to\research_ideas.json --fundamentals-provider yfinance --polygon-news --news-cache-path path\to\polygon_news_cache.json --xai-grok --limit 10 --rate-limit-batch-size 5 --rate-limit-pause-seconds 66 --output path\to\research_ideas.evidence_ready.json --summary-output path\to\evidence_summary.json
```

The pipeline still does not create decisions or orders. Feed its output into
`run_longterm_cycle.py --idea-batch ...` when the enriched names are ready for
committee review.

When `relevant_news` is present, Grok enrichment should produce
`article_evidence_summaries` for the strongest primary-company articles. These
summaries are snippet-grounded: they summarize only the article title, provider
summary/snippet, source, date, URL, relevance, and impact category already in
the enrichment payload. They are useful for the research committee, but they are
not proof that the full article page was opened or read.

After the committee produces first-pass `BUY` / `ADD` rows, run them through the
buy-promotion review gate before treating them as account-planning candidates.
The gate checks protected symbols, whether the symbol is already held,
confidence, positive suggested size, valuation context, margin-of-safety
support, permanent-capital-loss flags, staged-entry sizing, normalized-earnings
quality, and whether the packet has a versioned evidence brief with
article-level support. Promotion output is operator-facing only:
`ACTIONABLE_BUY` means "ready for the next dry-run planning stage," not "submit
an order." Weak or thin-evidence names remain in watchlist or existing-position
review states until more evidence is collected. Weak margin-of-safety support is
handled as `WATCHLIST_PENDING_CONFIRMATION`, not as a hard broker blocker, so the
system gets Graham-style price discipline without starving the research funnel.
High permanent-loss risk such as overpayment, leverage, refinancing pressure,
weak cash conversion, dilution, accounting quality concerns, or business
disruption also becomes a confirmation follow-up. The promotion review records a
defensive/enterprising/speculative label and a staged-entry hint; those fields
are advisory and do not override Stage 6B eligibility checks.

Render the current promotion report from a journal and portfolio snapshot:

```powershell
python scripts/longterm_buy_promotion.py --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --output path\to\buy_promotion.md
python scripts/longterm_buy_promotion.py --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --json --output path\to\buy_promotion.json
```

Account-action plans and next-actions also consult the promotion review. A
first-pass `BUY` that is missing article evidence, has low confidence, carries
an enrichment warning, has explicit low margin-of-safety support, or carries
high permanent-loss risk becomes a review/enrichment task with
`order_intent=NONE` instead of a dry-run buy. It is also excluded from rebalance
targets until it clears promotion. This keeps the sequence explicit:
research committee says "interesting buy" -> promotion gate says "actionable
enough" -> account planning sizes the candidate -> Stage 6B eligibility
revalidates again before any supervised paper submission.

When promotion says a BUY is actionable but the Graham staged-entry review says
`starter_position`, account-action planning uses the starter percentage for the
planned dry-run trade value and target value. This keeps promising but
moderate-margin names in the plan without letting a full-size target slip in as
if the margin of safety were already compelling. Missing margin detail by
itself does not shrink older clean BUY rows; it must be an explicit moderate
margin or permanent-loss-risk signal.

For existing holdings, next-actions applies the Graham/Mr. Market lens to large
quote moves. A material drawdown becomes a `mr_market_drawdown_review` asking
whether the quote is a bargain or a broken thesis. A material rally becomes a
`mr_market_rally_review` asking whether valuation, margin of safety, and
trailing-profit protection need review. These prompts never sell, trim, or add
automatically. The position-review queue applies the same lens directly to
current portfolio holdings, so daily scheduler artifacts can surface
`sell_or_add_after_thesis_check` and `trim_or_trailing_profit_review` prompts
even when the latest journal row is not itself a SELL or REDUCE decision.

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

For the first clean-account smoke, leave account cleanliness strict. After the
paper account intentionally holds positions from a supervised test, add
`--allow-existing-paper-positions` only when the exported cash still matches the
expected baseline. This converts known existing paper holdings into an explicit
ongoing-portfolio warning instead of a blocker; cash mismatch still blocks.

Generate an ordered Monday paper-trading runbook:

```powershell
python scripts/longterm_paper_runbook.py --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --portfolio-state path\to\portfolio.json --action-plan path\to\account_action_plan.json --output-dir path\to\paper_artifacts --profile-config path\to\profile.json --expected-cash 74000 --report-output path\to\paper_artifacts\paper_runbook.json
python scripts/longterm_paper_runbook.py --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --portfolio-state path\to\portfolio.json --action-plan path\to\account_action_plan.json --output-dir path\to\paper_artifacts --profile-config path\to\profile.json --expected-cash 74000 --report-output path\to\paper_artifacts\paper_runbook.json --json
```

The runbook is a deterministic checklist and command generator only. It does
not read Alpaca, write ledgers, or submit orders. Save it with `--report-output`
so later artifact checks can inspect whether the supervised submit command is
still redacted. By default, the supervised submit command is redacted so the
operator cannot accidentally copy it before reviewing the saved preflight
artifacts. Reveal it only after review:

```powershell
python scripts/longterm_paper_runbook.py --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --portfolio-state path\to\portfolio.json --action-plan path\to\account_action_plan.json --output-dir path\to\paper_artifacts --profile-config path\to\profile.json --expected-cash 74000 --include-submit-command
python scripts/longterm_paper_runbook.py --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --portfolio-state path\to\portfolio.json --action-plan path\to\account_action_plan.json --output-dir path\to\paper_artifacts --profile-config path\to\profile.json --expected-cash 74000 --scheduler-review-bundle path\to\scheduler_review_bundle.json --include-submit-command
```

Even when revealed, the supervised submit command still requires
`--confirm-paper-submit SUPERVISED_PAPER_BUY_ONLY`.
When `--profile-config` is supplied, the generated snapshot, workflow-smoke,
and supervised-submit commands reuse the same paper profile. When
`--scheduler-review-bundle` is supplied, the revealed supervised-submit command
also carries that bundle into the Stage 6B pre-submit validator; the redacted
runbook still stores the path as metadata without printing a runnable submit
command. After any future
supervised paper buy is observed, the runbook includes a manual cleanup reminder
to sell or cancel the temporary paper position in Alpaca before the next run.

Build the no-submit research-to-paper preflight pipeline from a saved account
action plan:

```powershell
python scripts/longterm_research_to_paper_pipeline.py --output-dir path\to\pipeline_artifacts --action-plan path\to\account_action_plan.json --portfolio-state path\to\portfolio.json --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --price-map path\to\price_map.json --skip-price-map --print-plan-only --json
python scripts/longterm_research_to_paper_pipeline.py --output-dir path\to\pipeline_artifacts --action-plan path\to\account_action_plan.json --portfolio-state path\to\portfolio.json --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --price-map path\to\price_map.json --skip-price-map --expected-cash 74000 --summary-output path\to\pipeline_artifacts\pipeline_summary.json --json
python scripts/longterm_research_to_paper_pipeline.py --output-dir path\to\pipeline_artifacts --action-plan path\to\account_action_plan.json --portfolio-state path\to\portfolio.json --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --expected-cash-from-portfolio-state --allow-existing-paper-positions --summary-output path\to\pipeline_artifacts\pipeline_summary.json --json
python scripts/longterm_research_to_paper_pipeline.py --output-dir path\to\pipeline_artifacts --portfolio-news-monitor path\to\portfolio_news_monitor.json --action-plan path\to\account_action_plan.json --portfolio-state path\to\portfolio.json --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --expected-cash-from-portfolio-state --allow-existing-paper-positions --summary-output path\to\pipeline_artifacts\pipeline_summary.json --json
```

The pipeline wrapper is the scheduler-ready command seam. It composes existing
scripts rather than reimplementing research, benchmark, review, promotion, or
paper safety logic. V1 starts from a saved account action plan, filters it
through `longterm_action_plan_filter.py`, builds whole-share paper preview and
readiness artifacts, generates a redacted runbook, checks the saved runbook
artifacts, and writes one `pipeline_summary.json`. It never executes or prints a
submit command and rejects any planned stage containing `--submit-paper-orders`.
Use `--print-plan-only` when wiring a scheduler or reviewing the exact command
order without executing any stage.

The optional `--allow-existing-paper-positions` flag is for ongoing paper
portfolio refreshes after known paper buys exist. It is intentionally not the
default because the initial supervised paper-smoke path should prove the account
is clean before first submission.
For ongoing paper scheduler runs, pair it with
`--expected-cash-from-portfolio-state` so the runbook/readiness cash check uses
the fresh `cash` field from the account snapshot instead of yesterday's hardcoded
cash number. Do not combine that flag with explicit `--expected-cash`; the CLI
fails closed when both cash sources are supplied.

The same wrapper can optionally prepend existing research/planning stages before
the paper preflight. This is still no-submit orchestration: committee batches run
through `run_longterm_cycle.py`, a final empty idea-batch cycle refreshes account
planning from the journal, and then the normal paper-readiness stages run:

```powershell
python scripts/longterm_research_to_paper_pipeline.py --output-dir path\to\pipeline_artifacts --committee-batch-dir path\to\committee_batches --final-planning-refresh --final-planning-timeout-seconds 900 --action-plan path\to\account_action_plan.json --portfolio-state path\to\portfolio.json --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --market-regime-file path\to\market_regime.json --motley-fool-config path\to\disabled_fool_config.json --planning-capital-from-portfolio-state --skip-price-map --expected-cash-from-portfolio-state --allow-existing-paper-positions --json
```

Use this expanded form only when the saved committee batch files and portfolio
snapshot are already prepared. `--planning-capital-from-portfolio-state`
derives final-planning available cash from `portfolio.cash` and active sleeve
value from cash plus non-protected holdings, so `FXAIX`/protected holdings stay
outside active deployment sizing. It does not perform broad-universe discovery,
fundamentals cache filling, Polygon/Grok enrichment, or broker submission by
itself; use the upstream research-campaign bridge below when the scheduler needs
to prepare those research batches first.
Use `--final-planning-timeout-seconds` for scheduler work. If final planning
exceeds the timeout, the wrapper fails closed with
`stage_timeout:final_planning_refresh`, writes the stage log, stops downstream
stages, and does not mark durable final-planning cadence as complete.

The wrapper can also prepend a broad-universe research campaign stage. This is
the scheduler-friendly bridge for overnight research preparation: it runs the
existing research automation campaign through `research_queue_ready`, then
splits the selected research queue into committee batch files. The generated
`committee_batch_dir` is recorded in `pipeline_summary.json`; run the wrapper
again with that directory via `--committee-batch-dir` when you are ready to spend
committee/LLM calls.

```powershell
python scripts/longterm_research_to_paper_pipeline.py --output-dir path\to\pipeline_artifacts --research-source-url https://example.com/listings.txt --research-source nasdaq_trader --research-campaign-dir path\to\research_campaign --research-resume --research-run-until research_queue_ready --research-watchlist-limit 305 --research-top-percent 10 --research-max-fundamental-fetches 500 --polygon-news --skip-grok --research-rate-limit-batch-size 5 --research-rate-limit-pause-seconds 69 --research-batch-size 5 --action-plan path\to\account_action_plan.json --portfolio-state path\to\portfolio.json --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --price-map path\to\price_map.json --skip-price-map --print-plan-only --json
```

For paid broad enrichment in that same no-submit bridge, replace `--skip-grok`
with `--perplexity-research` and pass the same Perplexity cost controls used by
the automation command:

```powershell
python scripts/longterm_research_to_paper_pipeline.py --output-dir path\to\pipeline_artifacts --research-source-url https://example.com/listings.txt --research-source nasdaq_trader --research-campaign-dir path\to\research_campaign --research-resume --research-run-until research_queue_ready --research-watchlist-limit 305 --research-top-percent 10 --research-max-fundamental-fetches 500 --polygon-news --perplexity-research --perplexity-search-context-size low --perplexity-credits-purchased-to-date 12 --research-rate-limit-batch-size 5 --research-rate-limit-pause-seconds 69 --research-batch-size 5 --action-plan path\to\account_action_plan.json --portfolio-state path\to\portfolio.json --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --price-map path\to\price_map.json --skip-price-map --print-plan-only --json
```

This upstream bridge still does not submit orders. `--xai-grok` and
`--perplexity-research` must be explicit; otherwise the campaign can run in
cheaper provider-free or cached modes. The batch split reads
`research_campaign\research_selection\research_queue_selected.json` and writes
committee-ready batch files under `research_campaign\committee_batches`.

If you want one scheduler command to continue from selected queue into committee
research, add `--run-generated-committee-batches`. This inserts a no-submit
runner stage after the batch split. The runner discovers generated batch files
at runtime, executes `run_longterm_cycle.py` once per batch in sorted order,
writes `generated_committee_batches\committee_batch_run_summary.json`, and uses
artifact-based `--resume` by default so scheduler retries do not rerun already
completed batch cycles from the same output directory.
The committee runner writes that summary incrementally after each completed
batch, so a timeout or interrupted long research cadence can resume from the
last persisted passed batch instead of starting over.
For scheduler-sized bites, add `--generated-committee-max-batches N`. A partial
run exits cleanly with `status=partial`, records `remaining_count`, and the
durable scheduler policy state does not mark `last_full_research_at` until the
generated committee summary reaches `status=completed` with no remaining
batches.

```powershell
python scripts/longterm_research_to_paper_pipeline.py --output-dir path\to\pipeline_artifacts --research-source-file path\to\nasdaqtrader.txt --research-source nasdaq_trader --research-campaign-dir path\to\research_campaign --research-resume --research-run-until research_queue_ready --run-generated-committee-batches --generated-committee-max-batches 1 --final-planning-refresh --final-planning-timeout-seconds 900 --market-regime-file path\to\market_regime.json --planning-capital-from-portfolio-state --action-plan path\to\account_action_plan.json --portfolio-state path\to\portfolio.json --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --price-map path\to\price_map.json --skip-price-map --expected-cash-from-portfolio-state --allow-existing-paper-positions --json
```

Use `--no-generated-committee-resume` only when intentionally rebuilding a
fresh output directory or after manually clearing the previous runner summary.

Inspect an existing pipeline summary without rerunning any stage:

```powershell
python scripts/longterm_pipeline_health.py --pipeline-summary path\to\pipeline_artifacts\pipeline_summary.json --report-output path\to\pipeline_artifacts\pipeline_artifact_health.json --require-artifact paper_preview --require-artifact workflow_smoke --json
```

The health command is read-only. It reloads the `artifact_paths` recorded in
`pipeline_summary.json`, builds scheduler/dashboard counts for research
selection, committee batches, action-plan intents, paper preview, workflow
smoke, portfolio-news monitor queues, and operator status, then reports missing
or malformed files. A `ready` report means the saved artifacts are coherent
enough for dashboard/scheduler inspection; it is not authorization to submit
orders.

Run the same no-submit pipeline as a bounded recurring scheduler loop:

```powershell
$snapshot = "python scripts/longterm_alpaca_paper_snapshot.py --portfolio-state-output {portfolio_state}"
$pipeline = "python scripts/longterm_research_to_paper_pipeline.py --output-dir {pipeline_output_dir} --research-source-file path\to\nasdaqtrader.txt --research-source nasdaq_trader --research-campaign-dir path\to\research_campaign --research-resume --research-run-until research_queue_ready --run-generated-committee-batches --generated-committee-max-batches 1 --final-planning-refresh --market-regime-file path\to\market_regime.json --planning-capital-from-portfolio-state --action-plan path\to\account_action_plan.json --portfolio-state {portfolio_state} --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --allow-existing-paper-positions --expected-cash-from-portfolio-state --json"
$policy = "python scripts/longterm_pipeline_scheduler_policy.py --rules-path {rules_path} --policy-state {scheduler_policy_state} --state-output {scheduler_policy_state} --market-regime path\to\market_regime.json --journal-db path\to\journal.db --pipeline-scheduler-summary {scheduler_summary} --pipeline-summary {pipeline_summary} --report-output {scheduler_policy} --json"
$refresh = "python scripts/longterm_paper_account_refresh.py --journal-db path\to\journal.db --action-plan path\to\account_action_plan.json --paper-ledger-db path\to\paper_ledger.db --pipeline-summary {pipeline_summary} --output-dir {account_refresh_output_dir} --dashboard-manifest-output path\to\dashboard_manifest.json --json"
$verify = "python scripts/longterm_pipeline_scheduler_verify.py --pipeline-scheduler-summary {scheduler_summary} --policy-state {scheduler_policy_state} --require-resource-bounded --require-policy-timestamp last_no_submit_preflight_at --require-policy-timestamp last_account_refresh_at --report-output {post_run_verification} --json"
python scripts/longterm_pipeline_scheduler.py --run-once --output-dir path\to\pipeline_scheduler_runs --rules-path path\to\active_rules.txt --pre-pipeline-refresh-command-template $snapshot --pipeline-command-template $pipeline --scheduler-policy-command-template $policy --account-refresh-command-template $refresh --post-run-verification-command-template $verify --json
python scripts/longterm_pipeline_scheduler.py --max-runs 3 --interval-seconds 3600 --output-dir path\to\pipeline_scheduler_runs --rules-path path\to\active_rules.txt --pre-pipeline-refresh-command-template $snapshot --pipeline-command-template $pipeline --scheduler-policy-command-template $policy --account-refresh-command-template $refresh --post-run-verification-command-template $verify --json
```

For the standard ongoing paper-review loop, prefer the built-in safe preset so
the operator does not have to hand-maintain four long command templates:

These examples are shown from `ai_trader/trading_agent`. The same preset can
also be launched from the repo root as
`python ai_trader\trading_agent\scripts\longterm_pipeline_scheduler.py ...`.
The preset renders absolute script paths, normalizes user-supplied file paths,
and runs child commands from `ai_trader/trading_agent` so pipeline-internal
`scripts\...` stages resolve consistently.

```powershell
python scripts/longterm_pipeline_scheduler.py --preset ongoing-no-submit --run-once --output-dir path\to\pipeline_scheduler_runs --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --action-plan path\to\account_action_plan.json --profile-config path\to\roth_ira_profile.json --market-regime-file path\to\market_regime.json --final-planning-refresh --final-planning-timeout-seconds 900 --planning-capital-from-portfolio-state --expected-cash-from-portfolio-state --allow-existing-paper-positions --json
python scripts/longterm_pipeline_scheduler.py --preset ongoing-no-submit --max-runs 3 --interval-seconds 3600 --output-dir path\to\pipeline_scheduler_runs --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --action-plan path\to\account_action_plan.json --profile-config path\to\roth_ira_profile.json --market-regime-file path\to\market_regime.json --final-planning-refresh --final-planning-timeout-seconds 900 --planning-capital-from-portfolio-state --expected-cash-from-portfolio-state --allow-existing-paper-positions --json
python scripts/longterm_pipeline_scheduler.py --preset ongoing-no-submit --run-once --output-dir path\to\pipeline_scheduler_runs --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --action-plan path\to\account_action_plan.json --profile-config path\to\roth_ira_profile.json --portfolio-news-monitor --portfolio-news-snapshot-file path\to\raw_news_by_symbol.json --portfolio-news-watchlist-ideas path\to\research_queue_selected.json --portfolio-news-published-after 2026-05-01 --final-planning-refresh --final-planning-timeout-seconds 900 --planning-capital-from-portfolio-state --expected-cash-from-portfolio-state --allow-existing-paper-positions --json
```

For repeatable local operation, copy
`longterm\configs\ongoing_no_submit_scheduler.example.json`, fill the local
artifact paths, and launch the same safe preset with:

```powershell
python scripts/longterm_pipeline_scheduler.py --config-file path\to\ongoing_no_submit_scheduler.local.json
```

Or render the local profile from the safe template with explicit overrides,
then validate it immediately without creating scheduler run folders:

```powershell
python scripts/longterm_scheduler_profile.py --output-profile path\to\ongoing_no_submit_scheduler.local.json --set output_dir=path\to\pipeline_scheduler_runs --set journal_db=path\to\journal.db --set ledger_db=path\to\paper_ledger.db --set action_plan=path\to\account_action_plan.json --set profile_config=path\to\roth_ira_profile.json --set summary_output=path\to\scheduler_profile_validation.json --set scheduler_config_validation=path\to\scheduler_profile_validation.json --enable allow_existing_paper_positions --enable expected_cash_from_portfolio_state --validate-after-write --json
```

After that validation payload is reviewed, render a no-submit run profile
instead of hand-editing `validate_config_only`:

```powershell
python scripts/longterm_scheduler_profile.py --template path\to\ongoing_no_submit_scheduler.local.json --output-profile path\to\ongoing_no_submit_scheduler.run.json --run-mode no-submit --set summary_output=path\to\pipeline_scheduler_summary.json --validate-after-write --json
```

The renderer refuses submit-capable keys and does not support
`submit_paper_orders` or `confirm_paper_submit`; supervised paper execution
remains a separate explicit path.

To prepare a Windows Task Scheduler registration without registering anything,
generate a review-only task-plan artifact from the reviewed no-submit run
profile:

```powershell
python scripts/longterm_scheduler_task_plan.py --profile-file path\to\ongoing_no_submit_scheduler.run.json --task-name LongTermTraderNoSubmit --start-time 09:35 --output path\to\scheduler_task_plan.json --json
```

The task-plan artifact includes the scheduler command plus `schtasks` and
PowerShell registration commands for manual review. It rejects validation-only
profiles and submit-capable profile keys. To show it on the dashboard, pass
`--scheduler-task-plan path\to\scheduler_task_plan.json` to the dashboard
manifest writer or read-only account refresh.

Before manually registering the Windows task, write a final handoff check that
confirms the profile validation, task plan, and dashboard manifest all point at
the same reviewed artifacts:

```powershell
python scripts/longterm_scheduler_handoff.py --scheduler-config-validation path\to\scheduler_profile_validation.json --scheduler-task-plan path\to\scheduler_task_plan.json --dashboard-manifest path\to\dashboard_manifest.json --output path\to\scheduler_handoff.json --json
```

The handoff check returns exit code `0` only when the chain is ready and no
artifact enables order submission.

The config file accepts an `args` object using the same argparse destination
names as the CLI, for example `journal_db`, `action_plan`, and
`allow_existing_paper_positions`. Unknown config keys fail closed so typos do
not silently drop scheduler controls. Explicit command-line scalar values can
override config values for one run.
Keep `validate_config_only=true` while testing a new local profile. Validation
resolves generated commands, checks the same no-submit command-template rules,
and prints bounded resource controls without creating scheduler run folders or
calling a broker. If `summary_output` is supplied, validation writes the same
JSON payload there so a dashboard/runbook/operator checklist can inspect the
profile without launching a cycle; that payload includes the resolved
`config_file` path for provenance. Remove `validate_config_only` from the local
profile, or set it to `false`, after the profile is reviewed:

```powershell
python scripts/longterm_pipeline_scheduler.py --config-file path\to\ongoing_no_submit_scheduler.local.json
```

To show the reviewed scheduler profile on the localhost dashboard, pass that
validation JSON into the manifest writer or read-only account/dashboard refresh:

```powershell
python scripts/longterm_operator_dashboard_server.py --manifest path\to\dashboard_manifest.json --write-manifest --write-manifest-only --action-plan path\to\account_action_plan.json --portfolio-state path\to\portfolio_state.json --scheduler-config-validation path\to\scheduler_profile_validation.json --json
python scripts/longterm_paper_account_refresh.py --profile-config path\to\roth_ira_profile.json --journal-db path\to\journal.db --action-plan path\to\account_action_plan.json --paper-ledger-db path\to\paper_ledger.db --output-dir path\to\account_refresh --scheduler-config-validation path\to\scheduler_profile_validation.json --scheduler-task-plan path\to\scheduler_task_plan.json --json
```

The same preset can also run a bounded upstream research cadence before the
paper-preflight chain. Keep paid/reasoning work capped; `--perplexity-research`
requires an explicit `--research-max-pass-count`, and generated committee
batches require `--generated-committee-max-batches`.

```powershell
python scripts/longterm_pipeline_scheduler.py --preset ongoing-no-submit --run-once --output-dir path\to\pipeline_scheduler_runs --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --action-plan path\to\account_action_plan.json --profile-config path\to\roth_ira_profile.json --research-source-file path\to\universe.csv --research-source manual_watchlist --research-campaign-dir path\to\research_campaign --research-resume --research-run-until research_queue_ready --research-max-pass-count 25 --research-evidence-batch-size 10 --research-max-evidence-batches 2 --perplexity-research --perplexity-search-context-size low --perplexity-credits-purchased-to-date 12 --run-generated-committee-batches --generated-committee-max-batches 1 --final-planning-refresh --final-planning-timeout-seconds 900 --planning-capital-from-portfolio-state --expected-cash-from-portfolio-state --allow-existing-paper-positions --json
```

The preset expands to the same safe stages as the manual template path: a fresh
Alpaca paper snapshot into `{portfolio_state}`, the no-submit
research-to-paper pipeline, the advisory scheduler-policy report, and a
read-only paper-account/dashboard refresh. It writes per-run
`dashboard_manifest.json` and `operator_dashboard_site` artifacts so the local
dashboard can refresh from the latest saved run. The preset still rejects
submit-capable fragments and never adds `--submit-paper-orders` or
`--confirm-paper-submit`.
When `--portfolio-news-monitor` is supplied, the preset also runs
`longterm_portfolio_news_monitor.py` after the fresh account snapshot and before
the pipeline. V1 requires `--portfolio-news-snapshot-file`; this keeps the
monitor cache/snapshot driven instead of inventing a live news provider inside
the scheduler. The rendered pipeline receives `--portfolio-news-monitor
{portfolio_news_monitor}`, and the post-run verifier requires
`last_news_monitor_at` in `scheduler_policy_state.json`.
If `--portfolio-news-followup-batches` is also supplied, the rendered pipeline
adds `--portfolio-news-followup-batches` and
`--portfolio-news-followup-batch-size`, then splits
`portfolio_news_followup_ideas.json` into
`portfolio_news_followup_batches\research-batch-*.json`. The verifier then
requires `last_followup_batch_split_at`, proving the deterministic handoff
completed. This is still an artifact-only step; running the generated batches
through committee review is a later, separately capped action.
If `--run-portfolio-news-followup-committee-batches` is also supplied, the
preset requires `--portfolio-news-followup-max-batches` and forwards the same
cap into the no-submit pipeline. The follow-up committee stage writes
`portfolio_news_followup_committee_batches\committee_batch_run_summary.json`,
records progress in `artifact_rollup.portfolio_news_monitor`, and the verifier
requires `last_followup_committee_at`. A capped run may legitimately finish
with `remaining_count > 0`; that means the batch runner stopped at the operator
cap, not that the scheduler should continue spending. The stage can journal
committee review decisions, but it does not run final planning, refresh
buy-promotion/account actions, or submit orders unless those other existing
gates are invoked explicitly.
It also appends a post-run `longterm_pipeline_scheduler_verify.py` command that
writes `run_00N\scheduler_cadence_verification.json` after the top-level
`pipeline_scheduler_summary.json` and `scheduler_policy_state.json` are
updated. If that verifier fails, the scheduler run is marked failed instead of
green.
When `--scheduler-review-bundle` is supplied, the preset also appends
`longterm_scheduler_review_bundle.py` after the post-run verifier. This option
requires `--position-review-queue` and a saved `--scheduler-handoff`, and it
writes `run_00N\scheduler_review_bundle\scheduler_review_bundle.json`,
`paper_submit_mode_plan.json`, and `dashboard_review_gates_manifest.json`.
The bundle runs only after the verifier exits `0`; verifier failures skip it,
and bundle failures mark the scheduler run failed with
`scheduler_review_bundle_command_failed`. The stage is read-only and keeps
order submission disabled.
When `--final-planning-refresh` is supplied, the preset forwards
`--final-planning-timeout-seconds`; if the operator omits it, the preset uses a
900-second default. The rendered `resource_controls` object records both
`final_planning_refresh` and `final_planning_timeout_seconds`, and an enabled
final-planning refresh without a timeout is treated as unbounded.

Each planned or completed scheduler run also includes a machine-readable
`resource_controls` object in `pipeline_scheduler_summary.json`. This summarizes
the visible research provider mode, paid-provider flag, research pass cap,
evidence batch cap, generated committee batch cap, and whether the rendered run
appears bounded. It deliberately reports `estimated_cost_usd` as `unknown`
unless a later stage provides actual usage; the field is a pre-run safety
surface, not a precise price quote.
The preset passes `{scheduler_summary}` into the read-only account/dashboard
refresh as `--pipeline-scheduler-summary`, so `/api/pipeline-health.json` and
the Safety / Preflight dashboard card can show provider/cap status after the
scheduler writes the final summary.
The advisory scheduler policy also reads the latest resource controls. Bounded
paid runs are allowed to proceed as advisory/scheduled work with a
`paid_research_provider_planned` warning, while unbounded paid/provider runs are
blocked into `resource_control_review` until the caps are fixed. Operator status
bundles and markdown include the same resource-control fields for pre-paper
review.

For quick scheduler-readiness smokes, prefer a saved action plan and omit
`--final-planning-refresh` unless you intentionally want a longer supervised
window for fresh account-action planning. The upstream research and
paper-preflight path is resumable and fast when it uses saved campaign
artifacts; the empty-batch final-planning refresh can be materially slower on
large journals and should be treated as its own bounded scheduler chunk.
Current proof point: a copied-artifact two-cycle watch under
`%TEMP%\longterm_scheduler_watch_20260507_173916` completed with no submitted
orders, pre-pipeline paper snapshots, no-submit pipeline runs, scheduler-policy
reports, post-run verifier reports, and dashboard/account refreshes all
returning exit code `0`.
Follow-up proof: `%TEMP%\longterm_scheduler_root_cwd_smoke_20260507_201015`
completed the same no-submit chain from the repo root after the preset was
hardened for absolute script paths and normalized child-command paths.

The recurring pipeline scheduler is a thin command orchestrator, not a new
trading authority. It validates the command templates before running them,
requires `--journal-db`, `--portfolio-state`, and the active rules file, injects
`--summary-output` plus `--rules-path` when omitted, writes isolated
`run_001`, `run_002`, ... folders, captures stdout/stderr, can optionally run a
pre-pipeline snapshot command that writes `{portfolio_state}` as
`run_00N\paper_portfolio_state.json`, writes `pipeline_artifact_health.json`
after each pipeline run, can optionally run the read-only scheduler-policy
command with the generated pipeline summary, and can then run the read-only
paper-account/dashboard refresh command. When a scheduler policy command is
supplied, the scheduler writes `{scheduler_policy}` as
`run_00N\scheduler_policy.json` and automatically passes that artifact to
`longterm_paper_account_refresh.py --scheduler-policy` unless the refresh
template already provides one. It rejects submit-capable fragments such as
`--submit-paper-orders`, `SUPERVISED_PAPER`, paper execution scripts, and shell
chaining separators. `order_submission_enabled` remains `false`; any future
paper submit remains a separate supervised Stage 6B command path.

Scheduler templates can also use `{scheduler_summary}` for the top-level
`pipeline_scheduler_summary.json` path and `{scheduler_policy_state}` for a
stable durable policy-state file in the scheduler output directory. That lets
the policy command read/write cadence memory across repeated runs without
hardcoding paths; on the first run, a missing optional policy-state or scheduler
summary file is treated as empty state. Templates can also use
`{dashboard_manifest}` and `{dashboard_site_output_dir}` for per-run dashboard
refresh output. Use `{portfolio_state}` in the pipeline template whenever
`--pre-pipeline-refresh-command-template` is supplied, so final-planning and
expected-cash checks read the fresh per-run paper snapshot.
After each completed scheduler cycle, the scheduler itself updates
`scheduler_policy_state.json` with successful account-refresh and no-submit
preflight timestamps. It marks `last_final_planning_at` only when the saved
pipeline summary completed both `final_planning_refresh` and
`extract_final_action_plan` with zero blockers. A timeout or partial extract
does not advance that timestamp.
If a post-run verifier template is supplied, the scheduler first writes the
provisional finalized summary and policy-state, then runs the verifier, records
its stdout/stderr and exit code on the run record, and writes the final summary
again. This is the recommended acceptance check for a bounded no-submit
simulator loop.

Recommended simulator cadence:
- Python account/price/regime refresh: every 15-60 minutes during market hours.
- Portfolio/watchlist news scan: daily, using deterministic providers and
  cached article relevance before any paid LLM.
- Deeper enrichment: weekly or event-triggered when fresh recommendations,
  thesis-breaking news, earnings, panic-regime changes, or review-due holdings
  appear.
- Committee LLM decisions: sparse and event-driven, normally `decision_4`;
  escalate to `decision_6` only for large sizing, new thesis, borderline
  valuation, complex sell/rebalance, or choppy macro decisions.

Verify a completed no-submit cadence run before treating it as scheduler-ready:

```powershell
python scripts/longterm_pipeline_scheduler_verify.py --pipeline-scheduler-summary path\to\pipeline_scheduler_summary.json --policy-state path\to\scheduler_policy_state.json --require-resource-bounded --require-final-planning-bound --require-policy-timestamp last_full_research_at --require-policy-timestamp last_no_submit_preflight_at --require-policy-timestamp last_account_refresh_at --require-policy-timestamp last_final_planning_at --report-output path\to\scheduler_cadence_verification.json --json
```

The verifier reads saved artifacts only. It blocks if scheduler or pipeline
submission is enabled, if any scheduler stage failed, if submit-capable command
fragments appear, if final planning is enabled without a timeout, if pipeline
blockers are present, if workflow smoke shows submitted orders, or if required
policy-state timestamps are missing. A `ready` report means the cadence run is
coherent for no-submit scheduler operation; it is still not authorization to
submit broker orders.

After a full cadence run finishes, optionally generate a post-cadence advisory
policy from the finalized scheduler summary and state:

```powershell
python scripts/longterm_pipeline_scheduler_policy.py --rules-path path\to\active_rules.txt --journal-db path\to\journal.db --policy-state path\to\scheduler_policy_state.json --pipeline-scheduler-summary path\to\pipeline_scheduler_summary.json --pipeline-summary path\to\run_001\pipeline_summary.json --market-regime path\to\market_regime.json --report-output path\to\post_cadence_scheduler_policy.json --json
```

This second policy read usually gives a cleaner "what next?" view than the
in-cycle policy artifact because it sees the finalized scheduler summary and
the account-refresh timestamp written by the scheduler.

When the scheduled no-submit path is refreshing an already-populated paper
portfolio, add `--allow-existing-paper-positions` to the pipeline command
template and `--expected-cash-from-portfolio-state` so cash cleanliness follows
the fresh paper snapshot. Add `--planning-capital-from-portfolio-state` whenever
the same command also performs `--final-planning-refresh`, so dry-run planning
sizes active deployment from the current non-protected paper sleeve instead of a
stale constant. Let the pipeline generate a fresh price map unless you have a
complete explicit map for both stock candidates and parking symbols. In ongoing
paper mode, the workflow smoke treats duplicate-only execution audit rows as
already-handled submissions so idempotency remains enforced without making
post-submit refresh cycles fail. Other execution blockers remain blockers.

Build an advisory scheduler cadence policy before choosing which safe command
class to run:

```powershell
python scripts/longterm_pipeline_scheduler_policy.py --rules-path path\to\active_rules.txt --policy-state path\to\scheduler_policy_state.json --state-output path\to\scheduler_policy_state.json --market-regime path\to\market_regime.json --journal-db path\to\journal.db --pipeline-scheduler-summary path\to\pipeline_scheduler_runs\pipeline_scheduler_summary.json --pipeline-summary path\to\current_pipeline_summary.json --report-output path\to\scheduler_policy.json --json
```

This policy artifact is read-only and advisory. It never submits orders and does
not switch scheduler commands by itself. It recommends one of:
`account_refresh_only`, `no_submit_preflight`, `full_research_cycle`,
`thesis_review_refresh`, `benchmark_reassessment`, or
`panic_regime_reassessment`. Panic output means reassess regime/next-actions in
dry-run mode; it is not liquidation authority.

The policy requires an explicit active rules file and records its SHA-256. If a
previous `scheduler_policy_state.json` contains a different
`active_rules_sha256`, the output warns with `active_rules_changed`. Optional
journal input reuses the existing `ReviewStatusBuilder` and benchmark guard so
stale/broken theses or FXAIX-underperformance can raise cadence urgency before
new research or buys.

The policy output includes `cadence_recommendations` with due flags for account
refresh, no-submit preflight, full research, and final planning. Final planning
becomes due when active rules change, when the last successful final plan is
older than the latest full research completion, or when the final-planning
cadence expires. This recommendation is still advisory and no-submit.

When `--pipeline-scheduler-summary` is supplied, the policy can infer the latest
successful no-submit preflight and account-refresh timestamps from completed
scheduler runs. A separate `--policy-state` file is still useful for
`last_full_research_at` and for detecting active-rules hash changes across
runs; if no full-research timestamp is available, the policy conservatively
treats research cadence as stale once account/preflight freshness is satisfied.
Use `--state-output` to persist the next policy-state file. The policy updates
`last_full_research_at` only when the supplied `--pipeline-summary` shows a
completed committee-research stage, or when `--mark-full-research-complete` is
explicitly supplied for a known full-research command.

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

Build the final read-only operator check from the saved runbook chain:

```powershell
python scripts/longterm_paper_monday_check.py --runbook path\to\paper_runbook.json --workflow-smoke path\to\paper_workflow_smoke.json --paper-smoke-readiness path\to\paper_smoke_readiness.json --runbook-check path\to\paper_runbook_check.json --report-output path\to\paper_monday_operator_check.json --json
python scripts/longterm_paper_monday_check.py --runbook path\to\paper_runbook.json --workflow-smoke path\to\paper_workflow_smoke.json --paper-smoke-readiness path\to\paper_smoke_readiness.json --runbook-check path\to\paper_runbook_check.json --allow-existing-paper-positions --report-output path\to\paper_monday_operator_check.json --json
```

Use the second form only for an ongoing paper portfolio where existing paper
holdings are expected and the smoke-readiness artifact already confirms cash is
within tolerance.

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

The preview converts `BUY` intents into buy-notional preview rows, explicit
`SELL` / `REDUCE` intents into sell-preview rows, `REBALANCE` intents into
paired sell/buy preview rows, and `REVIEW` / `BLOCKED` / `CAPITAL_NEEDED`
intents into `no_order` rows. It carries decision IDs, risk metadata, blocked
reasons, cash shortfall, sell holding-value validation, and rebalance
transaction IDs. It does not import Alpaca and cannot submit orders.

Use `--order-model whole_share` with an explicit JSON price map when the paper
workflow should mirror a whole-share live broker such as Schwab API. The preview
floors BUY and SELL quantities to whole shares, records the requested notional,
estimated price, executable quantity, estimated notional, and size variance, and
blocks rows when the price is missing or the target value cannot buy/sell at
least one share.
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
python scripts/longterm_paper_execution.py --journal-db path\to\journal.db --ledger-db path\to\paper_ledger.db --portfolio-state path\to\portfolio.json --action-plan path\to\account_action_plan.json --submit-paper-orders --confirm-paper-submit SUPERVISED_PAPER_BUY_ONLY --runbook-check path\to\paper_runbook_check.json --scheduler-review-bundle path\to\scheduler_review_bundle.json --audit-output path\to\paper_execution_audit.json
```

Stage 6B is deliberately narrow:

- It submits only simple `BUY` previews.
- `--submit-paper-orders` also requires the exact `--confirm-paper-submit SUPERVISED_PAPER_BUY_ONLY` latch; without it, the command exits before refreshing broker state or constructing the submit adapter.
- `--submit-paper-orders` also requires a ready, fresh `--runbook-check` artifact whose plan ID and canonical action-plan hash match the action plan being submitted.
- When `--scheduler-review-bundle` is supplied, the submit CLI validates that
  the latest scheduler handoff is `ready_for_manual_review`, contains no
  scheduler policy or high-priority position-review blockers, keeps broker/LLM
  calls and runnable submit command emission disabled, and contains no
  submit-capable command fragments before any Alpaca paper state refresh.
- The runbook-check artifact must be schema v2 or newer, proving the saved
  evidence came from the promotion-aware workflow/readiness path.
- The runbook-check artifact must include a clean `promotion_summary`; missing
  or blocked promotion evidence stops the submit CLI before any Alpaca paper
  state refresh.
- `--submit-paper-orders` blocks when the Alpaca paper market clock is closed, so market BUY smoke orders are not left pending after hours.
- Rebalance, explicit sell/reduce, and sell-to-fund-buy previews are
  previewable for operator review but hard-blocked at the submit boundary with
  `rebalance_blocked_v1`.
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
python scripts/longterm_operator_status_bundle.py --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --paper-ledger-db path\to\paper_ledger.db --action-plan path\to\account_action_plan.json --price-map path\to\prices.json --feedback-summary path\to\feedback_summary.json --monday-operator-check path\to\paper_monday_operator_check.json --live-readiness-bundle path\to\live_readiness_bundle.json --status-refresh path\to\paper_order_status_refresh.json --scheduler-policy path\to\scheduler_policy.json --report-output path\to\operator_status_bundle.json
python scripts/longterm_operator_status_bundle.py --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --paper-ledger-db path\to\paper_ledger.db --action-plan path\to\account_action_plan.json --monday-operator-check path\to\paper_monday_operator_check.json --live-readiness-bundle path\to\live_readiness_bundle.json --status-refresh path\to\paper_order_status_refresh.json --scheduler-policy path\to\scheduler_policy.json --report-output path\to\operator_status_bundle.json --json
```

The bundle combines paper lifecycle, buy-promotion state, optional Monday
artifact status, optional live-readiness evidence, optional paper status-refresh
state, optional scheduler-policy cadence guidance, advisory scheduler readiness,
and position intelligence into one
operator surface. It is intended for manual review before any later scheduler
automation design and keeps order submission disabled. Its `agent_next_step`
rollup is guidance only; it can tell the operator/agent what to review next, but
it never authorizes broker submission. If a scheduler policy artifact is
supplied, the bundle carries the recommended safe command class and next safe
action forward for dashboard/operator review.

Build a compact dashboard from saved artifacts:

```powershell
python scripts/longterm_operator_dashboard.py --action-plan path\to\account_action_plan.json --market-regime path\to\market_regime.json --operator-status path\to\operator_status_bundle.json --scheduler-policy path\to\scheduler_policy.json --report-output path\to\operator_dashboard.json --html-output path\to\operator_dashboard.html --json
```

Build a static dashboard site with ticker detail pages:

```powershell
python scripts/longterm_operator_dashboard.py --dashboard-file path\to\operator_dashboard.json --action-plan path\to\account_action_plan.json --portfolio-state path\to\portfolio.json --evidence-file path\to\evidence_ready_ideas.json --site-output-dir path\to\operator_dashboard_site --fetch-price-history --json
```

The dashboard is a static read-only view for humans and the future autonomous
operator surface. It summarizes paper-ready stock BUY candidates, idle/defensive
parking guidance, current market regime, and a machine-readable
`agent_advisory` state such as `ready_for_supervised_paper_review`,
`parking_only_review`, `blocked_preflight`, or `research_more`. It does not
submit or authorize orders. When supplied, the scheduler policy appears as an
advisory-only card in the Command Center with recommended mode, urgency, next
safe action, reasons, warnings, and affected symbols. The visual hero renders
those machine states as
short human labels such as `Paper Review Ready` so the JSON remains precise
while the dashboard stays readable. The static site uses an original premium
research dashboard style inspired by the layout concepts at
`https://www.fool.com/premium` and ticker tear sheets like
`https://www.fool.com/premium/company/NASDAQ/AAPL/financials/summary`, without
copying Fool branding. The index page uses the `Autonomous Research Surface`
label, a static app shell with a left navigation rail, top status/search bar,
tab strip, and command cockpit sections for agent state, overview highlights,
paper-ready candidates, capital parking, portfolio/exposure summary,
safety/preflight guardrails, and the research board.
The shell links navigate to real in-page sections. The left rail includes an
explicit `All Tear Sheets` link for the research-board/ticker-page grid.
Rankings renders an operator action table from account-action-plan stock
intents, not the full evidence universe. It prioritizes actionable/pending
promotion-review state and then review confidence, with actionability,
why-not-buy blockers, trade value, quality, growth, valuation, safety, context,
and score source columns; the symbol text links directly to that ticker's tear
sheet. Wide ranking tables are wrapped in a local horizontal scroller and use
compact human-readable actionability labels so the page layout remains stable.
The wide rankings table
also has a synced top scroller and sticky Rank/Symbol columns so row identity
remains visible while scanning right-side details. Long blocker tokens are
humanized and allowed to wrap inside cells. Scorecards renders a universe-wide
table from evidence scorecards, with superscore, quality, growth, valuation,
safety, market buzz, investing type, drawdown band, and top score reasons
linked back to ticker tear sheets. Future Foundational Core, Hold / Review,
Closed Positions, About, and Settings areas render placeholders when those
artifacts are not populated yet. `My Stocks`
lands on its own portfolio-holdings section. If `--portfolio-state` is supplied,
the generated site renders current holdings with symbol, shares, original
purchase total cost, current total value, percent gain/loss, and status. If the
snapshot only provides quantity, market value, and average entry price, the
dashboard derives original purchase total cost from quantity times average entry
price. Without a portfolio snapshot, the section stays as an empty ready-to-fill
table. Anchor targets
use scroll margins so navigation lands at the section title rather than in the
body text. The search box filters the research-board ticker cards plus Rankings
and Scorecard rows locally, and resets pagination after each search.
Research-board cards, Rankings rows, and Scorecard rows use static client-side
pagination hooks (`data-paginated-list` / `data-paginated-item`) so the same
site shape can scale from dozens of names to a much larger universe before a
local server or backend-backed dashboard is needed.
Ticker pages place a generated price chart first, then show thesis, promotion
state, Graham discipline fields, scorecard, financial sections, earnings
context, article evidence, and safety notes. Graham fields include margin of
safety, permanent-loss score/flags, defensive-vs-enterprising mode, staged-entry
label/size, and normalized-earnings quality when promotion-review data is
available. The chart is a static-file interactive widget: it includes range
controls, hover/crosshair close values, and no external JavaScript dependency.

The generated site also includes an `Agent Desk` bubble as a placeholder for a
future chat/command surface. In the current static dashboard this panel is
deliberately inert: example prompts can be viewed, but the textarea and send
button are disabled. Future work must define authentication, active LLM context
handoff, audit logging, command parsing, safety prechecks, and an explicit
supervised approval boundary before any question or command can be sent from the
dashboard. The placeholder exists to reserve the interaction pattern, not to
create an execution path.

Validate and open an already-generated static dashboard site:

```powershell
python scripts/longterm_operator_dashboard_preview.py --site-dir path\to\operator_dashboard_site --open --json
```

The preview helper checks for `index.html` and ticker pages, prints a local
`file:///.../index.html` URL, optionally opens it in the default browser, and can
write a JSON preview report with `--report-output`. It is local preview only and
does not call a broker or mutate artifacts.

Serve a read-only localhost dashboard from a manifest instead of regenerating
static files after each artifact refresh:

```powershell
python scripts/longterm_operator_dashboard_server.py --manifest path\to\dashboard_manifest.json --write-manifest --write-manifest-only --action-plan path\to\account_action_plan.json --portfolio-state path\to\portfolio.json --market-regime path\to\market_regime.json --operator-status path\to\operator_status_bundle.json --evidence-file path\to\research_queue_reconciled.json --price-history-file path\to\price_history.json --pipeline-summary path\to\pipeline_summary.json --scheduler-policy path\to\scheduler_policy.json --decision-journal path\to\journal.db --active-rules ai_trader\rules\active_rules.txt --campaign-id campaign_name --json
python scripts/longterm_operator_dashboard_server.py --manifest path\to\dashboard_manifest.json --host 127.0.0.1 --port 8765 --json
python scripts/longterm_operator_dashboard_server.py --auto-manifest-root path\to\campaign_or_latest_artifact_root --host 127.0.0.1 --port 8765 --json
```

The manifest records artifact paths, campaign ID, decision-journal path, active
rules path/hash, and `order_submission_enabled=false`. The server resolves
`/`, `/tickers/<SYMBOL>.html`, `/api/summary.json`, `/api/manifest.json`,
`/api/portfolio.json`, `/api/pipeline-health.json`,
`/api/scheduler-policy.json`, and `/health` directly from the latest saved
files. In `--auto-manifest-root` mode it recursively discovers
the newest valid dashboard manifest under the artifact root on each request, so
scheduler refreshes can keep the dashboard current by writing the stable
`latest_operator_surface` artifacts. It is an artifact viewer only: it does not
call Alpaca, run research, call an LLM, reveal submit commands, or write
ledgers. Protected symbols such as `FXAIX` may appear as holdings, but the
server filters them out of actionable dashboard candidate lists. The browser-side
portfolio card polls `/api/portfolio.json`, and the safety card polls
`/api/pipeline-health.json`; account/current-price freshness, scheduler-policy
freshness, and pipeline artifact freshness still come from the Python
refresh/scheduler layer, not from browser-side broker credentials.

After a supervised paper submit or account-state change, refresh the read-only
account/status/dashboard artifacts from the current Alpaca paper account:

```powershell
python scripts/longterm_paper_account_refresh.py --profile-config path\to\profile.json --journal-db path\to\journal.db --action-plan path\to\account_action_plan.json --paper-ledger-db path\to\paper_ledger.db --output-dir path\to\paper_account_refresh --market-regime path\to\market_regime.json --evidence-file path\to\research_queue_reconciled.json --price-history-file path\to\price_history.json --pipeline-summary path\to\pipeline_summary.json --scheduler-policy path\to\scheduler_policy.json --status-refresh-file path\to\paper_order_status_refresh.json --dashboard-manifest-output path\to\dashboard_account_refresh_manifest.json --dashboard-site-output-dir path\to\operator_dashboard_site_account_refresh --json
```

This command is read-only. It reads the Alpaca paper account, preserves
`avg_entry_price`, original purchase total cost, current price, and unrealized
P/L in the portfolio snapshot, writes a fresh operator status bundle and
dashboard manifest/site, and marks `FXAIX` plus profile protected symbols as
protected. It reuses the supplied action plan as-is and does not regenerate
recommendations, next actions, journal outcomes, status-refresh events, or broker
orders. When `--pipeline-summary` is supplied, the refreshed dashboard manifest
also powers `/api/pipeline-health.json` and the Safety / Preflight artifact
health card. When `--scheduler-policy` is supplied, the refreshed status bundle,
manifest, localhost API, and dashboard Command Center all carry the advisory
next safe scheduler action without enabling order submission.

Build a Monday launch packet that combines the dashboard, filtered Stage 6B
candidate plan, Monday operator check, optional workflow-smoke whole-share
preview, and runbook state into one no-submit review artifact:

```powershell
python scripts/longterm_operator_launch_packet.py --dashboard-file path\to\operator_dashboard.json --candidate-plan path\to\account_action_plan_stage6b_submit_candidates.json --monday-check path\to\paper_monday_operator_check.json --workflow-smoke path\to\paper_workflow_smoke.json --runbook path\to\paper_runbook.json --site-index path\to\operator_dashboard_site\index.html --output path\to\monday_launch_packet.md --json-output path\to\monday_launch_packet.json
```

The launch packet is intentionally higher-level than the runbook. It says
whether the saved artifacts are ready for supervised review, lists the simple
paper BUY candidates and any idle-cash parking symbols, repeats the required
safety conditions, and keeps `order_submission_enabled=false`. It does not reveal
or run the submit command and it does not call a broker.

Filter the full account action plan to the narrow Stage 6B submit-candidate
plan before running paper-submit preflights:

```powershell
python scripts/longterm_action_plan_filter.py --action-plan path\to\account_action_plan.json --output path\to\account_action_plan_stage6b_submit_candidates.json --json
```

The filtered plan keeps only simple stock `BUY` intents with
`ACTIONABLE_BUY` promotion state. It excludes review follow-ups, parking
guidance, blocked rows, and rebalances so the full planning surface can remain
rich without making the V1 paper boundary noisy or unsafe.

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
python scripts/longterm_motley_fool_capture.py --source stock_advisor_service
python scripts/longterm_motley_fool_capture.py --source analyst_rankings
python scripts/longterm_motley_fool_capture.py --source quant_rankings
python scripts/longterm_motley_fool_capture.py --source dashboard
```

The capture uses the logged-in Chrome profile at `~/.grok3api_chrome_profile`.
Use one capture process at a time for that profile. The default full capture runs
the pages sequentially so the profile is not opened by multiple Playwright
sessions at once. The optional `stock_advisor_service` source targets the full
Stock Advisor service page for universe expansion and repeat-count context; it
is not part of the default full capture because fresh `new_recommendations`
remain the higher-priority recurring source.

Captured Motley Fool ideas include `motley_fool_company_url` / `source_url`
when the premium table exposes a per-company link. Some Fool tables use numeric
company URLs such as `https://www.fool.com/premium/company/202816`; later
enrichment can navigate those URLs and let Fool resolve the detailed financials
page before summarizing the ticker context.

Stock Advisor service-list rows may include the same symbol multiple times. The
intake path merges those rows by symbol, increments `source_recommendation_count`
for repeated service-list appearances, and preserves the Stock Advisor
long-run performance snapshot as display-only source context. This context is
useful for operator attribution, but it is not an execution signal and does not
bypass enrichment, promotion review, benchmark/account checks, or paper
eligibility.

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

Generate that file from live market inputs with the snapshot helper:

```powershell
python scripts/longterm_market_regime_snapshot.py --provider yfinance --output path\to\market_regime.json
```

Or let the dry-run scheduler generate it before each cycle:

```powershell
python scripts/run_longterm_scheduler.py --run-once --journal-db path\to\journal.db --portfolio-state path\to\portfolio.json --auto-market-regime-snapshot --market-regime-output path\to\market_regime.json --quiet
```

The generated snapshot uses VIX, SPY versus its 200-day moving average, and the
10-year Treasury yield trend. VIX alone does not trigger long-duration Treasury
parking. A chaotic equity selloff with falling yields can allow a capped TLT
hedge, while inflation/rate-shock volatility defaults defensive parking toward
SGOV/cash-like exposure.

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
evidence-only and does not enable live execution. Paper-smoke readiness counts
only when the artifact is promotion-aware schema v2 or newer and has no
buy-promotion blockers.

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
