# Think & Trade Like a Champion - Notes

Source:
- [Think & Trade Like a Champion PDF](S:\LLM_files\knowledge_agent\sources\swing_trader\books\Think%20%26%20Trade%20Like%20a%20Champion_%20The%20Secrets,%20Rules%20%26%20Blunt%20Truths%20of%20a%20Stock%20Market%20Wizard.pdf)
- Extracted sections: [champion extracted source](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_think_trade_like_a_champion_the_secrets_rules_blunt_truths_of_a_stock_market_wizard_pdf_5abd08a1a7)

Notes:
- Page numbers come from the knowledge-agent section index and are intended for targeted lookup.
- This notebook is oriented toward system design and execution discipline, not full-book summarization.

## High-Value Takeaways For Our Swing Trader

This book is especially useful because it is practical and process-heavy. It reinforces:

1. Every trade should begin with a concrete plan.
2. Risk-first is not just a slogan; it should drive entry, stop, sizing, and reallocation.
3. Stage 2 only. No bottom-fishing and no buying long-term downtrends.
4. The system should know whether a trade is acting "on schedule."
5. Position sizing should be dynamic: start smaller, add only when a stock proves itself.
6. Capital should be reallocated away from dead money and weak names.
7. Post-trade review is mandatory if we want the system to improve.

Best sections for our trading system:
- Section 1, pages 12-24: planning, contingencies, train-on-schedule framing
- Section 2, pages 25-28: risk-first and stop-loss discipline
- Section 4, pages 36-44: journaling and truth-in-results
- Section 5, pages 45-54: never average down
- Sections 6-7, pages 55-84: Stage 2 only, Trend Template, buy rules
- Section 8, pages 85-87: position sizing and reallocation
- Section 9, pages 88-103: profit-taking, base count, late-stage risk
- Section 10, pages 104-108: concentration, turnover, timing
- Section 11, pages 109-124: mental rehearsal and process focus

## Front Matter / Introduction

### Introduction - First Steps to Thinking and Trading Like a Champion

- Pages 6-11
- File: [section_002.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_think_trade_like_a_champion_the_secrets_rules_blunt_truths_of_a_stock_market_wizard_pdf_5abd08a1a7\section_002.txt)
- Main topics:
  - Success is a choice, not a gift.
  - A trader needs the builder mindset, not the wrecking-ball mindset.
  - Process matters more than fantasy about outcomes.
  - Daily preparation should reinforce discipline and ownership.
- System relevance:
  - Reinforces why we should prefer rule-governed automation over ad hoc impulses.
  - Strong support for explicit daily preparation and post-analysis routines in the agent.

## Section 1 - Always Go In With A Plan

- Pages 12-24
- File: [section_003.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_think_trade_like_a_champion_the_secrets_rules_blunt_truths_of_a_stock_market_wizard_pdf_5abd08a1a7\section_003.txt)
- Main topics:
  - Every trade needs a plan before entry.
  - Hope is not a strategy.
  - The "train on schedule" analogy: define what timely progress should look like.
  - Contingency planning should be prepared in advance.
  - Core contingency items:
    - initial stop-loss
    - reentry criteria
    - selling into strength
    - selling into weakness
    - disaster plan
  - Priorities:
    - limit loss
    - protect breakeven
    - protect profit
- System relevance:
  - This is direct support for the planned early post-entry "on-schedule" framework.
  - Strong support for our overnight-gap / disaster handling.
  - Strong support for explicit reentry logic later.
  - Reinforces that every position should have a defined lifecycle, not just an entry.

## Section 2 - Approach Every Trade Risk-First

- Pages 25-28
- File: [section_004.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_think_trade_like_a_champion_the_secrets_rules_blunt_truths_of_a_stock_market_wizard_pdf_5abd08a1a7\section_004.txt)
- Main topics:
  - Always determine the exit before entry.
  - Stop-loss discipline is non-negotiable.
  - Mental stops are weak if not enforced.
  - Every huge loss starts as a small one.
  - Avoid becoming an "involuntary investor."
  - Large losses are mathematically toxic.
  - Avoid highly erratic names if they cannot be traded with sensible stop distance.
- System relevance:
  - Very strong support for the system’s safety-first bias.
  - Supports preserving hard protective stops even when trying to relieve live starvation.
  - Supports rejecting symbols that are too chaotic for disciplined risk control.

## Section 3 - Never Risk More Than You Expect To Gain

