"""One-shot diff: live snapshot from Kelly's 9Sig page (fetched via authenticated
Chrome session on 2026-05-11) vs the local JSON.

This is the manual-snapshot variant of `verify_9sig_history.py` — used when the
live HTTP fetch is blocked by WordPress cookie auth.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOCAL = json.loads(
    (REPO / "data" / "sig_plans" / "9sig_quarterly_history.json").read_text(),
)["history"]

# (date, cash, agg, tqqq) — every "BALANCES AFTER" block from the
# 2026-05-11 fetch of jasonkelly.com/kellyletter/9sig/
PUBLISHED: list[tuple[str, int, int, int]] = [
    ("2026-04-06", 3,        1_217_106, 5_893_063),
    ("2026-01-05", 14,       2_625_261, 5_487_791),
    ("2025-09-29", 1,        2_879_774, 4_950_572),
    ("2025-06-30", 11,       2_141_956, 4_532_937),
    ("2025-03-31", 54,       763_730,   3_845_693),
    ("2025-01-06", 20,       2_150_337, 3_766_890),
    ("2024-09-30", 16,       2_031_539, 3_328_714),
    ("2024-07-01", 58,       2_247_454, 3_053_505),
    ("2024-04-01", 73,       1_951_540, 2_796_679),
    ("2024-01-02", 58,       1_649_602, 2_473_767),
    ("2023-10-02", 6,        3_497,     2_968_475),
    ("2023-07-03", 14,       24_200,    3_387_423),
    ("2023-04-03", 54,       13_975,    2_275_822),
    ("2023-01-03", 12,       1_568,     1_451_988),
    ("2022-10-03", 33,       7_364,     1_589_436),
    ("2022-07-05", 17,       76_700,    1_805_082),
    ("2022-04-04", 26,       794_035,   2_810_326),
    ("2022-01-04", 15,       1_914_783, 2_585_925),
    ("2021-10-04", 110,      1_403_288, 2_306_353),
    ("2021-07-06", 57,       1_597_409, 2_164_228),
    ("2021-04-05", 12,       1_118_752, 2_019_296),
    ("2021-01-04", 109,      1_221_950, 1_833_069),
    ("2020-09-28", 51,       1_182,     2_144_921),
    ("2020-06-29", 45,       1_181,     1_531_921),
    ("2020-03-30", 38,       1_173,     906_644),
    ("2020-01-06", 17,       11_744,    1_410_773),
    ("2019-09-30", 4,        11_183,    988_375),
    ("2019-07-01", 63,       109_136,   959_322),
    ("2019-04-01", 64,       105_494,   856_485),
    ("2019-01-07", 14,       103_198,   567_807),
    ("2018-10-01", 104,      381_600,   526_639),
    ("2018-07-02", 92,       307_265,   461_867),
    ("2018-04-02", 87,       265_967,   425_286),
    ("2018-01-08", 80,       336_296,   397_068),
    ("2017-10-02", 57,       237_682,   365_536),
    ("2017-07-03", 30,       209_671,   338_954),
    ("2017-04-03", 482,      202_215,   305_841),
    ("2017-01-12", 49,       329_740,   140_951),
]


def main() -> int:
    local_by_date = {h["date"]: h for h in LOCAL}
    pub_by_date = {p[0]: p for p in PUBLISHED}

    # 1. Set diff
    only_local = set(local_by_date) - set(pub_by_date)
    only_pub = set(pub_by_date) - set(local_by_date)
    if only_local:
        print(f"In local but not published: {sorted(only_local)}")
    if only_pub:
        print(f"In published but not local: {sorted(only_pub)}")

    # 2. Per-date value diffs
    drift_count = 0
    print(f"\n=== Diff: local JSON vs published page (n={len(PUBLISHED)}) ===")
    for d, cash, agg, tqqq in PUBLISHED:
        loc = local_by_date.get(d)
        if not loc:
            continue
        agg_d = agg - loc["agg_value"]
        tqqq_d = tqqq - loc["tqqq_value"]
        if agg_d or tqqq_d:
            drift_count += 1
            print(f"  {d}: AGG diff=${agg_d:+,}  TQQQ diff=${tqqq_d:+,}")

    if drift_count == 0:
        print("  All 38 entries match exactly. ✓")

    # 3. Cash drag check (we ignore cash in the chart's % calc)
    max_cash_pct = max(
        100 * c / (c + a + t) for _, c, a, t in PUBLISHED if (c + a + t)
    )
    avg_cash_pct = sum(
        100 * c / (c + a + t) for _, c, a, t in PUBLISHED if (c + a + t)
    ) / len(PUBLISHED)
    print(f"\n=== Cash impact on % (chart ignores cash) ===")
    print(f"  Max cash %:  {max_cash_pct:.5f}%")
    print(f"  Mean cash %: {avg_cash_pct:.5f}%")
    print("  → Excluding cash from the chart's denominator drifts the TQQQ% "
          f"by at most {max_cash_pct:.4f} percentage points. Negligible.")

    # 4. Pre vs post rebalance allocation drift (the rebalance MOVE itself)
    print(f"\n=== Rebalance impact (pre→post allocation %, latest 8 quarters) ===")
    print(f"  {'date':<12} {'pre%':>7} {'post%':>7} {'Δpp':>7} {'rebal kind':<22}")
    # Pre-rebal value = post shares of LAST quarter × this quarter's close TQQQ price
    # We don't have full pre-rebal AGG values, so this is approximate.
    # For a clean comparison, just use the published "TQQQ Value on close" line vs post.
    # Hardcoded a few from the page for illustration.
    pre_rebal_tqqq_values = {  # TQQQ Value on close at $X (from STATUS line)
        "2026-04-06": 4_414_070,
        "2026-01-05": 5_042_561,
        "2025-09-29": 5_542_324,
        "2025-06-30": 5_795_231,
        "2025-03-31": 2_541_997,
        "2025-01-06": 3_814_296,
        "2024-09-30": 2_961_286,
        "2024-07-01": 3_334_154,
    }
    for d, _cash, agg_post, tqqq_post in PUBLISHED:
        pre_tqqq = pre_rebal_tqqq_values.get(d)
        if pre_tqqq is None:
            continue
        # Pre-rebal AGG: assume same shares × last close (approx);
        # but we just use post AGG as proxy for the pre/post comparison.
        # The accurate pre AGG would need an additional data fetch; skip for now.
        post_total = agg_post + tqqq_post
        post_pct = 100 * tqqq_post / post_total
        # Crude pre %: hold AGG roughly constant intra-rebalance day
        pre_total = agg_post + pre_tqqq
        pre_pct = 100 * pre_tqqq / pre_total if pre_total else 0
        delta = post_pct - pre_pct
        kind = "buy (raises TQQQ%)" if pre_tqqq < tqqq_post else "sell (lowers TQQQ%)"
        print(f"  {d:<12} {pre_pct:>6.1f}% {post_pct:>6.1f}% {delta:>+6.1f} {kind}")

    return 1 if drift_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
