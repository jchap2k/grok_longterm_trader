# Book Knowledge Coverage Audit

Last reviewed: 2026-05-09

Implementation update: 2026-05-10

Visibility update: 2026-05-10

This audit compares the curated book notes and extracted source cache under
`knowledge_agent/` against the long-term trader implementation. It is a
coverage map, not a replacement for the notes. The goal is to identify where
book knowledge is already operational and where it is still only prompt context
or informal policy.

## Sources Reviewed

Long-term trader sources:

- `One Up On Wall Street`
- `Quality Investing`
- `The Intelligent Investor Third Edition`
- `The Little Book That Still Beats the Market`

Swing/trading sources used as long-term filters:

- `How to Make Money in Stocks`
- `Think & Trade Like a Champion`
- `Trade Like a Stock Market Wizard`

Curated notes reviewed:

- `knowledge_agent/docs/one_up_on_wall_street_notes.md`
- `knowledge_agent/docs/quality_investing_notes.md`
- `knowledge_agent/docs/the_intelligent_investor_third_edition_notes.md`
- `knowledge_agent/docs/the_little_book_that_still_beats_the_market_notes.md`
- `knowledge_agent/docs/how_to_make_money_in_stocks_notes.md`
- `knowledge_agent/docs/think_and_trade_like_a_champion_notes.md`
- `knowledge_agent/docs/trade_like_a_stock_market_wizard_chapter_notes.md`
- `knowledge_agent/docs/longterm_reframing_of_existing_swing_books.md`
- `knowledge_agent/docs/longterm_trader_research_direction.md`

Implementation areas reviewed:

- `ai_trader/rules/active_rules.txt`
- `ai_trader/trading_agent/longterm/book_principles.py`
- `ai_trader/trading_agent/longterm/reviewers.py`
- `ai_trader/trading_agent/longterm/graham_risk.py`
- `ai_trader/trading_agent/longterm/quality_growth_scorecard.py`
- `ai_trader/trading_agent/longterm/buy_promotion.py`
- `ai_trader/trading_agent/longterm/account_action_plan.py`
- `ai_trader/trading_agent/longterm/discovery.py`
- `ai_trader/trading_agent/longterm/discovery_enrichment.py`
- `ai_trader/trading_agent/longterm/review_templates.py`
- `docs/system/ARCHITECTURE.md`
- `docs/system/OPERATIONS.md`
- `docs/system/REPO_CONTEXT.md`

## Current Coverage

### Strongly Operational

Lynch business-story discipline is well represented.

- Company category, business summary, thesis summary, growth driver, industry
  context, confirming signals, and invalidation conditions are first-class
  packet fields.
- `BusinessStoryReviewer` checks whether the story is understandable and
  thesis-shaped.
- Review cadence varies by company category and risk language.
- Motley Fool ideas are treated as sourcing context, not buy authority.

Graham margin-of-safety discipline is now first-class.

- `MarginOfSafetyReviewer` checks valuation support, normalized earnings or
  cash-flow language, overpayment risk, and permanent-loss language.
- `graham_risk.py` adds permanent-loss flags, defensive/enterprising/speculative
  labels, staged-entry sizing, and Mr. Market review triggers.
- `buy_promotion.py`, account planning, next-actions, and dashboard surfaces
  expose margin-of-safety follow-ups and staged-entry sizing.

Quality Investing durability language is operational at reviewer level.

- `QualityDurabilityReviewer` names durable quality patterns and common traps.
- Active rules require a quality pattern and quality-trap risk before buy/add.
- Review templates force checks for moat, recurring demand, stickiness,
  reinvestment runway, debt stress, share loss, churn, and management turnover.

Greenblatt-style QARP separation is operational.

- `QualityAtReasonablePriceReviewer` separates quality from valuation.
- `quality_growth_scorecard.py` scores quality, growth, valuation, safety, and
  market attention separately before producing a composite.
- `quality_growth_scorecard.py` also emits an additive
  `valuation_sanity_score` with FCF yield, earnings yield, PEG, return on
  invested capital, FCF/share growth, and cash/debt reasons. This gives
  Graham/Greenblatt valuation context without replacing the existing
  `valuation_score` contract.
- Account planning and buy promotion do not allow quality alone to bypass
  valuation and safety gates.

Moat durability now has a first-class deterministic reviewer.

