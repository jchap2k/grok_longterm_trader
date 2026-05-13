# Trade Like a Stock Market Wizard - Chapter Notes

Source:
- [Trade Like a Stock Market Wizard PDF](S:\LLM_files\knowledge_agent\sources\swing_trader\books\Trade%20Like%20a%20Stock%20Market%20Wizard_%20How%20to%20Achieve%20Super%20Performance%20in%20Stocks%20in%20Any%20Market.pdf)
- Extracted sections: [wizard extracted source](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_trade_like_a_stock_market_wizard_how_to_achieve_super_performance_in_stocks_in_any_market_pdf_a51586d490)

Notes:
- Page numbers below come from the knowledge-agent section index and are good enough for targeted lookup, but they may differ slightly from some PDF viewer page counters.
- This is a working research notebook for system design, not a full book summary.

## High-Value Takeaways For Our Swing Trader

The book strongly reinforces these directions for the live system:

1. Buy confirmed Stage 2 leaders, not cheap laggards.
2. Screen on hard technical criteria first, then use overlays for fundamentals/catalysts.
3. Treat leadership as an early market signal; the best stocks often lead the indexes.
4. Track whether a stock acts "on schedule" after entry instead of just waiting on a blunt time stop.
5. Keep risk-first thinking central: initial stop, reentry, profit protection, and disaster planning.

Best chapters to revisit when improving the system:
- Chapter 5, pages 33-60: stage analysis, trend alignment, no bottom-fishing
- Chapter 6, pages 61-70: categories, leaders, catalysts
- Chapter 7, pages 71-84: earnings/fundamental drivers
- Chapter 8, pages 85-95: earnings quality
- Chapter 9, pages 96-112: leaders as early market signal
- Chapter 12, pages 180-187: risk-first mindset
- Chapter 13, pages 188-208: stop, reentry, profit protection, disaster planning

## Front Matter

### Contents / Foreword
- Pages 3-7
- Main value:
  - David Ryan explicitly recommends reading the two risk-management chapters first.
  - Foreword frames the book around combining technical and fundamental analysis.
  - It also emphasizes the growth-stock life cycle: early phase, acceleration, topping, decline.

## Chapter 1 - An Introduction Worth Reading

- Pages 8-10
- File: [section_003.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_trade_like_a_stock_market_wizard_how_to_achieve_super_performance_in_stocks_in_any_market_pdf_a51586d490\section_003.txt)
- Main topics:
  - Winning traders share a small set of key traits.
  - Superperformance requires desire plus a winning strategy.
  - The author frames his style as "conservative aggressive opportunist."
  - Risk-first thinking is presented right at the start.
  - Opportunity plus preparation is a recurring theme.
- System relevance:
  - Supports keeping hard safety rails while still pursuing concentrated upside.
  - Supports our current direction of improving candidate quality rather than chasing more random trades.

## Chapter 2 - What You Need To Know First

- Pages 11-15
- File: [section_004.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_trade_like_a_stock_market_wizard_how_to_achieve_super_performance_in_stocks_in_any_market_pdf_a51586d490\section_004.txt)
- Main topics:
  - Superperformance is learnable, not luck.
  - Start small if needed; edge comes from skill, not account size.
  - History repeats because people repeat.
  - The main challenge is the trader, not the market.
  - Practice and emotional discipline matter more than opinions.
- System relevance:
  - Supports logging, review, and disciplined process over ad hoc intuition.
  - Reinforces the value of the non-pick follow-up work and learning database.

## Chapter 3 - Specific Entry Point Analysis: The SEPA Strategy

- Pages 16-19
- Files:
  - [section_005.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_trade_like_a_stock_market_wizard_how_to_achieve_super_performance_in_stocks_in_any_market_pdf_a51586d490\section_005.txt)
  - [section_006.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_trade_like_a_stock_market_wizard_how_to_achieve_super_performance_in_stocks_in_any_market_pdf_a51586d490\section_006.txt)
