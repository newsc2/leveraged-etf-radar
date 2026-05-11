"""Per-fund metric computation.

Returns over standard windows, max drawdown, realized volatility,
rolling beta and correlation vs proxy, and the leveraged-ETF-specific
"actual vs naive vs simulated daily-reset" tracking comparison.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class FundMetrics:
    """All scalar metrics for a single fund."""
    ticker: str
    latest_close: float | None = None
    return_1d: float | None = None
    return_1w: float | None = None
    return_1m: float | None = None
    return_3m: float | None = None
    return_ytd: float | None = None
    return_1y: float | None = None
    return_3y: float | None = None
    return_5y: float | None = None
    return_10y: float | None = None
    max_drawdown_1y: float | None = None
    max_drawdown_full: float | None = None
    realized_vol_1y: float | None = None  # annualized %
    realized_beta_60d: float | None = None
    realized_beta_20d: float | None = None
    realized_corr_60d: float | None = None
    realized_corr_20d: float | None = None
    naive_leverage_return_1y: float | None = None
    simulated_daily_reset_1y: float | None = None
    actual_minus_naive_1y: float | None = None
    actual_minus_simulated_1y: float | None = None
    avg_daily_dollar_volume: float | None = None  # USD, mean over last 90 trading days
    days_of_data: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_prev_year_close(series: pd.Series) -> float | None:
    """YTD baseline = last close of previous year (Yahoo convention).

    Critical for LETFs: small baseline drift compounds with leverage.
    """
    if series is None or series.empty:
        return None
    year_start: datetime = datetime(datetime.now().year, 1, 1)
    prev: pd.Series = series[series.index < year_start]
    if not prev.empty:
        return float(prev.iloc[-1])
    ytd: pd.Series = series[series.index >= year_start]
    return float(ytd.iloc[0]) if not ytd.empty else None


def _trading_days_back(series: pd.Series, days: int) -> float | None:
    """Return value `days` trading days before the latest, or None if too short."""
    if series is None or series.empty or len(series) <= days:
        return None
    return float(series.iloc[-days - 1])


def _calendar_lookback(series: pd.Series, calendar_days: int) -> float | None:
    """Return last value at/before (latest - calendar_days)."""
    if series is None or series.empty:
        return None
    cutoff = series.index[-1] - pd.Timedelta(days=calendar_days)
    earlier = series[series.index <= cutoff]
    return float(earlier.iloc[-1]) if not earlier.empty else None


def max_drawdown(series: pd.Series) -> float | None:
    """Peak-to-trough drawdown, expressed as a negative percent (-25.0 = -25%)."""
    if series is None or series.empty or len(series) < 2:
        return None
    cummax: pd.Series = series.cummax()
    dd: pd.Series = (series / cummax) - 1.0
    return float(dd.min() * 100)


def annualized_vol(series: pd.Series, lookback_days: int = 252) -> float | None:
    """Annualized realized volatility from daily simple returns, in percent."""
    if series is None or series.empty or len(series) < 30:
        return None
    recent = series.iloc[-lookback_days:] if len(series) > lookback_days else series
    rets = recent.pct_change().dropna()
    if rets.empty:
        return None
    return float(rets.std() * math.sqrt(252) * 100)


def daily_log_returns(series: pd.Series) -> pd.Series:
    """Daily log returns; safer for rolling beta than simple returns."""
    if series is None or series.empty:
        return pd.Series(dtype=float)
    ratios: pd.Series = series / series.shift(1)
    logs: pd.Series = pd.Series(np.log(ratios.values), index=ratios.index)
    return logs.dropna()


def rolling_beta(fund: pd.Series, proxy: pd.Series, window: int) -> float | None:
    """Most-recent rolling beta of fund vs proxy (daily log returns, `window` days)."""
    fr: pd.Series = daily_log_returns(fund)
    pr: pd.Series = daily_log_returns(proxy)
    aligned: pd.DataFrame = pd.concat([fr, pr], axis=1, join="inner").dropna()
    if len(aligned) < window:
        return None
    fr_w = aligned.iloc[-window:, 0]
    pr_w = aligned.iloc[-window:, 1]
    pvar = float(pr_w.var())
    if pvar == 0 or math.isnan(pvar):
        return None
    cov = float(fr_w.cov(pr_w))
    return cov / pvar


def rolling_correlation(fund: pd.Series, proxy: pd.Series, window: int) -> float | None:
    fr: pd.Series = daily_log_returns(fund)
    pr: pd.Series = daily_log_returns(proxy)
    aligned: pd.DataFrame = pd.concat([fr, pr], axis=1, join="inner").dropna()
    if len(aligned) < window:
        return None
    return float(aligned.iloc[-window:, 0].corr(aligned.iloc[-window:, 1]))


def simulate_daily_reset(
    proxy: pd.Series,
    leverage: float,
    lookback_days: int = 252,
) -> float | None:
    """Cumulative return of an idealized daily-reset LETF over the last `lookback_days`.

    For each day: leveraged_daily_return = leverage * proxy_simple_return.
    Then compound. Returned as percent (10.0 = +10%).
    Captures the volatility-drag effect that distinguishes LETF reality
    from naive `(1 + cum_proxy_return) * leverage`.
    """
    if proxy is None or proxy.empty or len(proxy) < 2:
        return None
    recent = proxy.iloc[-(lookback_days + 1):] if len(proxy) > lookback_days else proxy
    daily = recent.pct_change().dropna()
    if daily.empty:
        return None
    leveraged = 1.0 + leverage * daily
    prod_val: float = float(leveraged.prod())  # type: ignore[arg-type]
    return (prod_val - 1.0) * 100


def naive_leverage_return(proxy: pd.Series, leverage: float, calendar_days: int) -> float | None:
    """Leverage × cumulative proxy return over the period (no daily reset)."""
    if proxy is None or proxy.empty:
        return None
    base = _calendar_lookback(proxy, calendar_days)
    if base is None or base == 0:
        return None
    actual_return = (float(proxy.iloc[-1]) / base) - 1.0
    return float(leverage * actual_return * 100)


def period_return(series: pd.Series, calendar_days: int) -> float | None:
    """Calendar-day percent return over `calendar_days`."""
    if series is None or series.empty:
        return None
    base = _calendar_lookback(series, calendar_days)
    if base is None or base == 0:
        return None
    return float(((series.iloc[-1] / base) - 1.0) * 100)


def ytd_return(series: pd.Series) -> float | None:
    if series is None or series.empty:
        return None
    base = get_prev_year_close(series)
    if base is None or base == 0:
        return None
    return float(((series.iloc[-1] / base) - 1.0) * 100)


# ---------------------------------------------------------------------------
# Per-fund aggregator
# ---------------------------------------------------------------------------

def compute_fund_metrics(
    ticker: str,
    fund_series: pd.Series,
    proxy_series: pd.Series | None,
    leverage: float | None,
    volume_series: pd.Series | None = None,
) -> FundMetrics:
    """Roll up every scalar metric for one fund. Resilient to missing data."""
    m = FundMetrics(ticker=ticker)
    if fund_series is None or fund_series.empty:
        return m

    m.days_of_data = len(fund_series)
    m.latest_close = float(fund_series.iloc[-1])
    m.return_1d = period_return(fund_series, 1)
    m.return_1w = period_return(fund_series, 7)
    m.return_1m = period_return(fund_series, 30)
    m.return_3m = period_return(fund_series, 90)
    m.return_ytd = ytd_return(fund_series)
    m.return_1y = period_return(fund_series, 365)
    m.return_3y = period_return(fund_series, 365 * 3)
    m.return_5y = period_return(fund_series, 365 * 5)
    m.return_10y = period_return(fund_series, 365 * 10)
    m.max_drawdown_full = max_drawdown(fund_series)
    one_year_window = fund_series.iloc[-252:] if len(fund_series) > 252 else fund_series
    m.max_drawdown_1y = max_drawdown(one_year_window)
    m.realized_vol_1y = annualized_vol(fund_series)

    # Liquidity proxy: average daily dollar volume over last ~90 trading days.
    if volume_series is not None and not volume_series.empty:
        recent_vol = volume_series.iloc[-90:] if len(volume_series) > 90 else volume_series
        recent_px = fund_series.reindex(recent_vol.index, method="ffill")
        valid = (recent_vol > 0) & recent_px.notna()
        if valid.any():
            m.avg_daily_dollar_volume = float((recent_vol[valid] * recent_px[valid]).mean())

    if proxy_series is not None and not proxy_series.empty:
        m.realized_beta_60d = rolling_beta(fund_series, proxy_series, 60)
        m.realized_beta_20d = rolling_beta(fund_series, proxy_series, 20)
        m.realized_corr_60d = rolling_correlation(fund_series, proxy_series, 60)
        m.realized_corr_20d = rolling_correlation(fund_series, proxy_series, 20)
        if leverage is not None:
            m.naive_leverage_return_1y = naive_leverage_return(proxy_series, leverage, 365)
            m.simulated_daily_reset_1y = simulate_daily_reset(proxy_series, leverage, 252)
            if m.return_1y is not None and m.naive_leverage_return_1y is not None:
                m.actual_minus_naive_1y = m.return_1y - m.naive_leverage_return_1y
            if m.return_1y is not None and m.simulated_daily_reset_1y is not None:
                m.actual_minus_simulated_1y = m.return_1y - m.simulated_daily_reset_1y
    return m


def metrics_to_dict(m: FundMetrics) -> dict[str, Any]:
    """Flatten a FundMetrics dataclass into a dict (for table rendering)."""
    return {k: getattr(m, k) for k in m.__dataclass_fields__}


def metrics_dataframe(metrics: dict[str, FundMetrics]) -> pd.DataFrame:
    """Convert a {ticker: FundMetrics} dict into a single DataFrame for filtering/sorting."""
    rows = [metrics_to_dict(m) for m in metrics.values()]
    return pd.DataFrame(rows)