- `MoatDurabilityReviewer` checks for explicit moat evidence such as switching
  costs, network effects, pricing power, recurring revenue, installed base,
  brand strength, distribution advantage, scale/cost advantage, data/regulatory
  advantages, share gains, and stable oligopoly structure.
- It also flags moat-decay risks such as commoditization, churn, customer or
  platform dependency, price wars, share loss, good-enough substitutes,
  disruption, and regulatory risk.
- The reviewer is wired into `LongTermResearchRunner._run_deterministic_reviews`
  so the CGH committee sees moat support and objections before making a final
  recommendation.

Management and capital allocation now have a first-class deterministic
reviewer.

- `ManagementCapitalAllocationReviewer` checks packet text for owner alignment,
  disciplined capital allocation, reinvestment discipline, ROIC/return on
  invested capital, sensible buybacks, net cash, founder/operator language, and
  acquisition discipline.
- It flags dilution, SBC, empire building, serial acquisitions,
  leverage-funded buybacks, management turnover, accounting issues, aggressive
  guidance, weak cash conversion, and refinancing risk.
- `ResearchPacket` now preserves `fundamental_metrics`, allowing this reviewer
  to use ROIC/ROC, FCF margin, and cash/debt context when available while
  falling back gracefully for thin packets.
- Journal-backed dashboard evidence now hydrates `reviewer_support` and
  `reviewer_objections` into a ticker-page `Book Reviewer Signals` panel so
  moat and management/capital-allocation support/objections are visible during
  operator review.
- Ticker scorecards and evidence briefs now surface `valuation_sanity_score`
  and valuation-sanity reasons, so the Graham/Greenblatt valuation layer is
  visible outside the raw JSON artifacts.

Swing-book risk discipline is integrated as a long-term filter.

- Active rules prefer leaders over cheap laggards.
- Confirmation, invalidation, position sizing, review triggers, and avoiding
  broken leadership are explicit policy concepts.
- Broad panic/defensive policy uses volatility, trend, leadership, and recovery
  confirmation without turning the system into a short-term trader.

### Partially Operational

Management and capital allocation are now scored, but the data is still V1.

- The new reviewer combines text evidence with ROIC/ROC, FCF margin, and
  cash/debt context when available.
- Remaining gaps: share count trend, buyback quality, SBC dilution,
  acquisition returns, and explicit reinvestment-runway metrics.

Moat analysis now has a reviewer, but not a full taxonomy.

- Quality durability and the new moat reviewer both catch recurring revenue,
  installed base, pricing power, switching costs, share gains, stable
  oligopoly, brand strength, distribution advantage, and cost-to-replicate
  language.
- Remaining gaps: explicit moat type, moat evidence strength, moat decay risk
  severity, and moat time horizon.
- There is no dedicated Morningstar-style moat taxonomy layer.

Valuation is stronger, but still not a full intrinsic-value model.

- Current deterministic valuation uses P/E, EV/EBITDA, P/FCF, and PEG buckets.
- The scorecard now adds a valuation sanity layer for FCF yield, earnings
  yield, PEG, return on invested capital, FCF/share growth, and cash/debt.
- Graham and Greenblatt notes emphasize normalized earnings, earnings yield,
  book/asset support in specific cases, and process discipline through periods
  of underperformance.
- Remaining gaps: normalized owner earnings, rate/alternative comparison,
  book/asset support for asset plays, and explicit valuation assumptions and
  sensitivity ranges.

Industry and relative leadership are present, but not consistently measured.

- Discovery enrichment supports `category_leader`; active rules prefer
  industry/category leaders.
- The system does not yet compute industry-group relative strength, sector peer
  rank, leadership breadth, institutional sponsorship trend, or supply/demand
  pressure from volume.
- Kronos can add market-language context, but it should remain advisory and not
  replace deterministic relative-strength or leadership features.

CAN SLIM and Minervini concepts are intentionally reduced for long-term use.

- The current long-term version correctly avoids short-term chart precision,
  excessive turnover, and mechanical stop-loss behavior.
- However, it may be underusing the parts that transfer well: earnings/sales
  acceleration, institutional sponsorship, industry group strength, and price
  action as a sanity check before starting or adding.

### Weak Or Missing

Personal scuttlebutt / "buy what you know" is not a first-class input lane.

