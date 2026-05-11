"""Independent backtest: run the 9Sig engine forward and compare against
Kelly's published actions and balances.

Two tests per date:
  1. **Live allocation** — re-price prior-rebalance shares with that date's
     Yahoo Finance closes, then compare with what Kelly reported (where available).
  2. **Engine signal** — at each rebalance date, run our engine using the prior
     quarter's published state and check that the surplus/shortfall, override
     flags, and order sizes match Kelly's published actions.

Run:
    python scripts/backtest_9sig.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pandas as pd

from src.data import fetch_yahoo_series
from src.sig_plans import PlanConfig, PlanState, QuarterInputs, compute_rebalance


# Post-quarter share holdings, in **current (split-adjusted) basis** so they
# can be multiplied by Yahoo Finance split-adjusted close prices.
# TQQQ had a 2:1 split between 9/29/25 and 1/5/26, so all pre-2025-10 share
# counts are doubled here vs Kelly's published nominal numbers.
# Source: jasonkelly.com/kellyletter/9sig/ ("Shares Currently Owned" lines)
POST_REBALANCE_SHARES: dict[str, tuple[int, int]] = {
    # rebalance_date -> (tqqq_shares_current_basis, agg_shares)
    "2023-04-03": (82_219 * 2, 248),     # pre-split → ×2
    "2023-07-03": (82_219 * 2, 371),
    "2024-01-02": (83_104 * 2, 225),
    "2025-01-06": (44_332 * 2, 22_370),
    "2025-03-31": (71_177 * 2, 7_882),
    "2025-09-29": (48_162 * 2, 28_746),  # POST-Q3 2025 rebal (pre-split, ×2)
    "2026-01-05": (101_871, 26_513),     # post-split already
    "2026-04-06": (133_847, 12_294),
}


@dataclass
class TestCase:
    name: str
    test_date: date            # date to evaluate at
    is_rebalance_day: bool     # if True, also run engine signal test
    prior_rebalance: str       # date string of prior rebalance (for shares)
    kelly_pre_rebal_tqqq_value: int | None = None  # if test_date == quarter close
    kelly_published_tqqq_pct: float | None = None  # if Kelly reported this %


# Helper: pad weekends to nearest trading day
def closest_close(series: pd.Series, target: date) -> tuple[date, float]:
    ts = pd.Timestamp(target)
    valid = series.index[series.index <= ts]
    if len(valid) == 0:
        raise ValueError(f"No price data on or before {target}")
    found = valid.max()
    return (found.date(), float(series.loc[found]))


def main() -> int:
    print("Fetching Yahoo Finance daily closes for TQQQ + AGG (15y) ...")
    tqqq = fetch_yahoo_series("TQQQ", years_back=15)
    agg = fetch_yahoo_series("AGG", years_back=15)
    print(f"  TQQQ: {len(tqqq):,} closes ({tqqq.index.min().date()} → {tqqq.index.max().date()})")
    print(f"  AGG:  {len(agg):,} closes ({agg.index.min().date()} → {agg.index.max().date()})\n")

    cases = [
        # July 2023: rebalance day (30-Down skip) and a mid-quarter date
        TestCase("Jul 2023 — rebalance day (30 Down skip)",
                 test_date=date(2023, 7, 3), is_rebalance_day=True,
                 prior_rebalance="2023-04-03",
                 kelly_pre_rebal_tqqq_value=3_370_979,  # at 6/30/23 close $41.00
                 kelly_published_tqqq_pct=None),
        # March 2025: rebalance day, big shortfall full buy
        TestCase("Mar 2025 — rebalance day (full buy, no override)",
                 test_date=date(2025, 3, 31), is_rebalance_day=True,
                 prior_rebalance="2025-01-06",
                 kelly_pre_rebal_tqqq_value=2_541_997,  # at 3/28/25 close $57.34
                 kelly_published_tqqq_pct=None),
        # Dec 2025: between rebalances (9/29/25 → 1/5/26)
        TestCase("Dec 2025 — mid-quarter (between 9/29/25 and 1/5/26)",
                 test_date=date(2025, 12, 15), is_rebalance_day=False,
                 prior_rebalance="2025-09-29"),
        # Feb 2026: between rebalances (1/5/26 → 4/6/26)
        TestCase("Feb 2026 — mid-quarter (between 1/5/26 and 4/6/26)",
                 test_date=date(2026, 2, 17), is_rebalance_day=False,
                 prior_rebalance="2026-01-05"),
    ]

    cfg = PlanConfig()

    for case in cases:
        print(f"=== {case.name} ===")
        tqqq_shares, agg_shares = POST_REBALANCE_SHARES[case.prior_rebalance]
        print(f"  Prior rebalance: {case.prior_rebalance}")
        print(f"  Held shares: TQQQ {tqqq_shares:,} | AGG {agg_shares:,}")

        # Live re-pricing at test date
        tqqq_dt, tqqq_px = closest_close(tqqq, case.test_date)
        agg_dt, agg_px = closest_close(agg, case.test_date)
        live_tqqq = tqqq_shares * tqqq_px
        live_agg = agg_shares * agg_px
        live_total = live_tqqq + live_agg
        live_pct = 100 * live_tqqq / live_total

        print(f"  Yahoo closes used: TQQQ ${tqqq_px:.2f} ({tqqq_dt})  AGG ${agg_px:.2f} ({agg_dt})")
        print(f"  Live position:    TQQQ ${live_tqqq:,.0f}  AGG ${live_agg:,.0f}")
        print(f"  Live allocation:  TQQQ {live_pct:.2f}% / AGG {100-live_pct:.2f}%")

        # Compare to Kelly's pre-rebal published TQQQ value if available
        if case.kelly_pre_rebal_tqqq_value is not None:
            kelly_tqqq = case.kelly_pre_rebal_tqqq_value
            kelly_agg = live_agg  # we don't have Kelly's pre-rebal AGG; use ours
            kelly_pct = 100 * kelly_tqqq / (kelly_tqqq + kelly_agg)
            tqqq_diff = live_tqqq - kelly_tqqq
            tqqq_diff_pct = 100 * tqqq_diff / kelly_tqqq
            print(f"  Kelly published TQQQ: ${kelly_tqqq:,.0f}")
            print(f"  Diff (mine - Kelly):  ${tqqq_diff:+,.0f}  ({tqqq_diff_pct:+.3f}%)")

        # Engine signal test (only on rebalance days where we know Kelly's actions)
        if case.is_rebalance_day:
            # Build state from prior published quarter
            state, q, expected = build_engine_inputs(case)
            action = compute_rebalance(state, q, cfg)
            print(f"\n  --- Engine signal test ---")
            print(f"  Engine surplus/shortfall: ${action.surplus_or_shortfall:+,.0f}")
            print(f"  Kelly's surplus/shortfall: ${expected['surplus']:+,.0f}")
            print(f"  Engine 30-Down active: {action.thirty_down_active}  "
                  f"(Kelly says: {expected['thirty_down']})")
            print(f"  Engine orders: sell_TQQQ={action.sell_stock_shares:.0f} "
                  f"buy_TQQQ={action.buy_stock_shares:.0f} "
                  f"sell_AGG={action.sell_bond_shares:.0f} "
                  f"buy_AGG={action.buy_bond_shares:.0f}")
            print(f"  Kelly orders:  {expected['orders']}")

        print()

    return 0


def build_engine_inputs(case: TestCase):
    """Hand-coded prior states & quarter inputs for each rebalance test date."""
    if case.test_date == date(2023, 7, 3):
        # Q2 2023 — surplus signal under 30-Down (sell skipped)
        state = PlanState(
            last_rebalance_date=date(2023, 4, 3),
            prior_goal=2_322_335,   # back-solved to give Kelly's 2,536,644 goal
            last_quarter_close_price=28.26,    # 3/31/23 close
            rolling_2yr_quarterly_high=166.33, # 12/31/21
            high_set_on=date(2021, 12, 31),
            thirty_down_active=True,
            thirty_down_started=date(2022, 7, 1),
            shares_stock=82_219, shares_bond=248,
        )
        q = QuarterInputs(
            rebalance_date=date(2023, 7, 3),
            quarter_close_date=date(2023, 6, 30),
            quarter_close_price=41.00,
            rebalance_price=41.20,
            bond_price=99.0,
            dividends_this_quarter=10_598,
        )
        expected = {
            "surplus": 834_335, "thirty_down": True,
            "orders": "skip (30 Down)",
        }
    elif case.test_date == date(2025, 3, 31):
        # Q1 2025 — big shortfall, full buy executed (no override)
        # Use Kelly's literal published prior_goal ($3,652,994 at 1/6/25).
        # 30-Down note: drawdown 82.40→57.34 is 30.4% — exactly at boundary.
        # Kelly says No; my engine says Yes. Buy signals proceed regardless,
        # so the order calc is unaffected. We log the disagreement.
        state = PlanState(
            last_rebalance_date=date(2025, 1, 6),
            prior_goal=3_652_994,
            last_quarter_close_price=82.40,
            rolling_2yr_quarterly_high=82.40,
            high_set_on=date(2025, 1, 3),
            thirty_down_active=False,
            shares_stock=44_332, shares_bond=22_370,
        )
        q = QuarterInputs(
            rebalance_date=date(2025, 3, 31),
            quarter_close_date=date(2025, 3, 28),
            quarter_close_price=57.34,
            rebalance_price=54.03,
            bond_price=98.98,
            dividends_this_quarter=13_948,
        )
        expected = {
            "surplus": -1_446_700, "thirty_down": False,
            "orders": "sell 14,654 AGG / buy 25,231 TQQQ",
        }
    else:
        raise ValueError(f"No engine inputs defined for {case.test_date}")
    return state, q, expected


if __name__ == "__main__":
    sys.exit(main())
