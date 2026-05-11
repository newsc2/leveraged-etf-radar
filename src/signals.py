"""Per-fund technical signals — analytical labels, not investment advice.

Computes a `FundSignals` per ticker covering:
  - Trend regime (price vs 20/50/100/200d MAs + regime classification)
  - Overbought / oversold (RSI 14, z-score, 1Y percentile, drawdown from 52W)
  - High/low proximity (ATH/ATL, 52W high/low, new 20/65/252d high/low flags)
  - Momentum (5d, 20d returns, 20d acceleration, "popping" composite score)
  - Mean-reversion ("washed-out stabilizing" vs "falling knife")
  - Volatility pressure (20d vol vs 1Y vol, expanding/compressed)
  - Tracking weirdness (large gap between actual and simulated daily-reset)

All thresholds live in the THRESHOLDS dict at the top of the file —
easy to tune. Returns None for any metric where there isn't enough
price history (newer funds, short series).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Tunable thresholds (easy to find and tweak)
# ---------------------------------------------------------------------------

THRESHOLDS: dict[str, float] = {
    # Overbought / oversold (RSI 14)
    "rsi_oversold": 30.0,
    "rsi_deeply_oversold": 25.0,
    "rsi_stretched": 70.0,
    "rsi_very_stretched": 80.0,
    # Distance-from-50d-MA thresholds for stretched labels
    "stretched_above_50d_pct": 20.0,
    "very_stretched_above_50d_pct": 35.0,
    # Price percentile within 1Y for oversold label
    "oversold_percentile": 10.0,
    # Distance from 52W low for deeply_oversold label
    "near_52w_low_pct": 5.0,
    # Drawdown from 52W high threshold to be considered "washed out"
    "washed_out_drawdown_pct": -25.0,
    # Tracking-weirdness threshold (|actual - simulated daily-reset| pp)
    "tracking_weirdness_pp": 12.0,
    # Vol ratio (20d / 1Y) thresholds
    "vol_expanding": 1.4,
    "vol_compressed": 0.7,
}


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class FundSignals:
    """All signal/indicator state for a single fund."""
    ticker: str
    # Trend
    ma_20: float | None = None
    ma_50: float | None = None
    ma_100: float | None = None
    ma_200: float | None = None
    dist_from_ma_20: float | None = None      # % distance, signed
    dist_from_ma_50: float | None = None
    dist_from_ma_100: float | None = None
    dist_from_ma_200: float | None = None
    trend_regime: str = "insufficient_data"
    days_above_50: int | None = None
    days_above_200: int | None = None
    # Overbought / oversold
    rsi_14: float | None = None
    return_zscore_20d: float | None = None
    price_percentile_1y: float | None = None  # 0..100
    drawdown_52w: float | None = None         # % from 52W high (≤0)
    pct_above_52w_low: float | None = None    # %
    ob_os_label: str = "neutral"
    # High/low proximity
    pct_from_ath: float | None = None
    pct_above_atl: float | None = None
    pct_from_52w_high: float | None = None
    new_high_20d: bool = False
    new_high_65d: bool = False
    new_high_252d: bool = False
    new_low_20d: bool = False
    new_low_65d: bool = False
    new_low_252d: bool = False
    # Momentum
    return_5d: float | None = None
    return_20d: float | None = None
    acceleration_20d: float | None = None     # 20d return minus prior 20d return
    popping_score: float | None = None
    # Mean reversion
    reversion_label: str = "none"             # washed_out_stabilizing | falling_knife | none
    # Vol pressure
    vol_ratio_20d_vs_1y: float | None = None
    vol_pressure_label: str = "none"          # vol_expanding | vol_compressed | none
    tracking_weirdness: bool = False
    # Human-readable reason strings used by the Signal Board scan
    reasons_popping: list[str] = field(default_factory=list)
    reasons_oversold: list[str] = field(default_factory=list)
    reasons_stretched: list[str] = field(default_factory=list)
    reasons_near_lows: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def moving_average(prices: pd.Series, period: int) -> float | None:
    """Last simple moving average value over `period` trading days."""
    if prices is None or prices.empty or len(prices) < period:
        return None
    return float(prices.iloc[-period:].mean())


def moving_average_series(prices: pd.Series, period: int) -> pd.Series:
    """Full rolling MA series (NaN until period reached)."""
    if prices is None or prices.empty:
        return pd.Series(dtype=float)
    return prices.rolling(window=period, min_periods=period).mean()


def pct_distance(price: float | None, ma: float | None) -> float | None:
    if price is None or ma is None or ma == 0:
        return None
    return float((price / ma - 1.0) * 100)


def days_above(prices: pd.Series, ma_series: pd.Series) -> int | None:
    """Count consecutive trailing days where price > MA. Returns negative if
    price has been below; None if there's no MA data to compare against."""
    if prices is None or prices.empty or ma_series.empty:
        return None
    aligned = pd.concat([prices, ma_series], axis=1, join="inner").dropna()
    if aligned.empty:
        return None
    above = aligned.iloc[:, 0] > aligned.iloc[:, 1]
    # Walk back from the end while sign is stable
    count = 0
    current_state = bool(above.iloc[-1])
    for v in reversed(above.values):
        if bool(v) != current_state:
            break
        count += 1
    return count if current_state else -count


