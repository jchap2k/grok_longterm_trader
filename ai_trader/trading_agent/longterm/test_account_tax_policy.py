import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longterm.account_tax_policy import AccountTaxPolicy
from portfolio.portfolio_profile import PortfolioProfile


def test_tax_policy_allows_broad_parking_for_roth_and_paper_accounts():
    policy = AccountTaxPolicy()

    assert policy.can_execute_broad_parking(PortfolioProfile(account_strategy_mode="roth_ira")).allowed is True
    assert policy.can_execute_broad_parking(PortfolioProfile(account_strategy_mode="paper")).allowed is True


def test_tax_policy_blocks_broad_parking_and_rebalance_for_taxable_accounts():
    policy = AccountTaxPolicy()
    profile = PortfolioProfile(account_strategy_mode="taxable")

    parking = policy.can_execute_broad_parking(profile)
    rebalance = policy.can_execute_broad_rebalance(profile)

    assert parking.allowed is False
    assert parking.reason_code == "taxable_broad_parking_suppressed"
    assert "tax" in parking.reason.lower()
    assert rebalance.allowed is False
    assert rebalance.reason_code == "taxable_broad_rebalance_suppressed"


def test_tax_policy_treats_unspecified_account_mode_as_tax_cautious():
    policy = AccountTaxPolicy()

    result = policy.can_execute_broad_parking(PortfolioProfile())

    assert result.allowed is False
    assert result.reason_code == "unknown_tax_mode_broad_parking_suppressed"
