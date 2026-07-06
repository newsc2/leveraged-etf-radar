"""Tests for independent 9Sig new-plan simulation."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.ninesig_simulation import (
    NineSigAdjustedQuarter,
    build_ninesig_adjusted_quarters,
    simulate_ninesig_new_plan,
)


def _quarter(date: str, tqqq: float, agg: float = 100.0) -> NineSigAdjustedQuarter:
    ts = pd.Timestamp(date)
    period = ts.to_period("Q")
    return NineSigAdjustedQuarter(
        quarter=f"{period.year} Q{period.quarter}",
        date=date,
        tqqq_adjusted_close=tqqq,
        agg_adjusted_close=agg,
    )


def test_builds_quarterly_adjusted_close_rows_on_shared_dates() -> None:
    idx = pd.to_datetime(["2024-03-28", "2024-03-29", "2024-04-01", "2024-06-28"])
    tqqq = pd.Series([10.0, 11.0, 12.0, 13.0], index=idx)
    agg = pd.Series([100.0, 101.0, 102.0, 103.0], index=idx)

    rows = build_ninesig_adjusted_quarters(tqqq, agg, as_of=date(2024, 7, 1))

    assert [row.quarter for row in rows] == ["2024 Q1", "2024 Q2"]
    assert rows[0].date == "2024-03-29"
    assert rows[0].tqqq_adjusted_close == pytest.approx(11.0)
    assert rows[0].agg_adjusted_close == pytest.approx(101.0)


def test_excludes_incomplete_current_quarter() -> None:
    idx = pd.to_datetime(["2026-03-31", "2026-06-30", "2026-07-02"])
    tqqq = pd.Series([41.68, 81.0, 73.35], index=idx)
    agg = pd.Series([98.27, 98.65, 98.61], index=idx)

    rows = build_ninesig_adjusted_quarters(tqqq, agg, as_of=date(2026, 7, 6))

    assert [row.quarter for row in rows] == ["2026 Q1", "2026 Q2"]
    assert rows[-1].date == "2026-06-30"


def test_new_plan_starts_fresh_at_60_40_for_recent_start_years() -> None:
    quarters = [
        _quarter("2023-12-29", 90.0),
        _quarter("2024-03-29", 100.0),
        _quarter("2024-06-28", 110.0),
        _quarter("2024-09-30", 105.0),
        _quarter("2024-12-31", 115.0),
        _quarter("2025-03-31", 120.0),
        _quarter("2025-06-30", 130.0),
        _quarter("2025-09-30", 140.0),
        _quarter("2025-12-31", 150.0),
        _quarter("2026-03-31", 160.0),
        _quarter("2026-06-30", 170.0),
    ]

    for start_date in ("2024-01-01", "2025-01-01", "2026-01-01"):
        rows = simulate_ninesig_new_plan(quarters, start_date, 1_000_000)
        first = rows[0]

        assert first.rebalanceDate >= start_date
        assert first.actionType == "initial"
        assert first.tqqqAllocation == pytest.approx(0.60)
        assert first.aggAllocation == pytest.approx(0.40)
        assert first.tqqqValue == pytest.approx(600_000)
        assert first.aggValue == pytest.approx(400_000)
        assert first.signalTarget == pytest.approx(600_000)
        assert first.thirtyDownActive is False
        assert first.skippedSellSignals == 0
        assert first.qoqChange is None


def test_simulation_uses_adjusted_close_for_initial_share_counts() -> None:
    quarters = [_quarter("2024-03-29", 50.0, agg=80.0)]

    rows = simulate_ninesig_new_plan(quarters, "2024-01-01", 1_000_000)

    assert rows[0].tqqqShares == pytest.approx(600_000 / 50.0)
    assert rows[0].aggShares == pytest.approx(400_000 / 80.0)


def test_simulation_can_end_at_selected_quarter() -> None:
    quarters = [
        _quarter("2024-03-29", 100.0),
        _quarter("2024-06-28", 110.0),
        _quarter("2024-09-30", 120.0),
        _quarter("2024-12-31", 130.0),
    ]

    rows = simulate_ninesig_new_plan(
        quarters,
        "2024-01-01",
        1_000,
        end_date="2024-09-30",
    )

    assert [row.quarter for row in rows] == ["2024 Q1", "2024 Q2", "2024 Q3"]


def test_simulation_end_before_start_returns_initial_quarter_only() -> None:
    quarters = [
        _quarter("2024-03-29", 100.0),
        _quarter("2024-06-28", 110.0),
    ]

    rows = simulate_ninesig_new_plan(
        quarters,
        "2024-06-01",
        1_000,
        end_date="2024-03-29",
    )

    assert [row.quarter for row in rows] == ["2024 Q2"]
    assert rows[0].actionType == "initial"


def test_thirty_down_skips_two_sell_signals_then_resets_to_60_40() -> None:
    quarters = [
        _quarter("2024-03-29", 100.0),
        _quarter("2024-06-28", 50.0),
        _quarter("2024-09-30", 200.0),
        _quarter("2024-12-31", 220.0),
        _quarter("2025-03-31", 240.0),
    ]

    rows = simulate_ninesig_new_plan(quarters, "2024-01-01", 1_000)

    assert [row.actionType for row in rows] == [
        "initial",
        "buy_tqqq",
        "skip_sell_30_down",
        "skip_sell_30_down",
        "reset_60_40",
    ]
    assert rows[-1].tqqqAllocation == pytest.approx(0.60)
    assert rows[-1].aggAllocation == pytest.approx(0.40)


def test_buy_signal_that_would_zero_bonds_resets_to_60_40_when_not_thirty_down() -> None:
    quarters = [
        _quarter("2024-03-29", 100.0, agg=100.0),
        _quarter("2024-06-28", 80.0, agg=1.0),
    ]

    rows = simulate_ninesig_new_plan(quarters, "2024-01-01", 1_000)

    assert rows[1].actionType == "reset_60_40"
    assert rows[1].tqqqAllocation == pytest.approx(0.60)
    assert rows[1].aggAllocation == pytest.approx(0.40)
    assert rows[1].signalTarget == pytest.approx(rows[1].tqqqValue)
    assert rows[1].thirtyDownActive is False


def test_thirty_down_overrides_bond_zero_reset() -> None:
    quarters = [
        _quarter("2024-03-29", 100.0, agg=100.0),
        _quarter("2024-06-28", 20.0, agg=100.0),
    ]

    rows = simulate_ninesig_new_plan(quarters, "2024-01-01", 1_000)

    assert rows[1].actionType == "buy_tqqq_limited"
    assert rows[1].tqqqAllocation > 0.60
    assert rows[1].aggAllocation < 0.40
    assert rows[1].thirtyDownActive is True
    assert rows[1].signalTarget == pytest.approx(rows[1].tqqqValue)


def test_limited_buy_resets_target_and_does_not_retrigger_active_thirty_down() -> None:
    quarters = [
        _quarter("2024-03-29", 100.0),
        _quarter("2024-06-28", 10.0),
        _quarter("2024-09-30", 20.0),
        _quarter("2024-12-31", 22.0),
        _quarter("2025-03-31", 80.0),
    ]

    rows = simulate_ninesig_new_plan(quarters, "2024-01-01", 1_000)

    assert rows[1].actionType == "buy_tqqq_limited"
    assert rows[1].signalTarget == pytest.approx(rows[1].tqqqValue)
    assert rows[2].actionType == "skip_sell_30_down"
    assert rows[2].skippedSellSignals == 1
    assert rows[3].actionType == "skip_sell_30_down"
    assert rows[3].skippedSellSignals == 2
    assert rows[4].actionType == "reset_60_40"
