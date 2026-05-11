# 9Sig — Quarterly Action History

Captured from `jasonkelly.com/kellyletter/9sig/` on **2026-05-10**. The source page is public (no login). Re-pull this file each quarter for the latest action.

## Inception & rough performance

| Date | Event | TQQQ price | Portfolio value (AGG + TQQQ) |
|---|---|---|---|
| 2017-01-12 | Plan launched, half allocation | $70.09 | ~$470,691 |
| 2017-04-03 | Reached full 60/40 allocation | $88.47 | ~$508,056 |
| 2026-04-06 | Most recent rebalance | $44.03 | ~$7,110,169 |

> **TQQQ splits during the strategy's life (affect price comparisons across periods):**
> - **3:1 split on 5/24/2018** (Kelly notes this in his 7/2/2018 rebalance)
> - **2:1 split on ~1/13/2022** (Kelly notes "after 2:1 split" in his 1/4/2022 note)
> - **2:1 split in Q4 2025** (between 9/29/25 and 1/5/26 — discovered via backtest: post-9/29/25 share count was 48,162, but pre-1/5/26 was 96,324 = exactly 2×). Not explicitly called out in Kelly's published notes but implicit in the share count doubling. Yahoo Finance applies this split-adjustment retroactively to all pre-Oct-2025 prices.

**~9.2 years, ~$470K → ~$7.1M = ~15.1× gross.** Important caveat: the published "Goal" formula explicitly adds **50% of dividends** to the stock-side target each quarter, meaning the portfolio receives ongoing reinvested dividend contributions. The 15× figure includes this drip-feed; pure strategy CAGR is somewhat lower than the headline ~33%.

For a fairer comparison: TQQQ split-adjusted price was ~$11.68 at Jan 2017 launch (factoring 3:1 split in May 2018 and 2:1 split in Jan 2022); $43.33 at Apr 2026 → buy-and-hold TQQQ return ~3.7×. 9Sig's 15× *gross* shows what disciplined rebalancing + dividend reinvestment can layer on top — though that comparison sweeps the contribution effect under the rug.

## Key inflection points (annotated)

### 2018 Q4 — first 30 Down trigger

- **2019-01-07** rebalance: TQQQ at $38.34, big shortfall ($286,694), bought 7,478 TQQQ shares.
- 30 Down rule activated — would skip next four sell signals.

### 2019 Q2–Q4 — riding the rebound under 30 Down protection

- 2019-04-01: $219K surplus signal, **skipped** (30 Down active).
- 2019-07-01: gray-zone signal (TQQQ closed 0.08% below target), called as skipped sell.
- 2019-09-30: shortfall, throttled buy.
- Outcome: TQQQ exposure preserved through the recovery.

### 2020 Q1 — Covid crash, 30 Down extended

- **2020-03-30**: TQQQ at $46.04, $857K shortfall (huge). Buying-power throttle limited the AGG drawdown; bought 237 TQQQ. New 30 Down phase triggered.

### 2020 Q2–Q4 — locked in for the V-recovery

- 2020-06-29: $689K surplus, skipped (30 Down).
- 2020-09-28: $414K surplus, skipped (30 Down).
- 2021-01-04: 30 Down expires; **base reset to 60/40**. Sold 6,632 TQQQ at $184.08, bought AGG. Locked in the recovery.

### 2021–2022 Q1 — bull market sell-and-buy

- Mostly normal quarterly rebalances. Note Q4 2021 sold TQQQ at $167.83 (pre-split) — perfect timing before the 2022 collapse.

### 2022 Q1–Q4 — sustained bear, throttle and 30 Down doing heavy lifting

- 2022-04-04: TQQQ at $58.36, $1M shortfall, full buy.
- **2022-07-05**: TQQQ at $23.14, **$1.87M shortfall**. 30 Down active; AGG nearly depleted; throttled to ~33% of implied buy. Bought 29,852 shares anyway.
- 2022-10-03: TQQQ at $19.52, $568K shortfall, AGG balance limited the buy to just 3,419 shares.
- 2023-01-03: TQQQ at $17.66 (low), $310K shortfall, AGG nearly exhausted. Tiny buy.

This stretch is the strategy's stress test: AGG was repeatedly drained to single-digit thousands of dollars, but the throttle prevented total exhaustion.

### 2023 Q1–Q3 — the painful "hold and skip" phase

- 2023-04-03: $767K *surplus*, **skipped** (30 Down). TQQQ now at $27.68 — would have been a lousy sell.
- 2023-07-03: $834K surplus, skipped.
- 2023-10-02: shortfall (TQQQ pulled back), throttled buy.

### 2024 Q1 — second base reset (lock in the recovery)

- **2024-01-02**: TQQQ at $49.35, $977K surplus, 30 Down expires. **Reset to 60/40**: sold 32,977 TQQQ (vs implied 19,279), bought AGG. Refilled the bond reservoir.

### 2024–2025 — normal rebalances

Quarterly oscillation of buys and sells, no overrides triggered.

### 2025 Q2–Q3 — partial drawdown and recovery

- 2025-04: $1.45M shortfall, big TQQQ buy at $54.03.
- 2025-06: $1.34M surplus, sold TQQQ at $82.81, refilled AGG.
- 2025-09: $666K surplus, sold TQQQ at $102.79.

### 2026 Q1–Q2 — early 2026 wobble

- 2026-01-05: $299K shortfall, modest buy.
- 2026-04-06: TQQQ at $44.03, **$1.41M shortfall** (ugly quarter). Bought 31,971 TQQQ shares.

## Override-rule activations (cumulative count)

| Rule | Times triggered | Net effect |
|---|---|---|
| 30 Down (sell skipped) | ~9 quarters | Preserved TQQQ exposure through 2018-Q4 + Covid + 2022 bear |
| Bond reservoir reset | 2 (2021-01, 2024-01) | Refilled AGG after recoveries |
| Buying-power throttle | ~5+ quarters | Prevented AGG full depletion in 2019, 2020, 2022, 2023 |
| Spike reset (+100% quarter) | 0 | TQQQ has not actually doubled in any single quarter since plan inception |

## Re-pulling this data

```bash
# Public page, no auth required
curl -s https://jasonkelly.com/kellyletter/9sig/ | \
  grep -A 30 -E '^(.{1,3}/.{1,3}/.{1,4})$' | head -200
```

Or open the URL and copy the top-most quarter block. The published format is stable: `Date / Price / STATUS / ORDERS / RULES / RESULTS / BALANCES / NOTES`.

## Personal usage note (Tony)

Tony follows 9Sig **loosely** — not running the exact dollar amounts, but tracking the rebalance signals and sizing his own TQQQ/AGG allocation to roughly mirror the published actions. The published quarterly numbers (signal direction, % surplus/shortfall, rule overrides) are what matter for sizing decisions.