def rsi_wilder(prices: pd.Series, period: int = 14) -> float | None:
    """Classic Wilder RSI. Uses EWM with alpha=1/period."""
    if prices is None or prices.empty or len(prices) < period + 1:
        return None
    delta = prices.diff().dropna()
    if delta.empty:
        return None
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1.0 / period, adjust=False).mean()
    last_gain = float(avg_gain.iloc[-1])
    last_loss = float(avg_loss.iloc[-1])
    if last_loss == 0:
        return 100.0 if last_gain > 0 else 50.0
    rs = last_gain / last_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def return_zscore(prices: pd.Series, period: int = 20) -> float | None:
    """Z-score of the LAST daily return against the prior `period` returns."""
    if prices is None or prices.empty or len(prices) < period + 2:
        return None
    rets = prices.pct_change().dropna()
    if len(rets) < period + 1:
        return None
    sample = rets.iloc[-(period + 1):-1]   # exclude the latest return itself
    sigma = float(sample.std())
    if sigma == 0 or math.isnan(sigma):
        return None
    last_ret = float(rets.iloc[-1])
    mu = float(sample.mean())
    return (last_ret - mu) / sigma


def price_percentile(prices: pd.Series, period: int = 252) -> float | None:
    """Percentile rank (0..100) of the latest price within the trailing window."""
    if prices is None or prices.empty:
        return None
    window = prices.iloc[-period:] if len(prices) > period else prices
    if len(window) < 5:
        return None
    last = float(window.iloc[-1])
    rank = float((window <= last).sum())
    return rank / len(window) * 100


def drawdown_from_max(prices: pd.Series, period: int = 252) -> float | None:
    """% drop from the trailing `period`-day max (≤0)."""
    if prices is None or prices.empty:
        return None
    window = prices.iloc[-period:] if len(prices) > period else prices
    if window.empty:
        return None
    high = float(window.max())
    if high == 0:
        return None
    return float((window.iloc[-1] / high - 1.0) * 100)


def pct_above_min(prices: pd.Series, period: int = 252) -> float | None:
    """% above the trailing `period`-day min (≥0)."""
    if prices is None or prices.empty:
        return None
    window = prices.iloc[-period:] if len(prices) > period else prices
    if window.empty:
        return None
    low = float(window.min())
    if low == 0:
        return None
    return float((window.iloc[-1] / low - 1.0) * 100)


def trailing_return(prices: pd.Series, period_days: int) -> float | None:
    """% return over the last `period_days` trading days."""
    if prices is None or prices.empty or len(prices) <= period_days:
        return None
    base = float(prices.iloc[-period_days - 1])
    if base == 0:
        return None
    return float((prices.iloc[-1] / base - 1.0) * 100)


def is_new_high(prices: pd.Series, period_days: int) -> bool:
    if prices is None or prices.empty or len(prices) < period_days:
        return False
    window = prices.iloc[-period_days:]
    return bool(float(window.iloc[-1]) >= float(window.max()))


