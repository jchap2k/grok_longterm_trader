# Long-Term Trader Foundation Plan

## Goal

Build `grok_longterm_trader` as a research-first quality-growth trading system that holds positions for weeks to months, and sometimes longer, while preserving the safe broker and logging foundation already copied from `grok_swing_trader`.

This project is **not** intended to be:
- a copy of the swing trader with wider stops
- a deep-value cigar-butt investor
- a passive buy-and-hold allocator
- a fully discretionary AI committee

## Strategic Identity

The current research base points toward:
- understandable businesses
- strong or improving fundamentals
- category or industry leadership
- acceptable valuation relative to quality
- balance-sheet discipline
- thesis-driven entries and reviews

Plain-language summary:
- buy quality growth with discipline
- avoid cheap damaged laggards
- do deeper research on fewer names
- hold longer than swing trader
- review more slowly, but more deeply

## Research Base

Primary current sources:
- `One Up On Wall Street`
- `The Little Book That Still Beats the Market`
- `How to Make Money in Stocks`
- `Think & Trade Like a Champion`
- `Trade Like a Stock Market Wizard`

These sources support:
- idea generation
- category classification
- quality-growth selection
- trend and leadership awareness
- risk discipline
- valuation sanity checking

Current weak spots that later research should address:
- moat durability
- management quality
- capital allocation quality
- deeper intrinsic-value work

## Core Principles

1. A stock is a business first.
2. Idea generation is not enough; every candidate needs a research packet.
3. Company quality and stock price attractiveness must be evaluated separately.
4. Leadership is preferred to cheapness.
5. Balance-sheet weakness can kill a good story.
6. Every position needs a thesis, confirming signals, and invalidation conditions.
7. Long-term does not mean ignoring risk.

## First-System Shape

### 1. Idea Sourcing

Start with a narrow set of sources:
- practical observation and category familiarity
- earnings and growth screens
- category and industry leadership screens
- high-quality watchlist carry-forward

### 2. Company Classification

Use Lynch-style classification early:
- slow growers
- stalwarts
- fast growers
- cyclicals
- turnarounds
- asset plays

This classification should affect:
- hold horizon
- expected source of alpha
- risk tolerance
- thesis shape

### 3. Research Packet

Every candidate should eventually have a structured research packet with:
- symbol
- company category
- business summary
- thesis summary
- growth driver
- category / industry context
- balance-sheet assessment
- quality score
- valuation score
- combined attractiveness score
- expected hold horizon
- invalidation conditions
- review cadence

### 4. Reviewer Layer

First reviewers to implement:
- `BusinessStoryReviewer`
- `BalanceSheetReviewer`
- `QualityAtReasonablePriceReviewer`

Likely later reviewers:
- `MoatReviewer`
- `CapitalAllocationReviewer`

### 5. Portfolio Layer

The long-term trader should eventually decide:
- how many names can be held at once
- how much to size initial positions
- when to add to winners
- when to trim or exit on thesis deterioration
- how to rank liked candidates against current holdings
- when a better idea is strong enough to justify rebalancing

The live destination is autonomous inside explicit safety rails: the agent should find candidates, research them, compare them against current holdings, buy or rebalance when the active-sleeve opportunity is clearly superior, and continue measuring results against `FXAIX`.

The system should maintain a recommendation table similar in spirit to a curated stock-ranking service:
- one current row per liked symbol
- rank from strongest to weakest conviction
- include company, action, service/source, price, daily change, previous rank, market cap, risk type, 1Y revenue growth, return since recommendation, recommendation date, estimated return range, estimated max drawdown, times recommended, discussion/notes count, thesis reason, and a supporting link/reference when available
- use the table as the source of truth when new cash becomes available

If the table contains a high-conviction opportunity but active-sleeve cash is insufficient, the system may create a capital-needed alert. Brevo can later deliver that alert by email, but the alert should remain informational and should never automatically request deposits, sell protected holdings, or bypass sizing/risk rules.

### 6. Account Strategy Modes

Support account-aware behavior when it changes the right action:
- standard taxable mode
- Roth IRA mode

Roth IRA mode should allow more flexible defensive rebalancing during major market pullbacks because selling does not create near-term tax friction. That makes it reasonable to:
- rotate out of fragile holdings sooner
- emphasize pullback-resilient holdings during severe market stress
- rebalance back toward offensive leadership after broad market recovery is confirmed

Protected symbols should override rebalancing logic. The system must support explicit untouchable holdings that are tracked for context but never proposed for sale, trimming, or rotation unless the user removes the protection.

Benchmark and parking behavior should also be distinct:
- `FXAIX` can serve as the benchmark and protected core holding
- temporary defensive parking should use a liquid, reversible vehicle like `SPY`
- the system must not suggest moving into a protected benchmark position if that would make later re-entry operationally awkward

Cash should also be an available defensive state for the active sleeve:
- use cash only for truly hostile conditions, not ordinary pullbacks
- treat extreme volatility as a danger flag, not a standalone sell signal
- require broader deterioration such as broken trend and failing leadership before shifting the active sleeve fully defensive
- require recovery confirmation before redeploying

## Must-Have Phase 1 Features

1. Clear strategy identity and rules file
2. Research packet schema
3. Company category classifier
4. Balance-sheet review fields
5. Separate quality and valuation scoring
6. Thesis and invalidation tracking
7. Slow-cadence position review framework
8. Account-aware mode support for Roth IRA defensive rotation
9. Protected symbol support for untouchable core holdings
10. Separate benchmark symbol and defensive parking symbol support
11. Extreme-volatility cash mode for the active sleeve

## Highly Desirable Early Features

1. Industry / category leadership overlay
2. Trend-alignment overlay for entries
3. Watchlist-to-research funnel
4. Position sizing that starts smaller and adds only on confirmation
5. Logging that distinguishes:
   - idea source
   - thesis strength
   - valuation attractiveness
   - review outcome
6. Ranked recommendation table with links/reasons
7. Capital-needed alert payloads for future Brevo digest delivery

## Non-Goals For V1

- no pure deep-value strategy
- no short-term intraday logic
- no swing-specific PEAD / FORCESWING rule copy
- no giant multi-agent orchestration graph
- no second live order path outside the safe broker tooling

## Initial Build Order

### Phase 1
- establish rules and research vocabulary
- define research packet shape
- define stock category classifier

### Phase 2
- build research and reviewer scaffolding
- define first long-term candidate intake path
- define thesis review cadence

### Phase 3
- connect to safe broker / paper path
- add portfolio and sizing rules
- add basic backtest or replay path for long-horizon decisions

### Phase 4
- widen research depth
- add moat and capital allocation overlays
- compare long-term process results against simpler baselines

## Key Difference From Swing Trader

Swing trader:
- broad scan
- lighter per-name research
- shorter holding window
- more timing sensitivity

Long-term trader:
- narrower funnel
- deeper per-name research
- slower review cadence
- stronger business-quality and valuation emphasis

## Deliverables From This Plan

This plan should lead directly to:
- a long-term trader rules file
- a research packet schema
- a strategy implementation plan
- later, a long-term prompt and reviewer system