- Pages 29-35
- File: [section_005.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_think_trade_like_a_champion_the_secrets_rules_blunt_truths_of_a_stock_market_wizard_pdf_5abd08a1a7\section_005.txt)
- Main topics:
  - Reward/risk must make mathematical sense.
  - A trade should not be taken just because the story is appealing.
  - Expected gain versus expected loss should be explicit.
  - Precision matters more than activity.
- System relevance:
  - Supports requiring structured planning fields in the LLM decision flow.
  - Suggests conviction should remain coupled to risk-adjusted opportunity, not just "good idea" energy.

## Section 4 - Know The Truth About Your Trading

- Pages 36-44
- File: [section_006.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_think_trade_like_a_champion_the_secrets_rules_blunt_truths_of_a_stock_market_wizard_pdf_5abd08a1a7\section_006.txt)
- Main topics:
  - Measure everything.
  - Keep a spreadsheet or journal of every trade.
  - Know average gain, average loss, win rate, largest gain/loss, and holding time.
  - Keep strategy-specific records; don’t mix modes.
  - Results are personal truth.
- System relevance:
  - This strongly validates the non-pick follow-up and learning journal work.
  - Strong support for keeping live, simulator, and review metadata aligned.
  - Supports our focus on preserving structured fields like lane, leader quality, and future progress-state metadata.

## Section 5 - Compound Money, Not Mistakes

- Pages 45-54
- File: [section_007.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_think_trade_like_a_champion_the_secrets_rules_blunt_truths_of_a_stock_market_wizard_pdf_5abd08a1a7\section_007.txt)
- Main topics:
  - Never average down into losers.
  - "Just this one time" is the slippery slope that breaks discipline.
  - Only losers average losers.
  - Every major decline starts as a minor pullback.
  - The 50/80 rule:
    - once a secular leader tops, there is a high probability of a very large decline
  - Broken leaders are dangerous.
- System relevance:
  - Strong support for never adding to weakness in the live system.
  - Supports reentry only after a fresh valid setup, never by averaging down.
  - Reinforces that stop-outs should not be emotionally "fixed" by buying more.

## Section 6 - How And When To Buy Stocks, Part 1

- Pages 55-66
- File: [section_008.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_think_trade_like_a_champion_the_secrets_rules_blunt_truths_of_a_stock_market_wizard_pdf_5abd08a1a7\section_008.txt)
- Most important pages:
  - Pages 55-58: with-the-trend philosophy
  - Pages 58-61: Stage 2 only
  - Pages 61-64: Trend Template criteria
- Main topics:
  - Only buy with the long-term uptrend.
  - Stage 2 only.
  - Charts tell you whether the stock is acting normally or abnormally.
  - The stock should do what it ought to do; that defines the exit logic.
  - Trend Template, eight criteria:
    - price above 150d and 200d
    - 150d above 200d
    - 200d rising for at least one month, preferably longer
    - 50d above 150d and 200d
    - at least 25 percent above 52-week low
    - within 25 percent of 52-week high
    - RS ranking at least 70, preferably 90s
    - RS line rising, ideally for 6-13 weeks or more
    - price above 50d coming out of base
- System relevance:
  - This is the best single book section for improving our leader-quality overlay.
  - It provides concrete fields for future leader/stage scoring:
    - 150d vs 200d
    - rising 200d
    - distance from 52-week high
    - relative-strength trend, not just snapshot
  - Strong support for refusing long-term downtrend names even if a short-term pattern looks tempting.

## Section 7 - How And When To Buy Stocks, Part 2

- Pages 67-84
- File: [section_009.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_think_trade_like_a_champion_the_secrets_rules_blunt_truths_of_a_stock_market_wizard_pdf_5abd08a1a7\section_009.txt)
- Main topics:
  - Favor shallower corrections over deep damage.
  - Avoid stocks down more than roughly 2.5x-3x the market decline under normal conditions.
  - Stocks down 60 percent or more are generally off-limits.
  - Big winners must make new highs.
  - Use RS ranking plus RS line plus technical action together.
  - Leaders often bottom before the market.
  - The best stocks emerge in the first 4-8 weeks of a new bull leg.
  - Early leaders may appear before the whole sector looks strong.
- System relevance:
  - This supports adding a "damage" check into leader-quality.
  - It also supports later leader-breadth context and earlier recognition of improving leadership.
  - Strong evidence that the system should prefer resilient names over dramatic rebound stories.

## Section 8 - Position Sizing For Optimal Results