def is_new_low(prices: pd.Series, period_days: int) -> bool:
    if prices is None or prices.empty or len(prices) < period_days:
        return False
    window = prices.iloc[-period_days:]
    return bool(float(window.iloc[-1]) <= float(window.min()))


def annualized_vol(prices: pd.Series, period: int = 20) -> float | None:
    """Annualized realized volatility from the last `period` daily returns."""
    if prices is None or prices.empty or len(prices) < period + 1:
        return None
    rets = prices.iloc[-period - 1:].pct_change().dropna()
    if len(rets) < 2:
        return None
    return float(rets.std() * math.sqrt(252) * 100)


def vol_ratio(prices: pd.Series, short: int = 20, long: int = 252) -> float | None:
    """Ratio of short-window vol to long-window vol. >1 = expanding, <1 = compressed."""
    short_v = annualized_vol(prices, period=short)
    long_v = annualized_vol(prices, period=long)
    if short_v is None or long_v is None or long_v == 0:
        return None
    return short_v / long_v


# ---------------------------------------------------------------------------
# Classifiers
# ---------------------------------------------------------------------------

def classify_trend(
    price: float | None,
    ma_50: float | None,
    ma_200: float | None,
) -> str:
    if price is None or ma_50 is None or ma_200 is None:
        return "insufficient_data"
    if price > ma_50 and ma_50 > ma_200:
        return "strong_uptrend"
    if ma_50 > ma_200 and ma_200 < price <= ma_50:
        return "uptrend_pullback"
    # Rebound checked BEFORE downtrend: both have price < ma_200, but rebound
    # additionally has price > ma_50 (early recovery signature).
    if price > ma_50 and price < ma_200:
        return "rebound_attempt"
    if price < ma_200 and ma_50 < ma_200:
        return "downtrend"
    return "neutral"


def classify_ob_os(
    rsi: float | None,
    dist_from_50d: float | None,
    pct_above_52w_low: float | None,
    price_pct_1y: float | None,
) -> str:
    """Single ob/os label, highest-severity wins."""
    th = THRESHOLDS
    if rsi is not None and rsi <= th["rsi_deeply_oversold"]:
        return "deeply_oversold"
    if pct_above_52w_low is not None and pct_above_52w_low <= th["near_52w_low_pct"]:
        return "deeply_oversold"
    if rsi is not None and rsi <= th["rsi_oversold"]:
        return "oversold"
    if price_pct_1y is not None and price_pct_1y <= th["oversold_percentile"]:
        return "oversold"
    if rsi is not None and rsi >= th["rsi_very_stretched"]:
        return "very_stretched"
    if dist_from_50d is not None and dist_from_50d >= th["very_stretched_above_50d_pct"]:
        return "very_stretched"
    if rsi is not None and rsi >= th["rsi_stretched"]:
        return "stretched"
    if dist_from_50d is not None and dist_from_50d >= th["stretched_above_50d_pct"]:
        return "stretched"
    return "neutral"


def classify_reversion(
    drawdown_52w: float | None,
    rsi: float | None,
    return_5d: float | None,
    dist_from_ma_20: float | None,
    dist_from_ma_200: float | None,
) -> str:
    th = THRESHOLDS
    if drawdown_52w is None or rsi is None:
        return "none"
    # Washed-out but stabilizing: large drawdown, RSI recovering from oversold,
    # price has crossed back above 20d MA, but still below 200d MA.
    if (drawdown_52w <= th["washed_out_drawdown_pct"]
            and rsi >= th["rsi_oversold"]
            and rsi <= 50
            and dist_from_ma_20 is not None and dist_from_ma_20 > 0
            and dist_from_ma_200 is not None and dist_from_ma_200 < 0):
        return "washed_out_stabilizing"
    # Falling knife: near 52W low, deeply oversold RSI but still falling (5d red).
    if (rsi <= th["rsi_oversold"]
            and return_5d is not None and return_5d < -3.0
            and dist_from_ma_20 is not None and dist_from_ma_20 < -5.0):
        return "falling_knife"
    return "none"


