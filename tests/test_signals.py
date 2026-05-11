"""Tests for src.signals — indicators, classifiers, FundSignals aggregator."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.signals import (
    classify_ob_os,
    classify_reversion,
    classify_trend,
    classify_vol,
    compute_signals,
    days_above,
    drawdown_from_max,
    is_new_high,
    is_new_low,
    moving_average,
    pct_above_min,
    pct_distance,
    return_zscore,
    rsi_wilder,
    vol_ratio,
)


def _bdays(n: int) -> pd.DatetimeIndex:
    # Anchor to a fixed start so `periods=N` always returns exactly N business days
    # (the end= variant has been known to return N-1 on weekend boundaries).
    return pd.bdate_range(start="2020-01-02", periods=n)


def _flat(value: float, n: int = 100) -> pd.Series:
    idx = _bdays(n)
    return pd.Series([value] * len(idx), index=idx, dtype=float)


def _trending(start: float, daily_pct: float, n: int) -> pd.Series:
    idx = _bdays(n)
    vals = [start * (1 + daily_pct) ** i for i in range(len(idx))]
    return pd.Series(vals, index=idx)


class TestMovingAverage:
    def test_simple_average(self) -> None:
        s = pd.Series([10.0, 12.0, 14.0, 16.0, 18.0])
        assert moving_average(s, 5) == pytest.approx(14.0)

    def test_returns_none_when_short(self) -> None:
        assert moving_average(_flat(10.0, 5), 20) is None

    def test_pct_distance(self) -> None:
        assert pct_distance(110.0, 100.0) == pytest.approx(10.0)
        assert pct_distance(90.0, 100.0) == pytest.approx(-10.0)
        assert pct_distance(None, 100.0) is None
        assert pct_distance(100.0, 0) is None


class TestRSI:
    def test_strong_uptrend_pushes_rsi_high(self) -> None:
        s = _trending(100.0, 0.005, 100)  # +0.5%/day for 100 days
        rsi = rsi_wilder(s, 14)
        assert rsi is not None and rsi > 80

    def test_strong_downtrend_pushes_rsi_low(self) -> None:
        s = _trending(100.0, -0.005, 100)
        rsi = rsi_wilder(s, 14)
        assert rsi is not None and rsi < 20

    def test_flat_series_is_neutral(self) -> None:
        s = _flat(100.0, 60)
        rsi = rsi_wilder(s, 14)
        assert rsi is not None and 40 < rsi < 60  # neutral-ish

    def test_returns_none_when_too_short(self) -> None:
        assert rsi_wilder(_flat(100.0, 5), 14) is None

    def test_known_alternating_series(self) -> None:
        # Pure +1/-1 alternation → avg_gain == avg_loss → RSI 50
        vals = [100.0]
        for i in range(40):
            vals.append(vals[-1] + (1 if i % 2 == 0 else -1))
        s = pd.Series(vals, index=_bdays(len(vals)))
        rsi = rsi_wilder(s, 14)
        assert rsi is not None and 40 <= rsi <= 60


class TestHighLowProximity:
    def test_drawdown_from_max(self) -> None:
        s = pd.Series([100, 110, 120, 80, 90], index=_bdays(5), dtype=float)
        dd = drawdown_from_max(s, period=10)
        assert dd is not None and dd == pytest.approx(-25.0)  # 90/120 - 1 = -25%

    def test_pct_above_min(self) -> None:
        s = pd.Series([50, 60, 70, 80, 90], index=_bdays(5), dtype=float)
        pa = pct_above_min(s, period=10)
        assert pa is not None and pa == pytest.approx(80.0)  # 90/50 - 1 = +80%

    def test_new_high(self) -> None:
        s = pd.Series([100, 110, 120, 105, 130], index=_bdays(5), dtype=float)
        assert is_new_high(s, 5) is True
        s2 = pd.Series([100, 110, 120, 105, 115], index=_bdays(5), dtype=float)
        assert is_new_high(s2, 5) is False

    def test_new_low(self) -> None:
        s = pd.Series([100, 90, 80, 95, 70], index=_bdays(5), dtype=float)
        assert is_new_low(s, 5) is True


class TestTrendClassification:
    def test_strong_uptrend(self) -> None:
        assert classify_trend(price=120, ma_50=110, ma_200=100) == "strong_uptrend"

    def test_uptrend_pullback(self) -> None:
        assert classify_trend(price=105, ma_50=110, ma_200=100) == "uptrend_pullback"

    def test_downtrend(self) -> None:
        assert classify_trend(price=80, ma_50=90, ma_200=100) == "downtrend"

    def test_rebound_attempt(self) -> None:
        # Price > 50d but still < 200d
        assert classify_trend(price=105, ma_50=100, ma_200=110) == "rebound_attempt"

    def test_insufficient_data(self) -> None:
        assert classify_trend(None, 110, 100) == "insufficient_data"


class TestObOsClassification:
    def test_deeply_oversold_from_rsi(self) -> None:
        assert classify_ob_os(rsi=20, dist_from_50d=None, pct_above_52w_low=20, price_pct_1y=15) == "deeply_oversold"

    def test_deeply_oversold_from_52w_low(self) -> None:
        assert classify_ob_os(rsi=40, dist_from_50d=None, pct_above_52w_low=3, price_pct_1y=30) == "deeply_oversold"

    def test_oversold_from_percentile(self) -> None:
        assert classify_ob_os(rsi=35, dist_from_50d=None, pct_above_52w_low=20, price_pct_1y=8) == "oversold"

    def test_very_stretched_from_dist50(self) -> None:
        assert classify_ob_os(rsi=70, dist_from_50d=40, pct_above_52w_low=100, price_pct_1y=99) == "very_stretched"

    def test_stretched_from_rsi(self) -> None:
        assert classify_ob_os(rsi=72, dist_from_50d=10, pct_above_52w_low=80, price_pct_1y=90) == "stretched"

    def test_neutral(self) -> None:
        assert classify_ob_os(rsi=55, dist_from_50d=5, pct_above_52w_low=40, price_pct_1y=50) == "neutral"


class TestReversionClassification:
    def test_washed_out_stabilizing(self) -> None:
        label = classify_reversion(
            drawdown_52w=-40, rsi=42, return_5d=2.0,
            dist_from_ma_20=3.0, dist_from_ma_200=-15.0,
        )
        assert label == "washed_out_stabilizing"

    def test_falling_knife(self) -> None:
        label = classify_reversion(
            drawdown_52w=-50, rsi=25, return_5d=-8.0,
            dist_from_ma_20=-12.0, dist_from_ma_200=-30.0,
        )
        assert label == "falling_knife"

    def test_none(self) -> None:
        label = classify_reversion(
            drawdown_52w=-10, rsi=55, return_5d=1.0,
            dist_from_ma_20=1.0, dist_from_ma_200=2.0,
        )
        assert label == "none"


class TestVolClassification:
    def test_expanding(self) -> None:
        assert classify_vol(1.6) == "vol_expanding"

    def test_compressed(self) -> None:
        assert classify_vol(0.5) == "vol_compressed"

    def test_neutral(self) -> None:
        assert classify_vol(1.0) == "none"


class TestComputeSignals:
    def test_empty_series_yields_insufficient(self) -> None:
        s = compute_signals("XYZ", pd.Series(dtype=float))
        assert s.ticker == "XYZ"
        assert s.trend_regime == "insufficient_data"
        assert s.rsi_14 is None

    def test_strong_uptrend_series(self) -> None:
        # 300 days of steady uptrend
        prices = _trending(100.0, 0.003, 300)
        s = compute_signals("UP", prices)
        assert s.trend_regime == "strong_uptrend"
        assert s.ma_20 is not None and s.ma_50 is not None and s.ma_200 is not None
        # Dist > 20% confirms price is meaningfully above the 200-day MA
        assert s.dist_from_ma_200 is not None and s.dist_from_ma_200 > 20
        assert s.rsi_14 is not None and s.rsi_14 > 70
        assert s.ob_os_label in ("stretched", "very_stretched")
        assert s.new_high_252d is True

    def test_oversold_series_at_52w_low(self) -> None:
        # 252 days of decline ending at the minimum
        prices = _trending(100.0, -0.003, 252)
        s = compute_signals("DOWN", prices)
        assert s.trend_regime == "downtrend"
        assert s.ob_os_label in ("oversold", "deeply_oversold")
        assert s.new_low_252d is True
        assert s.pct_above_52w_low is not None and s.pct_above_52w_low < 1

    def test_tracking_weirdness_flag(self) -> None:
        prices = _trending(100.0, 0.001, 100)
        s = compute_signals("X", prices, actual_minus_simulated_1y=-15.0)
        assert s.tracking_weirdness is True
        s2 = compute_signals("Y", prices, actual_minus_simulated_1y=-2.0)
        assert s2.tracking_weirdness is False

    def test_reasons_populated_for_popping_series(self) -> None:
        prices = _trending(100.0, 0.003, 300)
        s = compute_signals("UP", prices)
        assert "new 1Y high" in s.reasons_popping or "new 65d high" in s.reasons_popping


class TestSupportFunctions:
    def test_days_above(self) -> None:
        prices = pd.Series([100, 102, 104, 106], index=_bdays(4), dtype=float)
        ma = pd.Series([99, 100, 101, 102], index=_bdays(4), dtype=float)
        d = days_above(prices, ma)
        assert d == 4

    def test_return_zscore_extreme(self) -> None:
        idx = _bdays(40)
        vals = [100.0 + i * 0.1 for i in range(39)]
        vals.append(vals[-1] * 1.10)   # huge +10% spike on the last day
        s = pd.Series(vals, index=idx)
        z = return_zscore(s, 20)
        assert z is not None and z > 5

    def test_vol_ratio(self) -> None:
        # Flat then volatile → ratio > 1
        idx = _bdays(300)
        np.random.seed(0)
        long_seg = np.zeros(200)
        short_seg = np.random.normal(0, 0.02, 100)
        rets = np.concatenate([long_seg, short_seg])
        prices = 100 * np.exp(np.cumsum(rets))
        s = pd.Series(prices, index=idx)
        vr = vol_ratio(s, 20, 252)
        assert vr is not None and vr > 1.5
