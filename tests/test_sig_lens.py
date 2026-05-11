"""Tests for src.sig_lens — quarter helpers, leverage-scaled targets, TQQQ 30-down."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.sig_lens import (
    compute_sig_lens,
    current_quarter_bounds,
    leverage_scaled_target,
    previous_quarter_end_close,
    quarter_progress,
)


class TestQuarterBounds:
    def test_q1(self) -> None:
        assert current_quarter_bounds(date(2026, 2, 14)) == (date(2026, 1, 1), date(2026, 3, 31))

    def test_q2(self) -> None:
        assert current_quarter_bounds(date(2026, 5, 10)) == (date(2026, 4, 1), date(2026, 6, 30))

    def test_q3(self) -> None:
        assert current_quarter_bounds(date(2026, 8, 1)) == (date(2026, 7, 1), date(2026, 9, 30))

    def test_q4(self) -> None:
        assert current_quarter_bounds(date(2026, 12, 31)) == (date(2026, 10, 1), date(2026, 12, 31))


class TestQuarterProgress:
    def test_partway_through(self) -> None:
        elapsed, total = quarter_progress(date(2026, 5, 10))
        assert elapsed == 40   # Apr 1 → May 10 inclusive
        assert total == 91     # Apr+May+Jun

    def test_first_day(self) -> None:
        elapsed, total = quarter_progress(date(2026, 4, 1))
        assert elapsed == 1
        assert total == 91


class TestPreviousQuarterEndClose:
    def test_returns_last_close_before_quarter(self) -> None:
        idx = pd.date_range("2026-03-25", "2026-05-01", freq="B")
        prices = pd.Series(range(100, 100 + len(idx)), index=idx, dtype=float)
        base = previous_quarter_end_close(prices, today=date(2026, 4, 15))
        # The last close before 2026-04-01 was on 2026-03-31 (Tuesday)
        expected = float(prices[prices.index == pd.Timestamp("2026-03-31")].iloc[-1])
        assert base == expected

    def test_returns_none_if_no_prior_data(self) -> None:
        idx = pd.date_range("2026-04-02", "2026-04-15", freq="B")
        prices = pd.Series(range(100, 100 + len(idx)), index=idx, dtype=float)
        base = previous_quarter_end_close(prices, today=date(2026, 4, 15))
        assert base is None


class TestLeverageScaledTarget:
    def test_long_canonical_tiers(self) -> None:
        assert leverage_scaled_target(1.0, "long")[0] == 3.0
        assert leverage_scaled_target(1.5, "long")[0] == 4.5
        assert leverage_scaled_target(2.0, "long")[0] == 6.0
        assert leverage_scaled_target(3.0, "long")[0] == 9.0

    def test_inverse_not_eligible(self) -> None:
        target, eligible, note = leverage_scaled_target(-3.0, "inverse")
        assert target is None and eligible is False
        assert note is not None and "long-only" in note

    def test_missing_metadata(self) -> None:
        assert leverage_scaled_target(None, "long")[1] is False
        assert leverage_scaled_target(3.0, None)[1] is False


class TestComputeSigLens:
    def test_eligible_long_3x(self) -> None:
        # Build a series where the close before Apr 1 is 100 and today is 109
        idx = pd.date_range("2026-03-15", "2026-05-10", freq="B")
        prices = np.linspace(100, 109, len(idx))
        s = pd.Series(prices, index=idx)
        lens = compute_sig_lens("TQQQ", s, leverage=3.0, direction="long",
                                today=date(2026, 5, 10))
        assert lens.eligible is True
        assert lens.is_canonical_9sig is True
        assert lens.quarterly_target_pct == 9.0
        assert lens.qtd_return_pct is not None
        assert lens.signal_gap_pct is not None

    def test_non_tqqq_3x_not_canonical(self) -> None:
        idx = pd.date_range("2026-03-15", "2026-05-10", freq="B")
        s = pd.Series(np.linspace(50, 53, len(idx)), index=idx)
        lens = compute_sig_lens("UPRO", s, leverage=3.0, direction="long",
                                today=date(2026, 5, 10))
        assert lens.eligible is True
        assert lens.is_canonical_9sig is False  # Only TQQQ is canonical
        assert lens.quarterly_target_pct == 9.0

    def test_inverse_not_eligible(self) -> None:
        idx = pd.date_range("2026-03-15", "2026-05-10", freq="B")
        s = pd.Series(np.linspace(40, 42, len(idx)), index=idx)
        lens = compute_sig_lens("SQQQ", s, leverage=-3.0, direction="inverse",
                                today=date(2026, 5, 10))
        assert lens.eligible is False
        assert lens.quarterly_target_pct is None


class TestThirtyDownDetection:
    def test_active_when_far_below_2yr_high(self) -> None:
        # 500 trading days of price stable at 100, then a 40% drop to 60
        idx = pd.date_range("2024-01-01", "2026-05-10", freq="B")
        prices = [100.0] * (len(idx) - 1) + [60.0]
        s = pd.Series(prices, index=idx, dtype=float)
        lens = compute_sig_lens("TQQQ", s, leverage=3.0, direction="long",
                                today=date(2026, 5, 10))
        assert lens.tqqq_30down_active is True
        assert lens.tqqq_30down_drawdown_pct is not None and lens.tqqq_30down_drawdown_pct <= -30

    def test_inactive_when_near_high(self) -> None:
        idx = pd.date_range("2024-01-01", "2026-05-10", freq="B")
        # Steadily rising — current is always the high
        prices = np.linspace(50, 100, len(idx))
        s = pd.Series(prices, index=idx, dtype=float)
        lens = compute_sig_lens("TQQQ", s, leverage=3.0, direction="long",
                                today=date(2026, 5, 10))
        assert lens.tqqq_30down_active is False


class TestSpikeWatch:
    def test_distance_from_100pct(self) -> None:
        # Q1 ends with price 50; current price 80 → QTD +60% → spike distance ~40pp.
        # Use ALL of March at price 50 (so the q1-end close is 50), then April+ at 80.
        idx = pd.date_range("2026-03-15", "2026-05-10", freq="B")
        prices = [50.0 if d.month == 3 else 80.0 for d in idx]
        s = pd.Series(prices, index=idx, dtype=float)
        lens = compute_sig_lens("TQQQ", s, leverage=3.0, direction="long",
                                today=date(2026, 5, 10))
        assert lens.qtd_return_pct == pytest.approx(60.0)
        # spike distance = 100% - 60% = 40pp
        assert lens.tqqq_spike_distance_pct is not None
        assert 30 < lens.tqqq_spike_distance_pct < 50
