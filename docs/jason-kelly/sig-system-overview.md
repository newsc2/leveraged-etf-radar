# Sig System Overview (3Sig / 6Sig / 9Sig)

Jason Kelly's Kelly Letter runs three tiers, all variants of the same **quarterly signal-line rebalance** mechanic from his book *The 3% Signal* (2015). All three trade only in **January, April, July, and October** — no daily watching, no panic clicks.

## The three tiers

| Tier | Name | Stock fund | Bond fund | Quarterly target | Base allocation | Confidence |
|---|---|---|---|---|---|---|
| 1 | **3Sig** | IJR (S&P SmallCap 600, 1×) | BND (Vanguard Bond) | +3% | 80/20 stock/bond | 100% |
| 2 | **6Sig** | MVV (S&P MidCap 400, 2×) | SCHZ (Schwab Bond) | +6% | 60/40 stock/bond | 90% |
| 3 | **9Sig** | TQQQ (Nasdaq 100, 3×) | AGG (iShares Bond) | +9% | 60/40 stock/bond | 80% |

The pattern: **higher leverage → higher quarterly target → lower confidence**. The quarterly target is roughly `leverage × 3%` because 3% is approximately the historical real return of the underlying small-cap index per quarter.

## The core mechanic (same for all three)

Each quarter, compute the "signal line" balance the stock fund *should* have:

```
signal_line = previous_signal_line × (1 + quarterly_target) + 0.5 × quarterly_contributions
```

Then:

- If actual stock balance > signal line → **sell** the surplus, **buy** bonds
- If actual stock balance < signal line → **sell** bonds, **buy** stock to reach signal

This is a disciplined **buy-low, sell-high** machine. The bond fund acts as a buy-side reservoir; gains are skimmed off in good quarters and redeployed when the stock fund falls behind.

## Why three tiers?

Suggested allocation:

- **50% in Tier 1 (3Sig)** — the ballast; tested back to 1950s
- **30% in Tier 2 (6Sig)** — moderate leverage upside
- **20% in Tier 3 (9Sig)** — aggressive, accepting it can swing -70% in a crash

The descending allocation matches descending confidence and ascending volatility. **9Sig is explicitly never meant to be 100% of one's investable capital.**

## Shared safety rules (apply across tiers)

### 30 Down, Stick Around

If the stock fund (TQQQ for 9Sig, MVV for 6Sig, **SPY** for 3Sig) closes 30%+ below its rolling quarterly high within a 2-year window, **skip all sell signals** for the duration of the bear market. Kelly's argument: the worst time to sell is into a panic, and the rebound from 30%-down lows is where the signal line's biggest wins come from.

This is the rule that kept TQQQ exposure through the 2020 Covid crash and the 2022 bear market — see `9sig-history.md` for the actual outcomes.

### Quarterly mechanic, not weekly/monthly

All three tiers ignore intra-quarter price moves. The signal calculation uses **only the last close of the previous quarter**. This is mechanically simple and emotionally insulating.

### Tax-advantaged accounts strongly preferred

Quarterly buy/sell actions generate constant taxable events. Kelly recommends running each tier in a separate IRA / Roth IRA / 401(k) (or TFSA / RRSP in Canada).

## Where 9Sig diverges from 3Sig/6Sig

9Sig adds two extra rules to handle TQQQ's 3× volatility:

1. **Spike reset trigger** — if TQQQ gains 100%+ in a single quarter, reset to 60% (lock in euphoria gains)
2. **Buying-power throttle (90%)** — never deploy more than 90% of bond reservoir in one quarter (preserve some dry powder during prolonged crashes)

Full details in [`9sig-strategy.md`](9sig-strategy.md).

## Calculator availability

- **3Sig Calculator** (web tool on jasonkelly.com) — handles 3Sig and 6Sig parameters
- **9Sig** — Kelly does NOT yet ship a complete official calculator (spike reset + throttle not implemented). Subscribers follow the letter's published numbers each quarter
- **Community tools** — the `r/JasonKelly` and `r/9Sig` subreddits host multiple member-built spreadsheets and Python scripts that implement the full 9Sig rule set including spike reset and throttle. The mechanics are simple enough (~50 lines of Python) that a custom tracker is straightforward — a candidate addition to this `leveraged-etf-radar` project

## Books

- *The 3% Signal: The Investing Technique That Will Change Your Life* (Plume, 2015) — full theory + back-testing for 3Sig
- *The Neatest Little Guide to Stock Market Investing* — broader investing primer
