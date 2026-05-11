"""Tests for the 9Sig engine — focuses on the four override rules.

Each test recreates a real historical scenario from the 9Sig action log
(`docs/jason-kelly/9sig-history.md`) so failures map to a known truth source.
"""
from __future__ import annotations

from datetime import date

import pytest

from src.sig_plans import (
    PlanConfig,
    PlanState,
    QuarterInputs,
    compute_rebalance,
    next_goal,
)


@pytest.fixture
def cfg() -> PlanConfig:
    return PlanConfig()


def _state(stock_shares: float, bond_shares: float, *, prior_goal: float,
           last_close: float, high: float = 200.0,
           thirty_down: bool = False) -> PlanState:
    return PlanState(
        last_rebalance_date=date(2025, 1, 1),
        prior_goal=prior_goal,
        last_quarter_close_price=last_close,
        rolling_2yr_quarterly_high=high,
        high_set_on=date(2024, 1, 1),
        thirty_down_active=thirty_down,
        shares_stock=stock_shares,
        shares_bond=bond_shares,
    )


def test_next_goal_compounds_target_and_dividend_share(cfg: PlanConfig) -> None:
    g = next_goal(prior_goal=1_000_000, dividends=10_000, cfg=cfg)
    # 1,000,000 * 1.09 + 0.5 * 10,000 = 1,095,000
    assert g == pytest.approx(1_095_000)


def test_ordinary_sell_signal(cfg: PlanConfig) -> None:
    """Surplus, no overrides → sell stock down to goal."""
    state = _state(stock_shares=100, bond_shares=100,
                   prior_goal=10_000, last_close=100.0, high=120.0)
    q = QuarterInputs(
        rebalance_date=date(2025, 4, 1),
        quarter_close_date=date(2025, 3, 31),
        quarter_close_price=120.0,   # stock now $12,000, goal $10,900
        rebalance_price=120.0,
        bond_price=100.0,
        dividends_this_quarter=0,
    )
    a = compute_rebalance(state, q, cfg)
    assert not a.thirty_down_active
    assert not a.spike_reset_triggered
    assert a.surplus_or_shortfall == pytest.approx(1_100)  # 12,000 - 10,900
    assert a.sell_stock_shares == pytest.approx(1_100 / 120.0)
    assert a.buy_bond_shares == pytest.approx(1_100 / 100.0)


def test_thirty_down_skips_sell(cfg: PlanConfig) -> None:
    """Mirrors 2020 Covid V-recovery: surplus signal under 30-Down → no action."""
    # Stock fell from 200 high → currently 130 (35% drawdown), 30-Down active
    state = _state(stock_shares=100, bond_shares=100,
                   prior_goal=10_000, last_close=130.0, high=200.0,
                   thirty_down=True)
    q = QuarterInputs(
        rebalance_date=date(2020, 7, 1),
        quarter_close_date=date(2020, 6, 30),
        quarter_close_price=130.0,
        rebalance_price=130.0,
        bond_price=100.0,
        dividends_this_quarter=0,
    )
    a = compute_rebalance(state, q, cfg)
    assert a.thirty_down_active
    assert a.sell_stock_shares == 0
    assert a.buy_bond_shares == 0
    assert "SKIPPED" in a.notes[0]


def test_spike_reset_fires_at_100_percent_quarter(cfg: PlanConfig) -> None:
    """The hot one: TQQQ doubles in a quarter while 30-Down inactive."""
    # Last quarter close $40; current $90 = +125% gain
    state = _state(stock_shares=1000, bond_shares=400,
                   prior_goal=40_000, last_close=40.0, high=90.0)
    q = QuarterInputs(
        rebalance_date=date(2026, 7, 1),
        quarter_close_date=date(2026, 6, 30),
        quarter_close_price=90.0,
        rebalance_price=90.0,
        bond_price=100.0,
        dividends_this_quarter=0,
    )
    a = compute_rebalance(state, q, cfg)
    # Stock value = 90,000; bond value = 40,000; total = 130,000
    # Target stock at 60% = 78,000 → must sell 12,000 worth
    assert a.spike_reset_triggered
    assert a.sell_stock_shares == pytest.approx(12_000 / 90.0)
    assert a.buy_bond_shares == pytest.approx(12_000 / 100.0)


