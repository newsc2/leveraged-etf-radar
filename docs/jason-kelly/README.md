# Jason Kelly — Sig Plans Reference

Offline reference for Jason Kelly's three-tier "Sig" investing strategies, with focus on **9Sig** (the leveraged TQQQ+AGG plan Tony loosely follows). Sourced from `jasonkelly.com/kellyletter/` (subscriber site uses rotating monthly password — these notes mean we don't need to log in each time).

## Files

| File | Contents |
|---|---|
| [`sig-system-overview.md`](sig-system-overview.md) | Three-tier (3Sig / 6Sig / 9Sig) framework, suggested 50/30/20 allocation, confidence levels |
| [`9sig-strategy.md`](9sig-strategy.md) | Full 9Sig mechanical rules: signal line, 30-Down, spike reset, buying-power throttle |
| [`9sig-history.md`](9sig-history.md) | Quarterly action log from inception (Jan 2017) through the most recent rebalance, with annotations on key inflection points |

## Source URLs (public, no login)

- Strategy hub: https://jasonkelly.com/kellyletter/9sig/
- User guide: https://jasonkelly.com/kellyletter/userguide/

## Source URLs (subscriber-only, rotating password)

- Note 1, 2017-01-08 — original 9Sig launch with full back-testing rationale
- Weekly notes — quarterly action calls and "30 Down, stick around" status updates
- User: `subscriber` / Password: changes monthly (in the first Sunday note of each month)

## How this relates to Leveraged ETF Radar

9Sig is the canonical real-money case study for the dashboard's central thesis: **naive `leverage × proxy_return` ≠ daily-reset compounded return** (see `AGENTS.md` → "Known traps"). Kelly's plan exists precisely because TQQQ's volatility decay and upside path-dependence demand a rules-based rebalancer rather than buy-and-hold.

The dashboard's tracking-math panel (actual LETF return vs naive vs simulated daily-reset) is the same lens Kelly uses to justify why 9Sig works on TQQQ specifically.
