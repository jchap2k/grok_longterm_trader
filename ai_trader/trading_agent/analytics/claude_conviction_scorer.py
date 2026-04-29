"""
Claude's Evidence-Based Conviction Scorer

Replaces hardcoded conviction rules with data-driven insights from Feb 15, 2026 analysis.
Based on 111 simulated trades with actual outcomes.

Key Findings:
- Medium gaps (1-3%) are optimal: 79.3% win rate vs 54.5% for 3-5% gaps
- Gap direction doesn't matter much (negative slightly better)
- 2% target is well-calibrated
- Current system rules are already good, just need refinement
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class ClaudeConvictionScorer:
    """
    Evidence-based conviction scoring using Claude's statistical analysis.

    Replaces subjective rules with proven patterns from backtested data.
    """

    def __init__(self, tracer=None):
        """
        Initialize with empirically-validated thresholds.

        Args:
            tracer: Optional ReasoningTracer instance. If provided, each call to
                    score_catalyst() will emit a structured JSONL record with the
                    reasoning steps. Omit (or pass None) for backward compatibility.
        """
        self._tracer = tracer

        # Gap size scoring (from actual win rates)
        self.gap_scores = {
            'medium': 2.0,      # 1-3%: 79.3% win rate - BEST
            'very_large': 1.0,  # >5%: 68.4% win rate - Good
            'small': 0.5,       # <1%: 59.6% win rate - OK
            'large': -1.0,      # 3-5%: 54.5% win rate - WORST (danger zone!)
            'zero': -2.0,       # ~0%: Poor - no momentum
            'mega': -1.5        # >8%: Poor - too volatile
        }

        # VIX regime scoring (from previous analysis)
        self.vix_scores = {
            'optimal': 1.5,     # 16-19: 70.4% win rate
            'normal': 1.0,      # 19-22: 53.8% win rate
            'high': 0.5,        # >25: Restrict trading window
            'low': 0.0          # <16: Less volatility = less opportunity
        }

    def calculate_gap_bucket(self, gap_pct: float) -> str:
        """
        Categorize gap size into empirically-validated buckets.

        Args:
            gap_pct: Gap percentage (can be negative)

        Returns:
            Gap bucket category
        """
        abs_gap = abs(gap_pct)

        if abs_gap < 0.5:
            return 'zero'  # No momentum
        elif abs_gap < 1.0:
            return 'small'  # 59.6% win rate
        elif abs_gap <= 3.0:
            return 'medium'  # 79.3% win rate - SWEET SPOT
        elif abs_gap <= 5.0:
            return 'large'  # 54.5% win rate - DANGER ZONE
        elif abs_gap <= 8.0:
            return 'very_large'  # 68.4% win rate
        else:
            return 'mega'  # Poor risk/reward

    def calculate_vix_regime(self, vix_level: float) -> str:
        """Categorize VIX into empirically-validated regimes."""
        if vix_level < 16:
            return 'low'
        elif vix_level <= 19:
            return 'optimal'  # Sweet spot
        elif vix_level <= 22:
            return 'normal'
        else:
            return 'high'

    def score_catalyst(self,
                      catalyst_data: Optional[Dict[str, Any]] = None,
                      regime_detail: Optional[Dict] = None,
                      trade_id: Optional[str] = None,
                      min_conviction_full: Optional[float] = None,
                      # Legacy day-trade args (deprecated - kept for backward compat)
                      gap_pct: float = 0.0,
                      vix_level: float = 20.0,
                      catalyst_age_hours: float = 12.0,
                      entry_time_hour: int = 8,
                      catalyst_type: str = 'earnings') -> Dict[str, Any]:
        """
        Calculate conviction score for a swing trade candidate.

        SWING RUBRIC (preferred - pass catalyst_data dict):
          Component 1: Catalyst quality  (0-3.0)  PEAD=3, Upgrade=2, Breakout=1
          Component 2: Catalyst duration (0-2.0)  multi-week=2, multi-day=1
          Component 3: FORCESWING signal (0-2.0)  all 5=2, 3-4=1, <3=0
          Component 4: RS vs SPY/sector  (0-1.5)  both pos=1.5, one pos=0.75
          Component 5: R/R ratio         (0-1.0)  >=1.75:1=1.0, >=1.25:1=0.5
          Component 6: Liquidity         (0-0.5)  ADV>500K + spread<0.5%=0.5
          Total max = 10.0. Min: 7.0 (full mode), 8.0 (reduced mode)

        catalyst_data keys:
            catalyst_type (str): 'PEAD', 'TIER1_UPGRADE', 'SECTOR_ROTATION', 'BREAKOUT', 'OTHER'
            forceswing_conditions_met (int): 0-5 FORCESWING conditions met
            rs_vs_spy (float): relative strength vs SPY (positive = outperforming)
            rs_vs_sector (float): relative strength vs sector ETF
            stop_pct (float): initial stop as decimal (e.g. 0.04 = 4%)
            target_pct (float): target as decimal (e.g. 0.07 = 7%)
            adv_millions (float): average daily volume in millions
            spread_pct (float): bid-ask spread as decimal
        """
        if catalyst_data is not None:
            return self._score_swing_rubric(catalyst_data, regime_detail, trade_id, min_conviction_full)
        # Legacy fallback (intraday-era scoring - will be removed once all callers updated)
        logger.warning(
            "score_catalyst called without catalyst_data dict - using deprecated legacy scorer"
        )
        return self._score_legacy(gap_pct, vix_level, catalyst_age_hours, entry_time_hour)

    def _score_swing_rubric(self,
                            catalyst_data: Dict[str, Any],
                            regime_detail: Optional[Dict],
                            trade_id: Optional[str],
                            min_conviction_full: Optional[float] = None) -> Dict[str, Any]:
        """6-component swing conviction rubric per active_rules.txt."""
        reasoning = []
        total = 0.0

        # --- Component 1: Catalyst Quality (0-3.0) ---
        c_type = str(catalyst_data.get('catalyst_type', 'OTHER')).upper()
        if c_type in ('PEAD', 'POST_EARNINGS', 'EARNINGS_DRIFT'):
            c1 = 3.0
            reasoning.append(f"Catalyst PEAD/earnings drift +{c1}")
        elif c_type in ('TIER1_UPGRADE', 'ANALYST_UPGRADE', 'SECTOR_ROTATION', 'ROTATION'):
            c1 = 2.0
            reasoning.append(f"Catalyst Tier1-upgrade/rotation +{c1}")
        elif c_type in ('BREAKOUT', 'TECHNICAL_BREAKOUT', 'MOMENTUM'):
            c1 = 1.0
            reasoning.append(f"Catalyst breakout/momentum +{c1}")
        else:
            c1 = 0.5
            reasoning.append(f"Catalyst type={c_type} (other) +{c1}")
        total += c1

        # --- Component 2: Catalyst Duration Quality (0-2.0) ---
        # PEAD and major upgrades have multi-week duration; breakouts are shorter
        if c_type in ('PEAD', 'POST_EARNINGS', 'EARNINGS_DRIFT', 'TIER1_UPGRADE', 'ANALYST_UPGRADE'):
            c2 = 2.0
            reasoning.append(f"Duration multi-week expected +{c2}")
        elif c_type in ('SECTOR_ROTATION', 'ROTATION', 'BREAKOUT', 'TECHNICAL_BREAKOUT'):
            c2 = 1.0
            reasoning.append(f"Duration multi-day expected +{c2}")
        else:
            c2 = 0.0
            reasoning.append(f"Duration unknown/single-day +{c2}")
        total += c2

        # --- Component 3: FORCESWING Signal Quality (0-2.0) ---
        conditions_met = int(catalyst_data.get('forceswing_conditions_met', 0))
        if conditions_met >= 5:
            c3 = 2.0
            reasoning.append(f"FORCESWING all {conditions_met}/5 conditions +{c3}")
        elif conditions_met >= 3:
            c3 = 1.0
            reasoning.append(f"FORCESWING {conditions_met}/5 conditions +{c3}")
        else:
            c3 = 0.0
            reasoning.append(f"FORCESWING only {conditions_met}/5 conditions - weak signal +{c3}")
        total += c3

        # --- Component 4: Relative Strength vs SPY and Sector (0-1.5) ---
        rs_spy = float(catalyst_data.get('rs_vs_spy', 0.0))
        rs_sector = float(catalyst_data.get('rs_vs_sector', 0.0))
        spy_positive = rs_spy > 0
        sector_positive = rs_sector > 0
        if spy_positive and sector_positive:
            c4 = 1.5
            reasoning.append(f"RS: outperforming SPY({rs_spy:+.2f}) AND sector({rs_sector:+.2f}) +{c4}")
        elif spy_positive or sector_positive:
            c4 = 0.75
            reasoning.append(f"RS: outperforming one benchmark (SPY={rs_spy:+.2f}, sector={rs_sector:+.2f}) +{c4}")
        else:
            c4 = 0.0
            reasoning.append(f"RS: underperforming both (SPY={rs_spy:+.2f}, sector={rs_sector:+.2f}) +{c4}")
        total += c4

        # --- Component 5: R/R Ratio (0-1.0) ---
        stop_pct = float(catalyst_data.get('stop_pct', 0.04))
        target_pct = float(catalyst_data.get('target_pct', 0.07))
        rr_ratio = (target_pct / stop_pct) if stop_pct > 0 else 0.0
        if rr_ratio >= 1.75:
            c5 = 1.0
            reasoning.append(f"R/R {rr_ratio:.2f}:1 (target {target_pct*100:.1f}%/stop {stop_pct*100:.1f}%) +{c5}")
        elif rr_ratio >= 1.25:
            c5 = 0.5
            reasoning.append(f"R/R {rr_ratio:.2f}:1 acceptable +{c5}")
        else:
            c5 = 0.0
            reasoning.append(f"R/R {rr_ratio:.2f}:1 insufficient +{c5}")
        total += c5

        # --- Component 6: Liquidity (0-0.5) ---
        adv = float(catalyst_data.get('adv_millions', 0.0))
        spread = float(catalyst_data.get('spread_pct', 1.0))
        if adv >= 0.5 and spread <= 0.005:  # ADV >= 500K shares, spread <= 0.5%
            c6 = 0.5
            reasoning.append(f"Liquidity ADV={adv:.1f}M spread={spread*100:.2f}% +{c6}")
        elif adv >= 0.2:  # ADV >= 200K
            c6 = 0.25
            reasoning.append(f"Liquidity ADV={adv:.1f}M (marginal) +{c6}")
        else:
            c6 = 0.0
            reasoning.append(f"Liquidity ADV={adv:.1f}M too thin +{c6}")
        total += c6

        # --- Additive overlay: Leader quality (0-0.5) ---
        leader_quality_score = float(catalyst_data.get('leader_quality_score', 0.0) or 0.0)
        if leader_quality_score >= 4.0:
            c7 = 0.5
        elif leader_quality_score >= 2.5:
            c7 = 0.25
        else:
            c7 = 0.0
        reasoning.append(f"Leader quality score={leader_quality_score:.2f} +{c7}")
        total += c7

        # Clamp to 0-10
        conviction = max(0.0, min(10.0, total))

        # Entry threshold per regime mode
        mode = regime_detail.get('mode', 'full') if regime_detail else 'full'
        min_conviction = 8.0 if mode.lower() == 'reduced' else (min_conviction_full if min_conviction_full is not None else 7.0)

        result = {
            'conviction': conviction,
            'reasoning': reasoning,
            'should_trade': conviction >= min_conviction,
            'min_conviction': min_conviction,
            'regime_mode': mode,
            'trade_id': trade_id,
            'components': {
                'catalyst_quality': c1,
                'catalyst_duration': c2,
                'forceswing_signal': c3,
                'relative_strength': c4,
                'risk_reward': c5,
                'liquidity': c6,
                'leader_quality': c7,
            }
        }

        # Emit to reasoning tracer if wired up (best-effort, non-blocking)
        if self._tracer and trade_id:
            _sim_date = catalyst_data.get("sim_date")  # injected by BacktestHarness; None in live
            self._tracer.trace_decision(
                symbol=trade_id,
                steps=reasoning,
                decision="score_catalyst",
                conviction=conviction,
                context={
                    "catalyst_type": c_type,
                    "forceswing_conditions_met": conditions_met,
                    "rs_vs_spy": rs_spy,
                    "rs_vs_sector": rs_sector,
                    "rr_ratio": round(rr_ratio, 3),
                    "regime_mode": mode,
                    "min_conviction": min_conviction,
                    "should_trade": conviction >= min_conviction,
                    "sim_date": _sim_date,
                    "components": {
                        "catalyst_quality": c1,
                        "catalyst_duration": c2,
                        "forceswing_signal": c3,
                        "relative_strength": c4,
                        "risk_reward": c5,
                        "liquidity": c6,
                    },
                }
            )

        return result

    def _score_legacy(self,
                      gap_pct: float,
                      vix_level: float,
                      catalyst_age_hours: float,
                      entry_time_hour: int) -> Dict[str, Any]:
        """Legacy day-trade conviction scoring (deprecated). Returns swing-compatible dict."""
        conviction = 7.0
        reasoning = []

        gap_bucket = self.calculate_gap_bucket(gap_pct)
        gap_adjustment = self.gap_scores[gap_bucket]
        conviction += gap_adjustment
        reasoning.append(f"[LEGACY] Gap {abs(gap_pct):.1f}% ({gap_bucket}) {gap_adjustment:+.1f}")

        vix_regime = self.calculate_vix_regime(vix_level)
        vix_adjustment = self.vix_scores[vix_regime]
        conviction += vix_adjustment
        reasoning.append(f"[LEGACY] VIX {vix_level:.1f} ({vix_regime}) {vix_adjustment:+.1f}")

        if catalyst_age_hours < 2:
            conviction += 0.5
            reasoning.append("[LEGACY] Fresh catalyst +0.5")
        elif catalyst_age_hours > 24:
            conviction -= 0.5
            reasoning.append("[LEGACY] Stale catalyst -0.5")

        conviction = max(0.0, min(10.0, conviction))
        return {
            'conviction': conviction,
            'gap_bucket': gap_bucket,
            'vix_regime': vix_regime,
            'reasoning': reasoning,
            'should_trade': conviction >= 6.0,
            'gap_score': gap_adjustment,
            'vix_score': vix_adjustment,
        }

    def should_skip_trade(self, gap_pct: float, vix_level: float) -> tuple:
        """
        Swing mode: gap-based hard filters are intraday (gap-fade) concepts.
        FORCESWING entries are breakout/momentum entries, not gap fades.
        All gap-bucket filters are bypassed for swing mode.

        Returns:
            (should_skip, reason) - always (False, "") for swing entries
        """
        return (False, "")


# Singleton instance
_scorer = None

def get_conviction_scorer(tracer=None) -> ClaudeConvictionScorer:
    """
    Get singleton conviction scorer instance.

    Args:
        tracer: Optional ReasoningTracer. Only used when first creating the
                singleton - subsequent calls with a tracer are ignored if the
                singleton already exists. Wire the tracer at agent startup.
    """
    global _scorer
    if _scorer is None:
        _scorer = ClaudeConvictionScorer(tracer=tracer)
    return _scorer