- Pages 85-87
- File: [section_010.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_think_trade_like_a_champion_the_secrets_rules_blunt_truths_of_a_stock_market_wizard_pdf_5abd08a1a7\section_010.txt)
- Main topics:
  - Never risk too much account equity on one position.
  - Suggested equity risk per trade:
    - roughly 1.25 to 2.5 percent
  - Position size should be backed into from stop distance and risk tolerance.
  - Optimal concentration is a handful of strong names, not dozens.
  - Often start smaller, then increase when the stock proves itself.
  - Reallocate away from names that are not acting well enough or quickly enough.
  - Two-for-one rule:
    - trim two weak positions to fund one better candidate
- System relevance:
  - Strong support for a future starter-size plus add-on framework.
  - Strong support for reallocation away from dead money.
  - Supports concentration, but only in proven names with controlled risk.

## Section 9 - When To Sell And Nail Down Profits

- Pages 88-103
- File: [section_011.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_think_trade_like_a_champion_the_secrets_rules_blunt_truths_of_a_stock_market_wizard_pdf_5abd08a1a7\section_011.txt)
- Most important themes:
  - selling into strength versus weakness
  - putting the chart into perspective
  - base count
  - late-stage risk
  - P/E expansion as a late-stage warning, not an entry filter
- Main topics:
  - Selling profits is emotionally hard and needs rules.
  - Aerial-view context matters before making sell decisions.
  - Use reward/risk math when deciding whether to hold or take profits.
  - Base count helps distinguish early-stage from late-stage opportunities.
  - Bases 1-2 after a correction are usually the highest-quality opportunities.
  - Bases 5-6 are increasingly failure-prone.
  - Late-stage crowded trades are dangerous.
- System relevance:
  - Strong support for earlier, more structured trade-management evaluation.
  - Supports the idea that some strong names should be held longer early in a new bull phase.
  - Could later inform more nuanced profit management and "late-stage caution" logic.

## Section 10 - Eight Keys To Unlocking Superperformance

- Pages 104-108
- File: [section_012.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_think_trade_like_a_champion_the_secrets_rules_blunt_truths_of_a_stock_market_wizard_pdf_5abd08a1a7\section_012.txt)
- Main topics:
  - Big performance requires both upside generation and drawdown control.
  - Timing matters.
  - Diversification dilutes edge if you truly have one.
  - Turnover is not taboo if you are rotating capital intelligently.
  - Concentration should happen at the right time, not all the time.
- System relevance:
  - Supports active capital reallocation.
  - Supports concentrating when conditions and candidate quality are genuinely favorable.
  - Useful framing for future live-vs-sim portfolio management decisions.

## Section 11 - The Champion Trader Mindset

- Pages 109-124
- File: [section_013.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_think_trade_like_a_champion_the_secrets_rules_blunt_truths_of_a_stock_market_wizard_pdf_5abd08a1a7\section_013.txt)
- Main topics:
  - Outcome detachment
  - mental rehearsal, not fantasy visualization
  - daily routines matter
  - review yesterday’s wins, lessons, and improvement points
  - celebrate disciplined small losses so pain/pleasure conditioning works in your favor
  - process focus beats scoreboard focus
- System relevance:
  - This is strong support for systematic post-trade and non-pick review.
  - Supports having the agent learn from small, disciplined "good losses" instead of treating every no-buy or stopped trade as failure.
  - Good conceptual support for future review and coaching tools around the system, even if not directly code-translated.

## Immediate Improvement Ideas Backed By This Book

### 1. Build the early post-entry "on-schedule" framework
- Best references:
  - Section 1, pages 12-24
  - Section 6, pages 55-66
  - Section 9, pages 88-103
- Why:
  - The book repeatedly frames trade management around whether the stock is behaving as planned.

### 2. Deepen leader-quality scoring
- Best references:
  - Section 6, pages 55-66
  - Section 7, pages 67-84
- Why:
  - The Trend Template gives concrete stage and resilience criteria we can score.

### 3. Add starter-size plus add-on readiness later
- Best references:
  - Section 8, pages 85-87
- Why:
  - The book strongly supports smaller initial exposure until the stock proves itself.

### 4. Continue strengthening journal / follow-up analytics
- Best references:
  - Section 4, pages 36-44
  - Section 11, pages 109-124
- Why:
  - The system should know the truth about its own behavior, not just its outcomes.

### 5. Keep anti-average-down and anti-bottom-fishing rules hard
- Best references:
  - Section 5, pages 45-54
  - Section 7, pages 67-84
- Why:
  - This is one of the clearest "never do this" messages in the book.

## Best Follow-Up Queries For Knowledge Agent

Use these when we want to drill deeper:
- `always go in with a plan`
- `risk first stop loss`
- `trend template stage 2`
- `leader stocks bottom first`
- `position sizing prove itself`
- `two for one rule reallocate`
- `base count late stage`
- `mental rehearsal trading`