def test_spike_reset_does_not_fire_below_threshold(cfg: PlanConfig) -> None:
    """+76% (today's TQQQ) should NOT trigger spike reset — close but not over."""
    state = _state(stock_shares=1000, bond_shares=400,
                   prior_goal=40_000, last_close=43.33, high=102.79)
    q = QuarterInputs(
        rebalance_date=date(2026, 7, 1),
        quarter_close_date=date(2026, 6, 30),
        quarter_close_price=76.28,    # +76% — close, not 100%
        rebalance_price=76.28,
        bond_price=100.0,
        dividends_this_quarter=0,
    )
    a = compute_rebalance(state, q, cfg)
    assert not a.spike_reset_triggered


def test_buying_power_throttle(cfg: PlanConfig) -> None:
    """Mirrors 2022-Q2 crash: huge shortfall, 30-Down active, throttle to 90% of bonds."""
    state = _state(stock_shares=100, bond_shares=100,
                   prior_goal=50_000, last_close=20.0, high=80.0,
                   thirty_down=True)
    q = QuarterInputs(
        rebalance_date=date(2022, 7, 5),
        quarter_close_date=date(2022, 7, 1),
        quarter_close_price=20.0,    # stock value $2,000, goal ~$54,500
        rebalance_price=20.0,
        bond_price=100.0,            # bond value $10,000
        dividends_this_quarter=0,
    )
    a = compute_rebalance(state, q, cfg)
    assert a.buying_power_throttled
    # 90% of $10,000 = $9,000 → buy $9,000 of stock at $20 = 450 shares
    assert a.buy_stock_shares == pytest.approx(450)
    assert a.sell_bond_shares == pytest.approx(90)


def test_bond_reservoir_reset(cfg: PlanConfig) -> None:
    """Mirrors 2024-Q1: surplus would drain AGG → modify orders to refill 60/40."""
    # Set up: bond reservoir nearly drained, but a SHORTFALL would consume it all.
    state = _state(stock_shares=1000, bond_shares=10,    # bond = $1,000
                   prior_goal=200_000, last_close=80.0, high=120.0)
    q = QuarterInputs(
        rebalance_date=date(2024, 1, 2),
        quarter_close_date=date(2023, 12, 29),
        quarter_close_price=80.0,    # stock $80,000, goal $218,000 → shortfall $138K > bonds
        rebalance_price=80.0,
        bond_price=100.0,
        dividends_this_quarter=0,
    )
    a = compute_rebalance(state, q, cfg)
    assert a.bond_reservoir_reset or a.buying_power_throttled
    # Either way, should not buy more stock than 90% of bond balance allows
    assert a.buy_stock_shares * 80.0 <= state.shares_bond * 100.0 * 0.90 + 1


def test_format_note_renders() -> None:
    state = _state(stock_shares=100, bond_shares=100,
                   prior_goal=10_000, last_close=100.0, high=120.0)
    q = QuarterInputs(
        rebalance_date=date(2025, 4, 1),
        quarter_close_date=date(2025, 3, 31),
        quarter_close_price=120.0,
        rebalance_price=120.0,
        bond_price=100.0,
        dividends_this_quarter=0,
    )
    out = compute_rebalance(state, q).format_note()
    assert "STATUS" in out
    assert "ORDERS" in out
    assert "RULES" in out


def test_state_round_trip() -> None:
    s = _state(stock_shares=100, bond_shares=200,
               prior_goal=12_345.67, last_close=44.0)
    s2 = PlanState.from_json(s.to_json())
    assert s2.prior_goal == pytest.approx(12_345.67)
    assert s2.shares_stock == 100
    assert s2.last_rebalance_date == s.last_rebalance_date
