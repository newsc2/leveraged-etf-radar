# 9Sig — Full Mechanical Rules

The aggressive third tier of the Kelly Letter. Launched January 8, 2017 (Note 1) after years of testing failed alternatives in Tier 3.

> **Confidence: 80%** — robust structure (inherited from 3Sig), but 3× leverage and a focused index introduce volatility that reduces confidence vs the 1× and 2× tiers.

## Plan parameters

| Parameter | Value |
|---|---|
| Stock fund | **TQQQ** — ProShares UltraPro QQQ (Nasdaq 100, 3×) |
| Bond fund | **AGG** — iShares Core U.S. Aggregate Bond ETF |
| Rebalance frequency | **Quarterly** (Jan / Apr / Jul / Oct, on the first trading day) |
| Quarterly growth target | **+9%** |
| Base allocation | **60% TQQQ / 40% AGG** |
| Reset trigger (bond depletion) | When AGG would be signaled to reach 0% |
| Reset timing | **Same quarter** (immediate, not deferred) |
| 30 Down rule monitored on | **TQQQ closing prices** (NOT NDX, NOT SPY) |
| Spike reset trigger | **TQQQ +100% in a quarter** → reset to 60% |
| Buying power throttle | **90%** (never deploy more than 90% of AGG reservoir) |

## The signal-line calculation (the engine)

Each quarter, compute the **TQQQ Balance Goal** for the upcoming rebalance:

```
new_goal = previous_goal × 1.09  +  0.5 × (TQQQ + AGG dividends this quarter)
```

The `+ 0.5 × dividends` line means **half of all dividend income gets baked into the stock-side target**, accelerating the goal slightly each quarter. The other half stays in the bond reservoir.

Then on rebalance day:

- **Surplus** (actual TQQQ value > goal) → sell TQQQ down to the goal, buy AGG with the proceeds
- **Shortfall** (actual TQQQ value < goal) → sell AGG to fund the gap, buy TQQQ up to the goal

In Kelly's published quarterly notes, this is computed step by step in the "STATUS" → "ORDERS" → "RULES" → "RESULTS" template you'll see in `9sig-history.md`.

## The four override rules (in order of precedence)

The rebalance logic above is modulated by four conditional checks, applied each quarter. If any apply, the orders get modified before execution.

### Rule 1 — 30 Down, Stick Around

> **IF** TQQQ has closed 30%+ below its rolling quarterly high within the prior 2 years
> **THEN** skip all SELL signals (still execute BUY signals)

Purpose: don't sell the leveraged fund into a deep drawdown. The strategy's biggest single-quarter wins come from the *recovery* off panic lows, and selling into the panic forfeits them.

The phase ends when TQQQ closes at a new all-time quarterly high (clock resets), then the strategy may issue normal sell signals again. There's also a fallback: if the phase has been active for 2 years, it expires.

**Historically active:**
- Q1 2019 → Q4 2020 (2018 Q4 crash, then Covid extension)
- Q3 2022 → Q2 2023 (2022 bear market)

### Rule 2 — Bond Reservoir Reset

> **IF** the rebalance would take AGG to 0% balance
> **AND** the 30 Down rule is NOT in effect
> **THEN** reset to 60/40 base allocation immediately (force-sell TQQQ to refill AGG)

Purpose: maintain dry powder. If TQQQ has run up so far that AGG would be fully drained on the next sell, the rebalance is **upsized** to bring the allocation back to 60/40.

**Historically triggered:** Q1 2024 (1/2/24) — TQQQ surplus implied selling enough to cut AGG too low, so the orders were modified to sell 32,977 TQQQ shares (vs implied 19,279) and refill AGG to 40%.

### Rule 3 — Buying-Power Throttle (90%)

> **IF** the rebalance would require buying TQQQ with more than 90% of the AGG balance
> **THEN** limit the AGG sell to ≤90% of AGG balance, and buy correspondingly less TQQQ

Purpose: in a prolonged crash, preserve some bond cushion for the *next* shortfall. Without this, a single huge buy signal could empty AGG, leaving zero firepower if prices fall another 30%.

**Historically triggered:** multiple quarters during the 2022 bear (Q3 2022, Q4 2022, Q3 2023) and during Covid (Q1 2020, Q3 2019).

### Rule 4 — Spike Reset (the "exit ramp")

> **IF** TQQQ gains 100%+ in a single quarter
> **AND** TQQQ's post-rebalance balance would be 60–100% of the portfolio
> **AND** the 30 Down rule is NOT in effect
> **THEN** reset to 60% TQQQ allocation (sell aggressively)

Purpose: lock in euphoric runs. A 3× leveraged fund can compound spectacularly in one quarter, and that's exactly when reversion risk is highest. The spike reset says: take the win, refill bonds.

This rule is unique to 9Sig — neither 3Sig nor 6Sig has it.

## What an "all-in" 9Sig looks like

In a deep crash, 9Sig will keep buying TQQQ even as it falls — but the buying-power throttle prevents going truly 100% all-in. Kelly notes:

> "In an extended crash, it will get close enough to all-in that it will behave for practical purposes as if it were all-in, and will certainly feel like it's all-in."

This is a **very volatile plan**. Drawdowns of 50–70% on the TQQQ side are plausible during major bear markets. The 60/40 base allocation means the *portfolio*-level drawdown is materially less, but it's still painful.

## Where 9Sig will and won't work

**Where it shines:**
- Long, choppy bull markets with periodic 10–20% pullbacks (forces buy-low, sell-high)
- Sharp V-shaped recoveries (30 Down keeps full TQQQ exposure for the bounce)
- Decade-scale tech-led economic growth (compounds the 3× upside)

**Where it struggles:**
- Multi-year sideways markets with no real direction (volatility decay grinds TQQQ down quarter after quarter while the goal keeps growing 9%)
- Sustained inflation/rate-rising regimes that hurt both TQQQ *and* AGG simultaneously (bond reservoir gets drained while it's also losing principal value — see 2022)

## Implementation notes for new starters

Kelly's standard advice (from the user guide) for someone starting today:

1. **Don't wait for the "right" moment** — there isn't one. Quarterly rebalancing handles entry-timing automatically.
2. **Ease in** if emotionally hesitant — start with 30% TQQQ + 70% AGG, let normal buy signals raise it to 60%.
3. **Run in a tax-advantaged account** (Roth IRA, 401(k), TFSA, RRSP) — quarterly turnover is high.
4. **Don't peek between rebalances** — the plan only checks prices on the last day of each quarter.
5. **Keep separate accounts** for each tier (3Sig, 6Sig, 9Sig) so calculations don't bleed across.

## See also

- [`9sig-history.md`](9sig-history.md) — quarterly action log with annotations
- [`sig-system-overview.md`](sig-system-overview.md) — three-tier framework comparison