- Main topics:
  - Reverse-factor modeling: study the biggest winners to identify common traits.
  - There is a right time and wrong time to buy.
  - Big winners can often be identified before the major move.
  - Shift from buying weakness to buying strength.
  - Build a "leadership profile" from historically strong stocks.
  - SEPA is a precision-entry framework built from converging traits.
- System relevance:
  - Strong support for our hybrid approach: mechanical technical qualification first, then overlay scoring.
  - Suggests we should keep treating setup quality as a convergence problem, not as a single-indicator problem.
  - Reinforces the value of leader-quality metadata and future live/backtest parity work.

## Chapter 4 - Value Comes At A Price

- Pages 20-32
- File: [section_007.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_trade_like_a_stock_market_wizard_how_to_achieve_super_performance_in_stocks_in_any_market_pdf_a51586d490\section_007.txt)
- Main topics:
  - Traditional "cheap" thinking is often a trap in growth stocks.
  - P/E by itself is a weak filter for finding superperformers.
  - Bottom-fishing tends to lead investors into broken situations.
  - High P/E alone should not disqualify a stock if growth and demand are exceptional.
  - Market leaders often look too expensive to amateurs.
- System relevance:
  - Supports avoiding bargain-hunting logic in the live scanner.
  - Supports keeping the system focused on leaders near highs rather than "good deals."
  - Useful for PEAD and leader-lane work: a stock can still be buyable even if it looks expensive on static valuation.

## Chapter 5 - Trading With The Trend

- Pages 33-60
- File: [section_008.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_trade_like_a_stock_market_wizard_how_to_achieve_super_performance_in_stocks_in_any_market_pdf_a51586d490\section_008.txt)
- Most important pages:
  - Page 33: hard technical criteria first, fundamentals second
  - Pages 33-39: four-stage framework
  - Pages 34-35: avoid Stage 1 and bottom-fishing
  - Page 35: transition from Stage 1 to Stage 2
  - Page 36: Stage 2 accumulation
  - Pages 37-38: Stage 3 distribution and Stage 4 decline
  - Page 39: use stages for perspective, then time precisely with other tactics
- Main topics:
  - No long entries below a declining 200-day average.
  - Stage 2 is where the major move happens.
  - Avoid Stage 1 and bottom-picking.
  - A proper Stage 2 should show demand on rallies and lighter volume on pullbacks.
  - A candidate should usually have a prior rally of at least 25-30 percent off the 52-week low before being considered a real Stage 2 move.
  - Transition criteria include:
    - price above 150-day and 200-day moving averages
    - 150-day above 200-day
    - 200-day rising
    - higher highs and higher lows
- System relevance:
  - This is the cleanest book support for a Stage-2 / leader-quality overlay.
  - Strongly supports refusing long candidates below a declining 200-day.
  - Supports our view that dead-money names should be filtered out earlier.
  - Gives concrete ingredients for future leader-quality scoring:
    - above 150d / 200d
    - 150d > 200d
    - 200d turning up
    - prior meaningful rally
    - strong-volume up legs, quiet pullbacks

## Chapter 6 - Categories, Industry Groups, And Catalysts

- Pages 61-70
- File: [section_009.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_trade_like_a_stock_market_wizard_how_to_achieve_super_performance_in_stocks_in_any_market_pdf_a51586d490\section_009.txt)
- Main topics:
  - Company categories:
    - market leaders
    - top competitors
    - institutional favorites
    - turnaround situations
    - cyclical stocks
    - past leaders / laggards
  - Favorite type is the market leader.
  - Look for scalable growth and market-share gain.
  - Category killers deserve special attention.
  - Cookie-cutter rollouts can sustain long earnings runs.
  - Same-store sales are important for retail growth analysis.
- System relevance:
  - Strong support for explicit candidate typing.
  - Supports our interest in distinguishing true leaders from follow-on laggards.
  - Suggests the scanner could benefit from more structured business-model/catalyst tagging over time.
  - Reinforces that our catalyst lane should not just ask "is there news?" but "what kind of growth engine is this?"

