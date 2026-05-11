"""Final ground-truth check: reconstruct weekly TQQQ% trajectory Apr 6 → May 8
using Yahoo close prices and Kelly's published share counts, then compare with
what Kelly published in Note 19 (5/10/26).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.data import fetch_yahoo_series

# Per Kelly's Note 19, current shares: TQQQ 133,842 | AGG 12,425
# These are post-4/6/26 buy + AGG dividend reinvestments through 5/6/26.
# We approximate AGG shares as growing linearly from 12,294 (4/6 post-rebal)
# to 12,425 (post-5/6 dividend), but for week-over-week comparison we use a
# constant AGG share count since intra-quarter dividend share growth is small.
TQQQ_SHARES = 133_842
AGG_SHARES_BY_DATE = {
    # AGG shares grow as monthly distributions are reinvested
    date(2026, 4, 6): 12_294,
    date(2026, 5, 6): 12_425,   # confirmed via dividend email
}

KELLY_PUBLISHED = {
    date(2026, 4, 6): 83,
    date(2026, 4, 10): 84,
    date(2026, 4, 17): 86,
    date(2026, 4, 24): 87,
    date(2026, 5, 1): 88,
    date(2026, 5, 8): 89,
}


def agg_shares_on(d: date) -> int:
    """Linearly interpolate AGG share count between known data points."""
    if d <= date(2026, 4, 6):
        return 12_294
    if d >= date(2026, 5, 6):
        return 12_425
    days_total = (date(2026, 5, 6) - date(2026, 4, 6)).days
    days_in = (d - date(2026, 4, 6)).days
    return 12_294 + round((12_425 - 12_294) * days_in / days_total)


def main() -> int:
    print("Fetching TQQQ + AGG daily closes ...")
    tqqq = fetch_yahoo_series("TQQQ", years_back=2)
    agg = fetch_yahoo_series("AGG", years_back=2)
    print()

    print(f"{'date':<12} {'TQQQ$':>8} {'AGG$':>7} {'AGGsh':>7} "
          f"{'TQQQval':>12} {'AGGval':>11} {'mine%':>7} {'Kelly%':>7} {'diff':>5}")
    print("-" * 86)

    total_diff = 0.0
    n = 0
    for d, kelly_pct in sorted(KELLY_PUBLISHED.items()):
        # Find the Yahoo close on or before this date
        ts = tqqq.index[tqqq.index <= d.isoformat()].max()
        tq_close = float(tqqq.loc[ts])
        ts_a = agg.index[agg.index <= d.isoformat()].max()
        ag_close = float(agg.loc[ts_a])

        agg_sh = agg_shares_on(d)
        tq_val = TQQQ_SHARES * tq_close
        ag_val = agg_sh * ag_close
        my_pct = 100 * tq_val / (tq_val + ag_val)
        diff = my_pct - kelly_pct

        print(f"{d.isoformat():<12} {tq_close:>8.2f} {ag_close:>7.2f} "
              f"{agg_sh:>7,} ${tq_val:>11,.0f} ${ag_val:>10,.0f} "
              f"{my_pct:>6.2f}% {kelly_pct:>6.0f}% {diff:>+5.2f}")
        total_diff += abs(diff)
        n += 1

    print("-" * 86)
    print(f"Mean abs diff vs Kelly: {total_diff/n:.2f} percentage points")
    return 0


if __name__ == "__main__":
    sys.exit(main())
