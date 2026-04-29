"""
Live Hypothesis Backtester - PARAMETER_VARIATION verdict engine

Runs PARAMETER_VARIATION hypotheses from generate_live_hypotheses() through
BacktestEngine and stores verdicts in LearningDatabase/hypothesis_verdicts.

Called from: AutomatedTradingScheduler._run_live_cgh_pipeline() on Saturdays.
"""

import logging
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

MIN_SAMPLE_SIZE = 10
WIN_RATE_IMPROVEMENT_THRESHOLD = 0.05  # 5 pp improvement required for PASS


class LiveHypothesisBacktester:
    """
    Runs PARAMETER_VARIATION hypotheses through BacktestEngine and stores verdicts.
    """

    def __init__(self, learning_db_path: Optional[str] = None):
        """
        Initialize with LearningDatabase and BacktestEngine.

        Imports are deferred inside __init__ to avoid circular imports at module load.

        Args:
            learning_db_path: Path to learning.db. Defaults to ai_trader_data/learning.db.
        """
        from ai_trader.trading_agent.analytics.learning_database import LearningDatabase
        from ai_trader.trading_agent.analytics.backtest_engine import BacktestEngine

        db_path = learning_db_path or str(
            Path(__file__).resolve().parent.parent.parent / "ai_trader_data" / "learning.db"
        )
        self.ldb = LearningDatabase(db_path=db_path)
        self.backtest_engine = BacktestEngine(self.ldb)

    def run(
        self,
        hypotheses: List[Dict],
        window_start: str,
        window_end: str,
        trigger_trade_count: int,
    ) -> List[Dict]:
        """
        Run each hypothesis through BacktestEngine and store verdicts.

        Args:
            hypotheses: List of PARAMETER_VARIATION hypothesis dicts from generate_live_hypotheses().
            window_start: ISO date string - start of the trade window.
            window_end: ISO date string - end of the trade window.
            trigger_trade_count: Total closed trades at trigger time (stored in DB for audit).

        Returns:
            List of verdict dicts. Each has keys: hypothesis_id, title, proposed_change,
            confidence, verdict, backtest_win_rate, backtest_sample_size, baseline_win_rate, notes.
        """
        if not hypotheses:
            return []

        days_lookback = max(
            (date.fromisoformat(window_end) - date.fromisoformat(window_start)).days, 1
        )
        run_date = date.today().isoformat()

        baseline_result = self._run_single_backtest(
            {"conditions": {}, "hypothesis": "baseline"}, days_lookback
        )
        baseline_win_rate = baseline_result.get("win_rate", 0.0)
        logger.info(
            f"Live CGH baseline: win_rate={baseline_win_rate:.1%}, "
            f"sample={baseline_result.get('sample_size', 0)}"
        )

        verdicts = []
        for hyp in hypotheses:
            hyp_id = hyp.get("HypothesisID") or f"LCH_{str(uuid.uuid4())[:8].upper()}"
            proposed = hyp.get("ProposedChange", "")
            pattern = self._hypothesis_to_pattern(hyp)

            result = self._run_single_backtest(pattern, days_lookback)
            sample_size = result.get("sample_size", 0)
            bt_win_rate = result.get("win_rate", 0.0)

            if sample_size < MIN_SAMPLE_SIZE:
                verdict = "INSUFFICIENT"
                notes = f"Only {sample_size} matching trades (need >= {MIN_SAMPLE_SIZE})"
            elif bt_win_rate > baseline_win_rate + WIN_RATE_IMPROVEMENT_THRESHOLD:
                verdict = "PASS"
                delta = bt_win_rate - baseline_win_rate
                notes = f"Win rate improved {delta:+.1%} vs baseline"
            else:
                verdict = "FAIL"
                delta = bt_win_rate - baseline_win_rate
                notes = f"Win rate change {delta:+.1%} - below {WIN_RATE_IMPROVEMENT_THRESHOLD:.0%} threshold"

            logger.info(
                f"  {hyp_id}: {verdict} "
                f"(win_rate={bt_win_rate:.1%}, sample={sample_size}, baseline={baseline_win_rate:.1%})"
            )

            verdict_dict = {
                "hypothesis_id": hyp_id,
                "title": hyp.get("Title"),
                "proposed_change": proposed,
                "confidence": hyp.get("Confidence"),
                "verdict": verdict,
                "backtest_win_rate": bt_win_rate,
                "backtest_sample_size": sample_size,
                "baseline_win_rate": baseline_win_rate,
                "notes": notes,
            }

            try:
                self.ldb.write_hypothesis_verdict(
                    run_date=run_date,
                    trigger_trade_count=trigger_trade_count,
                    window_start=window_start,
                    window_end=window_end,
                    hypothesis_id=hyp_id,
                    hypothesis_type=hyp.get("Type", "PARAMETER_VARIATION"),
                    title=hyp.get("Title"),
                    proposed_change=proposed,
                    confidence=hyp.get("Confidence"),
                    backtest_win_rate=bt_win_rate,
                    backtest_sample_size=sample_size,
                    baseline_win_rate=baseline_win_rate,
                    verdict=verdict,
                    notes=notes,
                )
            except Exception as e:
                logger.error(f"  Failed to persist verdict for {hyp_id}: {e}")

            verdicts.append(verdict_dict)

        return verdicts

    def _run_single_backtest(self, pattern: Dict, days_lookback: int) -> Dict:
        """Run backtest for one pattern. Returns empty result dict on error."""
        try:
            return self.backtest_engine.backtest_pattern(
                pattern, days_lookback, min_sample_size=1
            )
        except Exception as e:
            logger.warning(f"  backtest_pattern error: {e}")
            return {"sample_size": 0, "win_rate": 0.0, "avg_return": 0.0}

    def _hypothesis_to_pattern(self, hyp: Dict) -> Dict:
        """
        Convert a PARAMETER_VARIATION hypothesis to a BacktestEngine pattern dict.

        Best-effort parse of 'param: old -> new' format. Returns empty conditions
        for unrecognized formats (causes all trades to match - same as baseline).
        """
        proposed = hyp.get("ProposedChange", "")
        conditions = self._parse_proposed_change(proposed)
        return {
            "conditions": conditions,
            "hypothesis": hyp.get("Title", proposed),
        }

    def _parse_proposed_change(self, proposed: str) -> Dict:
        """
        Parse 'parameter: old -> new' string into BacktestEngine condition dict.

        Handles: vix, adr, gap, conviction.
        Returns {} for unrecognized formats.
        """
        if not proposed:
            return {}

        try:
            after_arrow = proposed.split("->")[-1].strip()
            value_match = re.search(r"[\d.]+", after_arrow)
            if not value_match:
                return {}
            new_value = float(value_match.group())
        except Exception:
            return {}

        lower = proposed.lower()

        if "vix" in lower:
            return {"vix_at_entry": f"< {new_value}"}
        if "adr" in lower or "range" in lower:
            return {"adr_pct": f">= {new_value}"}
        if "gap" in lower:
            return {"gap_pct": f">= {new_value}"}
        if "conviction" in lower:
            return {"conviction": f">= {new_value}"}

        return {}
