"""Account tax-mode guardrails for broad parking and rebalance behavior."""

from __future__ import annotations

from dataclasses import dataclass

from portfolio.portfolio_profile import PortfolioProfile


@dataclass(frozen=True)
class AccountTaxPolicyDecision:
    allowed: bool
    reason_code: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "reason_code": self.reason_code,
            "reason": self.reason,
        }


class AccountTaxPolicy:
    """Conservative policy for actions that create broad taxable-account churn."""

    def can_execute_broad_parking(self, profile: PortfolioProfile) -> AccountTaxPolicyDecision:
        mode = _mode(profile)
        if profile.is_non_taxable:
            return AccountTaxPolicyDecision(
                allowed=True,
                reason_code="non_taxable_broad_parking_allowed",
                reason="Account mode is non-taxable, so broad idle-cash parking may be planned.",
            )
        if not mode:
            return AccountTaxPolicyDecision(
                allowed=False,
                reason_code="unknown_tax_mode_broad_parking_suppressed",
                reason="Account tax mode is unspecified; suppress broad idle-cash parking until the profile is explicit.",
            )
        return AccountTaxPolicyDecision(
            allowed=False,
            reason_code="taxable_broad_parking_suppressed",
            reason="Taxable account mode suppresses broad idle-cash parking to avoid unnecessary tax churn.",
        )

    def can_execute_broad_rebalance(self, profile: PortfolioProfile) -> AccountTaxPolicyDecision:
        mode = _mode(profile)
        if profile.is_non_taxable:
            return AccountTaxPolicyDecision(
                allowed=True,
                reason_code="non_taxable_broad_rebalance_allowed",
                reason="Account mode is non-taxable, so broad rebalance review may be planned.",
            )
        if not mode:
            return AccountTaxPolicyDecision(
                allowed=False,
                reason_code="unknown_tax_mode_broad_rebalance_suppressed",
                reason="Account tax mode is unspecified; suppress broad rebalance planning until the profile is explicit.",
            )
        return AccountTaxPolicyDecision(
            allowed=False,
            reason_code="taxable_broad_rebalance_suppressed",
            reason="Taxable account mode suppresses broad rebalance planning unless a symbol-specific sell is separately justified.",
        )


def _mode(profile: PortfolioProfile) -> str:
    return str(profile.account_strategy_mode or "").lower().replace("-", "_").strip()


__all__ = ["AccountTaxPolicy", "AccountTaxPolicyDecision"]
