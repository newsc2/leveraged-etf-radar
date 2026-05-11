"""Tests for src.metrics — return math, daily-reset simulation, beta."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from src.metrics import (
    annualized_vol,
    compute_fund_metrics,
    get_prev_year_close,
    max_drawdown,
    naive_leverage_return,
    period_return,
    rolling_beta,
    rolling_correlation,
    simulate_daily_reset,
    ytd_return,
)


def _bdays_index(periods: int) -> pd.DatetimeIndex:
    """Robust business-day index of exactly `periods` entries ending today."""
    return pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=periods)


def _flat_series(value: float, periods: int = 100) -> pd.Series:
    idx = _bdays_index(periods)
    return pd.Series([value] * len(idx), index=idx, dtype=float)


def _trending_series(start: float, daily_pct: float, periods: int) -> pd.Series:
    idx = _bdays_index(periods)
    n = len(idx)
    vals = [start * (1 + daily_pct) ** i for i in range(n)]
    return pd.Series(vals, index=idx)


def _gbm_series(periods: int, mu: float, sigma: float, seed: int = 0, start: float = 100.0) -> pd.Series:
    np.random.seed(seed)
    idx = _bdays_index(periods)
    n = len(idx)
    rets = np.random.normal(mu, sigma, n)
    prices = start * np.exp(np.cumsum(rets))
    return pd.Series(prices, index=idx)


class TestPrevYearClose:
    def test_returns_last_value_of_prior_year(self) -> None:
        # Build a series spanning prior year + current year
        prior_year = datetime.now().year - 1
        prior = pd.date_range(f"{prior_year}-12-15", f"{prior_year}-12-31", freq="D")
        ytd = pd.date_range(f"{datetime.now().year}-01-02", f"{datetime.now().year}-01-15", freq="D")
        idx = prior.append(ytd)
        vals = list(range(len(prior))) + list(range(100, 100 + len(ytd)))
        s = pd.Series(vals, index=idx, dtype=float)
        baseline = get_prev_year_close(s)
        assert baseline == float(len(prior) - 1)  # last index in prior-year slice

    def test_falls_back_to_first_ytd_entry(self) -> None:
        ytd = pd.date_range(f"{datetime.now().year}-01-02", f"{datetime.now().year}-01-15", freq="D")
        s = pd.Series(range(10, 10 + len(ytd)), index=ytd, dtype=float)
        baseline = get_prev_year_close(s)
        assert baseline == 10.0

    def test_returns_none_for_empty(self) -> None:
        assert get_prev_year_close(pd.Series(dtype=float)) is None


class TestPeriodReturn:
    def test_zero_when_flat(self) -> None:
        s = _flat_series(100.0, 60)
        r = period_return(s, 30)
        assert r == pytest.approx(0.0)

    def test_positive_when_trending_up(self) -> None:
        s = _trending_series(100.0, 0.001, 60)
        r = period_return(s, 30)
        assert r is not None and r > 0

    def test_none_when_empty(self) -> None:
        assert period_return(pd.Series(dtype=float), 30) is None


class TestYtdReturn:
    def test_uses_prev_year_baseline(self) -> None:
        year = datetime.now().year
        prior = pd.date_range(f"{year-1}-12-15", f"{year-1}-12-31", freq="D")
        ytd = pd.date_range(f"{year}-01-02", f"{year}-01-30", freq="D")
        idx = prior.append(ytd)
        vals = [100.0] * len(prior) + [110.0] * len(ytd)
        s = pd.Series(vals, index=idx)
        r = ytd_return(s)
        assert r == pytest.approx(10.0)


class TestSimulateDailyReset:
    def test_flat_proxy_yields_zero(self) -> None:
        proxy = _flat_series(100.0, 252)
        assert simulate_daily_reset(proxy, 3, 252) == pytest.approx(0.0)

    def test_steady_uptrend_compounds_more_than_naive(self) -> None:
        # 400 bdays is comfortably > 365 calendar days for the lookback
        proxy = _trending_series(100.0, 0.001, 400)
        sim = simulate_daily_reset(proxy, 3, 252)
        naive = naive_leverage_return(proxy, 3, 365)
        assert sim is not None and naive is not None
        assert sim > naive  # daily reset compounds in trending markets

    def test_whipsaw_decays_below_naive(self) -> None:
        idx = _bdays_index(252)
        n = len(idx)
        # Even-length alternating ±2% — vol drag with no drift
        rets = np.array([0.02, -0.02] * (n // 2) + ([0.02] if n % 2 else []))
        rets = rets[:n]
        prices = 100 * np.cumprod(1 + rets)
        proxy = pd.Series(prices, index=idx)
        sim = simulate_daily_reset(proxy, 3, n - 1)
        assert sim is not None
        assert sim < -5  # 3x leveraged whipsaw decays substantially

    def test_none_for_empty(self) -> None:
        assert simulate_daily_reset(pd.Series(dtype=float), 3, 252) is None


class TestRollingBeta:
    def test_perfect_2x_replica(self) -> None:
        proxy = _gbm_series(300, mu=0, sigma=0.01, seed=0)
        proxy_rets = np.log(proxy / proxy.shift(1)).dropna()
        # LETF: 2x daily simple returns from proxy log-rets
        letf_daily = 2 * (np.exp(proxy_rets.values) - 1)
        letf_prices = 100 * np.cumprod(1 + letf_daily)
        letf = pd.Series(letf_prices, index=proxy.index[1:])
        beta = rolling_beta(letf, proxy, 60)
        assert beta is not None and 1.5 < beta < 2.5

    def test_none_when_too_short(self) -> None:
        s = _trending_series(100.0, 0.001, 30)
        assert rolling_beta(s, s, 60) is None

    def test_correlation_near_one_for_replica(self) -> None:
        proxy = _gbm_series(200, mu=0, sigma=0.01, seed=1)
        proxy_rets = np.log(proxy / proxy.shift(1)).dropna()
        letf_daily = 3 * (np.exp(proxy_rets.values) - 1)
        letf_prices = 100 * np.cumprod(1 + letf_daily)
        letf = pd.Series(letf_prices, index=proxy.index[1:])
        corr = rolling_correlation(letf, proxy, 60)
        assert corr is not None and corr > 0.95


class TestMaxDrawdown:
    def test_negative_after_drop(self) -> None:
        s = pd.Series([100, 110, 120, 80, 90, 95])
        dd = max_drawdown(s)
        assert dd is not None
        assert dd == pytest.approx(-(120 - 80) / 120 * 100)

    def test_none_when_empty(self) -> None:
        assert max_drawdown(pd.Series(dtype=float)) is None


class TestAnnualizedVol:
    def test_zero_for_flat(self) -> None:
        v = annualized_vol(_flat_series(100.0, 100))
        assert v == pytest.approx(0.0)

    def test_positive_for_noisy(self) -> None:
        s = _gbm_series(252, mu=0, sigma=0.01, seed=0)
        v = annualized_vol(s)
        assert v is not None and v > 5


class TestComputeFundMetrics:
    def test_all_none_for_empty_series(self) -> None:
        m = compute_fund_metrics("XYZ", pd.Series(dtype=float), None, 3.0)
        assert m.ticker == "XYZ"
        assert m.latest_close is None
        assert m.return_ytd is None

    def test_populates_when_data_present(self) -> None:
        proxy = _gbm_series(300, mu=0.0002, sigma=0.01, seed=2)
        proxy_rets = np.log(proxy / proxy.shift(1)).dropna()
        letf_daily = 3 * (np.exp(proxy_rets.values) - 1)
        letf_prices = 100 * np.cumprod(1 + letf_daily)
        letf = pd.Series(letf_prices, index=proxy.index[1:])
        m = compute_fund_metrics("FOO", letf, proxy, 3.0)
        assert m.latest_close is not None
        assert m.realized_beta_60d is not None
        assert m.simulated_daily_reset_1y is not None