def classify_vol(vol_r: float | None) -> str:
    th = THRESHOLDS
    if vol_r is None:
        return "none"
    if vol_r >= th["vol_expanding"]:
        return "vol_expanding"
    if vol_r <= th["vol_compressed"]:
        return "vol_compressed"
    return "none"


# ---------------------------------------------------------------------------
# Top-level aggregator
# ---------------------------------------------------------------------------

def compute_signals(
    ticker: str,
    prices: pd.Series,
    actual_minus_simulated_1y: float | None = None,
) -> FundSignals:
    """Compute every signal for one fund. Returns a FundSignals dataclass."""
    s = FundSignals(ticker=ticker)
    if prices is None or prices.empty:
        return s

    last_px = float(prices.iloc[-1])

    # MAs
    s.ma_20 = moving_average(prices, 20)
    s.ma_50 = moving_average(prices, 50)
    s.ma_100 = moving_average(prices, 100)
    s.ma_200 = moving_average(prices, 200)
    s.dist_from_ma_20 = pct_distance(last_px, s.ma_20)
    s.dist_from_ma_50 = pct_distance(last_px, s.ma_50)
    s.dist_from_ma_100 = pct_distance(last_px, s.ma_100)
    s.dist_from_ma_200 = pct_distance(last_px, s.ma_200)
    s.trend_regime = classify_trend(last_px, s.ma_50, s.ma_200)
    s.days_above_50 = days_above(prices, moving_average_series(prices, 50))
    s.days_above_200 = days_above(prices, moving_average_series(prices, 200))

    # OB/OS
    s.rsi_14 = rsi_wilder(prices, 14)
    s.return_zscore_20d = return_zscore(prices, 20)
    s.price_percentile_1y = price_percentile(prices, 252)
    s.drawdown_52w = drawdown_from_max(prices, 252)
    s.pct_above_52w_low = pct_above_min(prices, 252)
    s.ob_os_label = classify_ob_os(
        s.rsi_14, s.dist_from_ma_50, s.pct_above_52w_low, s.price_percentile_1y,
    )

    # High/low
    full_max = float(prices.max())
    full_min = float(prices.min())
    s.pct_from_ath = (last_px / full_max - 1.0) * 100 if full_max else None
    s.pct_above_atl = (last_px / full_min - 1.0) * 100 if full_min else None
    s.pct_from_52w_high = s.drawdown_52w  # same thing
    s.new_high_20d = is_new_high(prices, 20)
    s.new_high_65d = is_new_high(prices, 65)
    s.new_high_252d = is_new_high(prices, 252)
    s.new_low_20d = is_new_low(prices, 20)
    s.new_low_65d = is_new_low(prices, 65)
    s.new_low_252d = is_new_low(prices, 252)

    # Momentum
    s.return_5d = trailing_return(prices, 5)
    s.return_20d = trailing_return(prices, 20)
    prior_20d = trailing_return(prices.iloc[:-20], 20) if len(prices) > 41 else None
    s.acceleration_20d = (
        s.return_20d - prior_20d if s.return_20d is not None and prior_20d is not None else None
    )
    s.popping_score = _popping_score(s)

    # Mean reversion
    s.reversion_label = classify_reversion(
        s.drawdown_52w, s.rsi_14, s.return_5d, s.dist_from_ma_20, s.dist_from_ma_200,
    )

    # Vol pressure
    s.vol_ratio_20d_vs_1y = vol_ratio(prices, 20, 252)
    s.vol_pressure_label = classify_vol(s.vol_ratio_20d_vs_1y)

    # Tracking weirdness — fed in by the caller (lives on FundMetrics).
    if actual_minus_simulated_1y is not None:
        s.tracking_weirdness = abs(actual_minus_simulated_1y) >= THRESHOLDS["tracking_weirdness_pp"]

    # Signal Board reason strings
    _build_reasons(s)
    return s