- Lynch-style idea sourcing is supported by source notes and business summaries.
- There is no structured operator-observation field for product usage,
  customer behavior, local evidence, or "I understand this business because..."

Accounting quality is mostly language-based.

- Graham permanent-loss checks can flag accounting risk from text.
- There is no deterministic accruals, cash-conversion, receivables/inventory,
  capitalized-cost, or one-time-adjustment analysis.

Shareholder yield and dilution are underused.

- The source cache and notes contain many references to management, debt,
  cash flow, share repurchase, dilution, and capital allocation themes.
- Current scorecards do not strongly evaluate buybacks, SBC dilution, net share
  count trend, or whether repurchases happen at sensible valuations.

Category-specific expectations could be stricter.

- Lynch categories exist and drive review cadence.
- The reviewer layer does not yet apply category-specific evidence requirements,
  such as cyclicals needing cycle normalization, turnarounds needing balance
  sheet/runway proof, asset plays needing asset-value support, or fast growers
  needing growth runway and reinvestment proof.

## Notes-Vs-Source Sanity Check

The extracted source cache shows several themes that appear much more often in
the source text than in the curated notes. This does not mean the notes are
wrong, but it does indicate where the notes may be too compressed for full
system use:

- `management`
- `competitive advantage`
- `return on capital`
- `earnings yield`
- `margin of safety`
- `Mr. Market`
- `institutional`
- `relative strength`
- `industry group`
- `market leader`
- `cash flow`
- `book value`
- `sales growth`
- `earnings growth`
- `supply and demand`
- `base pattern`
- `position size`

The current notes capture the main concepts, but the next notes pass should add
more implementation-oriented bullets for these areas, especially where they map
to deterministic fields or reviewer outputs.

## Recommended Next Implementation Chunks

1. Extend `MoatDurabilityReviewer` into a moat taxonomy.

   Add moat type, evidence strength, decay-risk severity, and time horizon.

2. Extend `ManagementCapitalAllocationReviewer` with richer metrics.

   Add share count trend, SBC/dilution, buyback quality, acquisition returns,
   ROIC/ROC trend, and leverage trend when data sources support them.

3. Expand valuation sanity into intrinsic-value assumptions.

   Add normalized owner earnings, rate/alternative comparison, asset-value
   support for asset plays, and explicit valuation assumptions in the
   packet/journal.

4. Add industry leadership and trend sanity.

   Compute peer/sector relative rank where possible, preserve industry-group
   context, and expose advisory technical sanity checks for starts/adds without
   turning the long-term system into a swing trader.

5. Add category-specific evidence gates.

   Apply different minimum evidence requirements for stalwarts, fast growers,
   cyclicals, turnarounds, asset plays, and slow growers before committee calls
   or buy-promotion review.

6. Add an operator-observation source field.

   Preserve Lynch-style "what do we actually know about the product/customer?"
   evidence as structured context, separate from scraped articles and Motley
   Fool source rows.

## Recommended Notes Update

The current notes are good enough for prompt context, but not complete enough
for implementation planning. Add a short "Implementation hooks" section to each
book note with:

- packet fields the concept should populate
- deterministic reviewer fields the concept should influence
- dashboard/report fields the concept should surface
- false-positive traps the system should guard against

Priority order:

1. `quality_investing_notes.md` for moat and capital allocation hooks.
2. `the_little_book_that_still_beats_the_market_notes.md` for earnings yield
   and ROC implementation hooks.
3. `the_intelligent_investor_third_edition_notes.md` for normalized valuation,
   asset support, and category-specific margin of safety.
4. `longterm_reframing_of_existing_swing_books.md` for relative strength,
   industry group strength, and institutional sponsorship as advisory filters.
5. `one_up_on_wall_street_notes.md` for category-specific evidence gates and
   operator-observation/scuttlebutt intake.

## Bottom Line

The system is using the core book knowledge, especially Lynch story discipline,
Graham permanent-loss protection, Quality Investing durability/trap language,
and Greenblatt QARP separation. As of 2026-05-10, moat durability,
management/capital allocation, and valuation sanity have first-pass
deterministic implementation. The largest remaining opportunities are richer
moat taxonomy, richer capital-allocation metrics, intrinsic-value assumptions,
industry leadership, and category-specific evidence rules.