## Chapter 7 - Fundamentals To Focus On

- Pages 71-84
- File: [section_010.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_trade_like_a_stock_market_wizard_how_to_achieve_super_performance_in_stocks_in_any_market_pdf_a51586d490\section_010.txt)
- Main topics:
  - Earnings anticipation and surprise
  - Analyst revisions
  - The "cockroach effect" after bad earnings
  - Why strong reported numbers attract attention
  - Strong companies usually show the strength in the numbers before the crowd fully catches up
- System relevance:
  - Strong support for PEAD and post-catalyst follow-through logic.
  - Supports checking estimate revisions and expectation context where available.
  - Suggests future candidate quality should weigh not just the beat itself but whether expectations are still moving the right way.

## Chapter 8 - Assessing Earnings Quality

- Pages 85-95
- File: [section_011.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_trade_like_a_stock_market_wizard_how_to_achieve_super_performance_in_stocks_in_any_market_pdf_a51586d490\section_011.txt)
- Main topics:
  - Nonoperating and nonrecurring income can distort results.
  - Beware massaged numbers.
  - Write-downs and revenue shifting matter.
  - Margin quality matters.
  - Cost-cutting can create misleading profitability.
- System relevance:
  - Supports making PEAD more selective than "headline beat."
  - Useful for future catalyst-quality checks:
    - beat quality
    - margin quality
    - absence of obvious low-quality adjustments
  - Helps explain why generic catalysts should stay out of the live lane.

## Chapter 9 - Follow The Leaders

- Pages 96-112
- File: [section_012.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_trade_like_a_stock_market_wizard_how_to_achieve_super_performance_in_stocks_in_any_market_pdf_a51586d490\section_012.txt)
- Most important pages:
  - Pages 96-99: leaders often turn before the indexes
  - Pages 99-101: lockout rally concept
  - Pages 101-104: best stocks make their lows first
  - Pages 104-108: 4-8 week leader window after a new bull phase begins
- Main topics:
  - Leading stocks often move first, before the broad indexes look healthy.
  - New highs among leaders matter.
  - The best stocks often make their lows first.
  - In an early bull phase, expanding new highs and a growing leader list are constructive.
  - A bottom-up view of the best relative-strength names can beat a top-down market view.
  - The best leaders may already be moving before sector strength becomes obvious.
  - Signs of a healthy early rally:
    - first wave of leaders emerge
    - setups proliferate
    - leaders give up relatively little ground
    - quick rebounds after weakness
    - distribution in major averages stays limited
- System relevance:
  - This is the strongest book support for a leader-breadth context signal.
  - Supports counting resilient, high-RS names and expanding new highs, not just index filters.
  - Useful for later refinement of the regime model when ADX is middling but leadership is improving.

## Chapter 10 - A Picture Is Worth A Million Dollars

- Pages 113-171
- File: [section_013.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_trade_like_a_stock_market_wizard_how_to_achieve_super_performance_in_stocks_in_any_market_pdf_a51586d490\section_013.txt)
- Main topics:
  - Charts reflect underlying cause/effect, not just decoration.
  - Consolidation periods matter.
  - Volatility contraction matters.
  - "Is the train on schedule?" is a key evaluation question.
  - Base quality and price/volume behavior matter more than superficial pattern naming.
- System relevance:
  - Strong support for the planned post-entry "on-schedule" framework.
  - Suggests we should monitor whether a stock is acting constructively after entry:
    - holding breakout area
    - controlled pullbacks
    - improving or stable relative strength
  - This is likely the best book chapter for refining the current time-kill-switch logic.

## Chapter 11 - Don't Just Buy What You Know

- Pages 172-179
- File: [section_014.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_trade_like_a_stock_market_wizard_how_to_achieve_super_performance_in_stocks_in_any_market_pdf_a51586d490\section_014.txt)
- Main topics:
  - Avoid familiarity bias.
  - A known company is not automatically a good stock.
  - A base should be allowed to develop properly.
  - Narrative comfort can lead investors away from better opportunities.
