"""
ReasoningTracer - Writes step-by-step decision reasoning to daily JSONL files.

Each record captures the full conviction scoring chain for one decision,
making it easy to grep/tail without SQL queries.

File location: <log_root>/YYYY/Month_YYYY/decisions_MMDD.jsonl

Inspired by Dexter's scratchpad logging pattern. Best-effort writes only -
tracer failures NEVER interrupt trading logic.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]


class ReasoningTracer:
    """
    Writes conviction scoring steps + final decision to a daily JSONL file.

    One record per decision, one record per line.
    Never raises - all writes are best-effort so tracer failures
    cannot interrupt trading logic.

    Example record (one line in the JSONL file):
    {
      "timestamp": "2026-04-03T09:34:12.451",
      "symbol": "NVDA",
      "decision": "enter_long",
      "conviction": 8.5,
      "steps": [
        "Catalyst PEAD/earnings drift +3.0",
        "Duration multi-week expected +2.0",
        "FORCESWING all 5/5 conditions +2.0",
        "RS: outperforming SPY(+0.12) AND sector(+0.08) +1.5"
      ],
      "context": {
        "catalyst_type": "PEAD",
        "forceswing_conditions_met": 5,
        "regime_mode": "FULL"
      }
    }
    """

    DEFAULT_LOG_ROOT = "ai_trader/ai_trader_data/logs"

    def __init__(self, log_root: Optional[str] = None):
        """
        Args:
            log_root: Root log directory. Defaults to DEFAULT_LOG_ROOT.
                      For backtests, pass log_dir/run_id/trace to colocate
                      with backtest output.
        """
        self._log_root = Path(log_root or self.DEFAULT_LOG_ROOT)

    def _today_path(self) -> Path:
        """Return path to today's JSONL file, creating directories as needed."""
        now = datetime.now()
        month_dir = f"{_MONTH_NAMES[now.month]}_{now.year}"
        day_str = now.strftime("%m%d")
        path = self._log_root / str(now.year) / month_dir / f"decisions_{day_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def trace_decision(
        self,
        symbol: str,
        steps: List[str],
        decision: str,
        conviction: float = 0.0,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Append one decision record to today's JSONL file.

        Args:
            symbol: Stock ticker (e.g., "NVDA")
            steps: Ordered reasoning steps from conviction scorer
                   (e.g., ["Catalyst PEAD +3.0", "FORCESWING 5/5 +2.0"])
            decision: Final decision label. Common values:
                      "enter_long"    - agent decided to buy
                      "skip"          - agent decided to pass
                      "score_catalyst"- raw scorer output (pre-agent decision)
                      "reflect_warn"  - self-reflection gate flagged an issue
                      "llm_reflection"- post-session Grok reasoning batch (future)
            conviction: Final conviction score on 0-10 scale
            context: Optional market context dict (price, vix, regime, etc.)
                     Include whatever context is available at call site.
        """
        try:
            record: Dict[str, Any] = {
                "timestamp": datetime.now().isoformat(),
                "symbol": symbol,
                "decision": decision,
                "conviction": conviction,
                "steps": steps,
            }
            if context:
                record["context"] = context

            path = self._today_path()
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")

        except Exception as e:
            # Never let tracer errors affect trading
            logger.debug("[ReasoningTracer] Write failed (non-fatal): %s", e)
