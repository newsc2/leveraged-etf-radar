"""Sig-style quarterly discipline lens — inspired by Jason Kelly's 3Sig/6Sig/9Sig.

IMPORTANT: only TQQQ is true 9Sig. Anything else here is a "Sig-style lens" —
a leverage-scaled quarterly target (~3% × leverage) used as a discipline yardstick,
NOT an endorsement of running 9Sig on those tickers.

Per-fund output:
  - QTD return from previous quarter-end close
  - Quarter progress (days elapsed / total)
  - Leverage-scaled quarterly target
  - signal_gap = QTD return - target
  - linear_pace_gap = QTD - (target × days_elapsed / total_days)
  - eligible = long, |leverage| ∈ {1, 1.5, 2, 3}; inverse funds flagged not-Sig-compatible

TQQQ-specific extras:
  - Canonical 9% target lens
  - 30-down active (rolling 2yr drawdown ≥ 30%)
  - Spike-watch: distance from +100% quarter

NO trade orders. NO portfolio sizing. This is analytical only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd

# 3% × |leverage|, capped at the canonical tiers Kelly publishes
_TIER_TARGETS: dict[float, float] = {
    1.0: 3.0,    # 3Sig
    1.5: 4.5,    # interpolated
    2.0: 6.0,    # 6Sig
    3.0: 9.0,    # 9Sig (TQQQ canonical)
}

TQQQ_TICKER: str = "TQQQ"
SPIKE_RESET_THRESHOLD_PCT: float = 100.0      # Kelly's +100% quarter rule
THIRTY_DOWN_THRESHOLD_PCT: float = -30.0      # 30%+ below rolling 2yr high
THIRTY_DOWN_LOOKBACK_DAYS: int = 504          # ~2 trading years


@dataclass
class SigLens:
    """Sig-style quarterly lens for one fund."""
    ticker: str
    eligible: bool                                  # long & |lev| supported
    eligibility_note: str | None = None             # why not eligible (if applicable)
    quarterly_target_pct: float | None = None       # leverage-scaled
    qtd_return_pct: float | None = None
    signal_gap_pct: float | None = None             # QTD - target
    pace_gap_pct: float | None = None               # QTD - linear pace
    quarter_start: str | None = None                # ISO date of qtr start
    quarter_end: str | None = None                  # ISO date of qtr end
    days_elapsed: int | None = None
    days_total: int | None = None
    is_canonical_9sig: bool = False                 # only TQQQ
    # TQQQ-only extras
    tqqq_30down_active: bool | None = None
    tqqq_30down_drawdown_pct: float | None = None
    tqqq_spike_distance_pct: float | None = None    # how far from +100% quarter


# ---------------------------------------------------------------------------
# Quarter helpers
# ---------------------------------------------------------------------------

def current_quarter_bounds(today: date | None = None) -> tuple[date, date]:
    """Return (quarter_start, quarter_end) for the calendar quarter containing `today`."""
    today = today or datetime.now().date()
    q = (today.month - 1) // 3
    start_month = q * 3 + 1
    qstart = date(today.year, start_month, 1)
    end_month = start_month + 2
    # last day of end_month
    if end_month == 12:
        qend = date(today.year, 12, 31)
    else:
        qend = date(today.year, end_month + 1, 1).replace(day=1)
        qend = date(qend.year, qend.month, 1)
        from calendar import monthrange
        qend = date(today.year, end_month, monthrange(today.year, end_month)[1])
    return qstart, qend


def previous_quarter_end_close(prices: pd.Series, today: date | None = None) -> float | None:
    """Last close on or before the most recent quarter-start. The 'baseline' for QTD."""
    if prices is None or prices.empty:
        return None
    today = today or datetime.now().date()
    qstart, _ = current_quarter_bounds(today)
    # last close strictly before this quarter
    cutoff = pd.Timestamp(qstart)
    prior = prices[prices.index < cutoff]
    if prior.empty:
        return None
    return float(prior.iloc[-1])


def quarter_progress(today: date | None = None) -> tuple[int, int]:
    """Return (days_elapsed_in_qtr, total_days_in_qtr) — calendar days."""
    today = today or datetime.now().date()
    qstart, qend = current_quarter_bounds(today)
    days_elapsed = (today - qstart).days + 1
    days_total = (qend - qstart).days + 1
    return days_elapsed, days_total


# ---------------------------------------------------------------------------
# Target scaling
# ---------------------------------------------------------------------------

def leverage_scaled_target(leverage: float | None, direction: str | None) -> tuple[float | None, bool, str | None]:
    """Return (target_pct, eligible, note).

    - Long funds with |lev| in {1, 1.5, 2, 3} are eligible.
    - Inverse funds are not eligible (Kelly's plans assume long exposure).
    - Long with unusual leverage (e.g. -1, fractional) falls back to lev × 3.
    """
    if leverage is None or direction is None:
        return None, False, "missing leverage or direction metadata"
    if direction != "long":
        return None, False, "inverse fund — Sig-style targets are long-only"
    abs_lev = abs(leverage)
    if abs_lev in _TIER_TARGETS:
        return _TIER_TARGETS[abs_lev], True, None
    # Sensible default: 3% per quarter × |leverage|
    return abs_lev * 3.0, True, "non-canonical leverage; using lev × 3%"


# ---------------------------------------------------------------------------
# Sig-style lens (per fund)
# ---------------------------------------------------------------------------

def compute_sig_lens(
    ticker: str,
    prices: pd.Series,
    leverage: float | None,
    direction: str | None,
    today: date | None = None,
) -> SigLens:
    today = today or datetime.now().date()
    target_pct, eligible, note = leverage_scaled_target(leverage, direction)
    lens = SigLens(
        ticker=ticker,
        eligible=eligible,
        eligibility_note=note,
        quarterly_target_pct=target_pct,
        is_canonical_9sig=(ticker == TQQQ_TICKER and leverage == 3 and direction == "long"),
    )

    qstart, qend = current_quarter_bounds(today)
    lens.quarter_start = qstart.isoformat()
    lens.quarter_end = qend.isoformat()
    elapsed, total = quarter_progress(today)
    lens.days_elapsed, lens.days_total = elapsed, total

    if prices is None or prices.empty:
        return lens
    base = previous_quarter_end_close(prices, today)
    if base is None or base == 0:
        return lens

    last_px = float(prices.iloc[-1])
    qtd_pct = (last_px / base - 1.0) * 100
    lens.qtd_return_pct = qtd_pct

    if target_pct is not None:
        lens.signal_gap_pct = qtd_pct - target_pct
        if total > 0:
            linear_pace = target_pct * (elapsed / total)
            lens.pace_gap_pct = qtd_pct - linear_pace

    # TQQQ-specific overlays
    if lens.is_canonical_9sig:
        lens.tqqq_30down_active, lens.tqqq_30down_drawdown_pct = _tqqq_30down_status(prices)
        lens.tqqq_spike_distance_pct = SPIKE_RESET_THRESHOLD_PCT - qtd_pct

    return lens


def _tqqq_30down_status(prices: pd.Series) -> tuple[bool, float]:
    """Is TQQQ currently ≥30% below its rolling 2-year high close?

    Returns (active, current_drawdown_pct).
    """
    if prices is None or prices.empty:
        return False, 0.0
    window = prices.iloc[-THIRTY_DOWN_LOOKBACK_DAYS:] if len(prices) > THIRTY_DOWN_LOOKBACK_DAYS else prices
    high = float(window.max())
    if high == 0:
        return False, 0.0
    last = float(prices.iloc[-1])
    dd = (last / high - 1.0) * 100
    return dd <= THIRTY_DOWN_THRESHOLD_PCT, dd


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def sig_lens_to_dict(s: SigLens) -> dict[str, Any]:
    return {f: getattr(s, f) for f in s.__dataclass_fields__}