def _popping_score(s: FundSignals) -> float | None:
    """Transparent weighted score: high recent return + new highs + above-trend
    bonuses; deep-drawdown penalty. Higher = more "popping"."""
    if s.return_20d is None:
        return None
    score: float = float(s.return_20d)
    if s.new_high_20d:
        score += 8
    if s.new_high_65d:
        score += 12
    if s.new_high_252d:
        score += 20
    if s.dist_from_ma_50 is not None and s.dist_from_ma_50 > 0:
        score += min(s.dist_from_ma_50 * 0.3, 15)
    if s.return_5d is not None:
        score += s.return_5d * 0.4
    if s.drawdown_52w is not None and s.drawdown_52w <= -50:
        score -= 25
    return score


def _build_reasons(s: FundSignals) -> None:
    """Populate human-readable reason strings for the Signal Board tabs."""
    # Popping
    if s.new_high_252d:
        s.reasons_popping.append("new 1Y high")
    elif s.new_high_65d:
        s.reasons_popping.append("new 65d high")
    elif s.new_high_20d:
        s.reasons_popping.append("new 20d high")
    if s.return_20d is not None and s.return_20d >= 15:
        s.reasons_popping.append(f"+{s.return_20d:.0f}% in 20d")
    if s.trend_regime == "strong_uptrend":
        s.reasons_popping.append("strong uptrend")
    if s.acceleration_20d is not None and s.acceleration_20d > 10:
        s.reasons_popping.append("momentum accelerating")

    # Oversold
    if s.rsi_14 is not None and s.rsi_14 <= THRESHOLDS["rsi_deeply_oversold"]:
        s.reasons_oversold.append(f"RSI {s.rsi_14:.0f}")
    elif s.rsi_14 is not None and s.rsi_14 <= THRESHOLDS["rsi_oversold"]:
        s.reasons_oversold.append(f"RSI {s.rsi_14:.0f}")
    if s.pct_above_52w_low is not None and s.pct_above_52w_low <= 8:
        s.reasons_oversold.append(f"+{s.pct_above_52w_low:.0f}% above 52W low")
    if s.price_percentile_1y is not None and s.price_percentile_1y <= 10:
        s.reasons_oversold.append(f"{s.price_percentile_1y:.0f}th pct in 1Y")
    if s.reversion_label == "washed_out_stabilizing":
        s.reasons_oversold.append("washed out, stabilizing")

    # Stretched
    if s.rsi_14 is not None and s.rsi_14 >= THRESHOLDS["rsi_very_stretched"]:
        s.reasons_stretched.append(f"RSI {s.rsi_14:.0f}")
    elif s.rsi_14 is not None and s.rsi_14 >= THRESHOLDS["rsi_stretched"]:
        s.reasons_stretched.append(f"RSI {s.rsi_14:.0f}")
    if s.dist_from_ma_50 is not None and s.dist_from_ma_50 >= THRESHOLDS["very_stretched_above_50d_pct"]:
        s.reasons_stretched.append(f"+{s.dist_from_ma_50:.0f}% above 50d")
    elif s.dist_from_ma_50 is not None and s.dist_from_ma_50 >= THRESHOLDS["stretched_above_50d_pct"]:
        s.reasons_stretched.append(f"+{s.dist_from_ma_50:.0f}% above 50d")
    if s.new_high_252d:
        s.reasons_stretched.append("at 1Y high")

    # Near lows
    if s.new_low_252d:
        s.reasons_near_lows.append("new 1Y low")
    elif s.new_low_65d:
        s.reasons_near_lows.append("new 65d low")
    elif s.new_low_20d:
        s.reasons_near_lows.append("new 20d low")
    if s.pct_above_52w_low is not None and s.pct_above_52w_low <= 5:
        s.reasons_near_lows.append(f"+{s.pct_above_52w_low:.0f}% above 52W low")
    if s.pct_from_ath is not None and s.pct_from_ath <= -60:
        s.reasons_near_lows.append(f"{s.pct_from_ath:.0f}% from ATH")


def signals_to_dict(s: FundSignals) -> dict[str, Any]:
    """Flatten FundSignals → dict, ready for JSON serialization."""
    out: dict[str, Any] = {}
    for f in s.__dataclass_fields__:
        v = getattr(s, f)
        if isinstance(v, np.floating):
            v = float(v)
        out[f] = v
    return out