- System relevance:
  - Supports data-driven candidate selection over "recognizable company" bias.
  - Reinforces that the scanner should privilege setup quality over narrative familiarity.

## Chapter 12 - Risk Management Part 1: The Nature Of Risk

- Pages 180-187
- File: [section_015.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_trade_like_a_stock_market_wizard_how_to_achieve_super_performance_in_stocks_in_any_market_pdf_a51586d490\section_015.txt)
- Most important pages:
  - Pages 180-182: consistency and risk management over heroics
  - Pages 182-184: profits are principal, not house money
  - Pages 184-187: basics must be repeated and respected
- Main topics:
  - Consistency and risk management separate pros from amateurs.
  - Once profits are made, they become principal and deserve protection.
  - Good trading should feel disciplined and even boring.
  - Cutting losses is foundational, not a cliché to ignore.
  - Basics need constant reinforcement.
- System relevance:
  - Strong support for not loosening hard risk rails just to force more trades.
  - Supports our existing philosophy of safety-first automation.
  - Useful when evaluating whether a new live change is improving opportunity or just increasing recklessness.

## Chapter 13 - Risk Management Part 2: How To Deal With And Control Risk

- Pages 188-208
- File: [section_016.txt](S:\LLM_files\knowledge_agent\memory\extracted_sources\swing_trader_trade_like_a_stock_market_wizard_how_to_achieve_super_performance_in_stocks_in_any_market_pdf_a51586d490\section_016.txt)
- Most important pages:
  - Pages 188-191: discipline, habits, and contingency planning
  - Pages 191-193: initial stop-loss
  - Pages 193-195: reentry after a valid reset
  - Pages 195-197: selling at a profit
  - Pages 197-199: disaster plan
  - Pages 199-208: expectation, edge, and loss sizing vs gain profile
- Main topics:
  - Discipline becomes habit.
  - Contingency planning should be done before the open.
  - Four core contingency plans:
    - initial stop-loss
    - reentry
    - selling at a profit
    - disaster plan
  - A valid stock can stop you out and later reset into a better entry.
  - Once gain is a multiple of initial risk, it should rarely be allowed to turn into a loss.
  - Good risk management is tied to expected payoff, not hope.
- System relevance:
  - Strong support for our bracket / stop discipline.
  - Strong support for adding an explicit reentry framework later instead of treating one stop-out as final.
  - Strong support for overnight gap and disaster handling.
  - Useful for future starter-size plus add-on logic because it frames risk as managed, staged exposure rather than all-or-nothing conviction.

## Immediate Improvement Ideas Backed By This Book

### 1. Strengthen Stage-2 / leader-quality screening
- Best references:
  - Chapter 5, pages 33-39
  - Chapter 9, pages 96-104
- Why:
  - The book is very clear that major winners come from confirmed Stage 2, not cheap laggards.

### 2. Add a real post-entry "on-schedule" evaluator
- Best references:
  - Chapter 10, pages 113-171
  - Chapter 13, pages 191-197
- Why:
  - The system should distinguish constructive digestion from dead money or broken behavior.

### 3. Keep PEAD strict on catalyst quality
- Best references:
  - Chapter 7, pages 71-84
  - Chapter 8, pages 85-95
- Why:
  - Headline beats are not enough; quality and expectation context matter.

### 4. Add leader-breadth context to market evaluation
- Best references:
  - Chapter 9, pages 96-112
- Why:
  - Leaders often improve before the major averages fully confirm.

### 5. Build explicit reentry logic later
- Best references:
  - Chapter 13, pages 193-195
- Why:
  - Good names can stop out once, reset, and become valid again.

## Best Follow-Up Queries For Knowledge Agent

Use these when we want to drill deeper:
- `stage 2 transition criteria`
- `leader list before market turn`
- `on schedule stock action`
- `reentry after stop out`
- `protect profits risk management`
- `earnings quality massaged numbers`
- `category killer scalable growth`

