"""Charts for the 9Sig portfolio engine.

Reads quarterly history from `data/sig_plans/9sig_quarterly_history.json`
and produces Plotly figures showing how the TQQQ/AGG allocation has
drifted over time — the central insight that 9Sig is NOT a fixed-allocation
strategy. The 60/40 base only holds at base-reset moments.

Run as a script to generate a standalone HTML preview:
    python -m src.sig_plan_charts

The output lands at `dist/9sig-allocation.html`.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import plotly.graph_objects as go

from src.config import CHART_LAYOUT, COLORS, DATA_DIR, DIST_DIR

HISTORY_FILE: Path = DATA_DIR / "sig_plans" / "9sig_quarterly_history.json"
STATE_FILE: Path = DATA_DIR / "sig_plans" / "9sig_current_state.json"


def load_history() -> list[dict[str, Any]]:
    """Load the quarterly action history from JSON."""
    with HISTORY_FILE.open() as f:
        data: dict[str, Any] = json.load(f)
    return list(data["history"])


def build_allocation_chart(
    history: list[dict[str, Any]] | None = None,
) -> go.Figure:
    """Stacked-area chart of TQQQ% / AGG% over time, with annotations.

    The chart's purpose: show that 9Sig's allocation lives anywhere from
    55/45 to 99/1 between base resets. The "60/40" headline only describes
    the snapshot you hit at a base reset.
    """
    history = history or load_history()

    dates = [date.fromisoformat(h["date"]) for h in history]
    tqqq_pct = [
        100 * h["tqqq_value"] / (h["tqqq_value"] + h["agg_value"]) for h in history
    ]
    agg_pct = [100 - p for p in tqqq_pct]

    # Hover text gives the dollar context, not just %
    hover_tqqq = [
        f"<b>{d.isoformat()}</b><br>"
        f"TQQQ: ${h['tqqq_value']:,.0f} ({tp:.1f}%)<br>"
        f"AGG:  ${h['agg_value']:,.0f} ({100-tp:.1f}%)<br>"
        f"TQQQ price: ${h['tqqq_price']:.2f}"
        + (f"<br><i>{h['annotation']}</i>" if h["annotation"] else "")
        for d, h, tp in zip(dates, history, tqqq_pct, strict=True)
    ]

    fig = go.Figure()

    # Stacked area: AGG (bottom) + TQQQ (top)
    fig.add_trace(go.Scatter(
        x=dates, y=agg_pct, name="AGG (bonds)",
        mode="lines", stackgroup="alloc",
        line=dict(width=0, color=COLORS["cat_5"]),  # sage
        fillcolor=COLORS["cat_5"],
        hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=dates, y=tqqq_pct, name="TQQQ (3× NDX)",
        mode="lines", stackgroup="alloc",
        line=dict(width=0, color=COLORS["cat_1"]),  # teal
        fillcolor=COLORS["cat_1"],
        hovertemplate="%{customdata}<extra></extra>",
        customdata=hover_tqqq,
    ))

    # Reference line at 60% (the base-reset target)
    fig.add_hline(
        y=60, line_dash="dot", line_color=COLORS["text_muted"], line_width=1,
        annotation_text="60% base-reset target",
        annotation_position="bottom right",
        annotation_font=dict(size=11, color=COLORS["text_muted"]),
    )

    # Mark notable events directly on the chart (no separate legend block)
    annotations: list[dict[str, Any]] = []
    for d, h, tp in zip(dates, history, tqqq_pct, strict=True):
        if not h["annotation"]:
            continue
        if "BASE RESET" in h["annotation"]:
            annotations.append(dict(
                x=d, y=tp, xref="x", yref="y",
                text="◆ Base reset", showarrow=True, arrowhead=0,
                arrowcolor=COLORS["highlight"], ax=0, ay=-30,
                font=dict(size=10, color=COLORS["text"]),
                bgcolor=COLORS["panel_bg"], bordercolor=COLORS["highlight"],
                borderwidth=1, borderpad=2,
            ))
        elif "30 Down activated" in h["annotation"] or "Covid crash" in h["annotation"]:
            annotations.append(dict(
                x=d, y=tp, xref="x", yref="y",
                text="▼ 30 Down begins", showarrow=True, arrowhead=0,
                arrowcolor=COLORS["red"], ax=0, ay=-25,
                font=dict(size=10, color=COLORS["red"]),
            ))

    layout = dict(CHART_LAYOUT)
    layout.update(
        title=dict(
            text="<b>9Sig portfolio allocation drift</b><br>"
                 "<span style='font-size:13px;color:#555'>"
                 "TQQQ/AGG split at each quarterly rebalance — "
                 "the 60/40 base is only hit at resets</span>",
            x=0.02, xanchor="left",
            font=dict(family="Lora, Georgia, serif", size=20, color=COLORS["text"]),
        ),
        yaxis=dict(
            title="Allocation (%)", range=[0, 100],
            ticksuffix="%", gridcolor=COLORS["grid"],
        ),
        xaxis=dict(title=None, gridcolor=COLORS["grid"]),
        annotations=annotations,
        legend=dict(
            orientation="h", yanchor="bottom", y=-0.18, xanchor="left", x=0,
            bgcolor="rgba(0,0,0,0)",
        ),
        hovermode="x unified",
        height=500,
    )
    fig.update_layout(**layout)
    return fig


def build_dollar_balance_chart(
    history: list[dict[str, Any]] | None = None,
) -> go.Figure:
    """Companion chart: absolute dollar balances over time.

    Useful for seeing the magnitudes — the % chart can hide that AGG was
    near $0 for years 2019-2020 and again 2022-2023.
    """
    history = history or load_history()

    dates = [date.fromisoformat(h["date"]) for h in history]
    tqqq_vals = [h["tqqq_value"] for h in history]
    agg_vals = [h["agg_value"] for h in history]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates, y=tqqq_vals, name="TQQQ", mode="lines+markers",
        line=dict(color=COLORS["cat_1"], width=2),
        marker=dict(size=5, color=COLORS["cat_1"]),
        hovertemplate="<b>%{x|%Y-%m-%d}</b><br>TQQQ: $%{y:,.0f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=dates, y=agg_vals, name="AGG", mode="lines+markers",
        line=dict(color=COLORS["cat_5"], width=2),
        marker=dict(size=5, color=COLORS["cat_5"]),
        hovertemplate="<b>%{x|%Y-%m-%d}</b><br>AGG: $%{y:,.0f}<extra></extra>",
    ))

    layout = dict(CHART_LAYOUT)
    layout.update(
        title=dict(
            text="<b>9Sig dollar balances by quarter</b><br>"
                 "<span style='font-size:13px;color:#555'>"
                 "AGG repeatedly drained near $0 during 30-Down phases (2019-20, 2022-23)"
                 "</span>",
            x=0.02, xanchor="left",
            font=dict(family="Lora, Georgia, serif", size=20, color=COLORS["text"]),
        ),
        yaxis=dict(title="Balance (USD)", tickprefix="$", tickformat=",.0f"),
        xaxis=dict(title=None),
        hovermode="x unified",
        height=400,
        legend=dict(
            orientation="h", yanchor="bottom", y=-0.20, xanchor="left", x=0,
            bgcolor="rgba(0,0,0,0)",
        ),
    )
    fig.update_layout(**layout)
    return fig


def derive_current_shares(latest: dict[str, Any]) -> tuple[float, float]:
    """Back out TQQQ + AGG share counts from the latest published balances.

    Uses the published quarter-action TQQQ price as the divisor for TQQQ shares.
    AGG shares come from balance / typical AGG price (~$99). Approximate but
    within rounding error.
    """
    tqqq_shares = latest["tqqq_value"] / latest["tqqq_price"]
    # AGG price isn't published per quarter; use $99 as a stable proxy
    # (AGG has traded $94-$118 over the history, mostly $96-$102)
    agg_shares = latest["agg_value"] / 99.0
    return tqqq_shares, agg_shares


def write_standalone_html(
    out_path: Path | None = None,
    current_tqqq_price: float | None = None,
    current_agg_price: float = 99.00,
) -> Path:
    """Write a single self-contained HTML with both charts side by side.

    If `current_tqqq_price` is supplied, also computes the **live** allocation
    by re-pricing the post-rebalance shares at the supplied market price.
    Defaults to last quarter's fill price (i.e. shows last-published snapshot only).
    """
    out_path = out_path or DIST_DIR / "9sig-allocation.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    history = load_history()
    fig_alloc = build_allocation_chart(history)
    fig_dollar = build_dollar_balance_chart(history)

    plot_alloc = fig_alloc.to_html(
        include_plotlyjs="cdn", full_html=False, div_id="alloc",
    )
    plot_dollar = fig_dollar.to_html(
        include_plotlyjs=False, full_html=False, div_id="dollar",
    )

    latest = history[-1]
    snapshot_tqqq = latest["tqqq_value"]
    snapshot_agg = latest["agg_value"]
    snapshot_total = snapshot_tqqq + snapshot_agg
    snapshot_tqqq_pct = 100 * snapshot_tqqq / snapshot_total
    last_date = latest["date"]

    # Live re-pricing
    tqqq_shares, agg_shares = derive_current_shares(latest)
    live_block = ""
    if current_tqqq_price is not None:
        live_tqqq = tqqq_shares * current_tqqq_price
        live_agg = agg_shares * current_agg_price
        live_total = live_tqqq + live_agg
        live_tqqq_pct = 100 * live_tqqq / live_total
        live_block = f"""
  <div class="kpi" style="border-left: 3px solid {COLORS['highlight']}">
    <div class="kpi-label">Live TQQQ allocation</div>
    <div class="kpi-value">{live_tqqq_pct:.1f}%</div>
    <div style="font-size:0.78rem;color:{COLORS['text_muted']};margin-top:0.25rem">
      at TQQQ ${current_tqqq_price:.2f} / AGG ${current_agg_price:.2f}
    </div>
  </div>"""
        current_tqqq_pct = live_tqqq_pct
    else:
        current_tqqq_pct = snapshot_tqqq_pct

    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>9Sig allocation drift</title>
<link rel="icon" type="image/svg+xml" href="/favicon.svg" />
<style>
  body {{
    font-family: Inter, system-ui, sans-serif;
    background: {COLORS['bg']};
    color: {COLORS['text']};
    max-width: 1100px;
    margin: 2rem auto;
    padding: 0 1rem;
    line-height: 1.55;
  }}
  h1 {{ font-family: Lora, Georgia, serif; font-weight: 600; margin-bottom: 0.25rem; }}
  .lede {{ color: {COLORS['text_muted']}; font-size: 0.95rem; margin-top: 0; }}
  .kpis {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 1rem; margin: 1.5rem 0;
  }}
  .kpi {{
    background: {COLORS['panel_bg']}; border: 1px solid {COLORS['border']};
    padding: 0.9rem 1rem; border-radius: 4px;
  }}
  .kpi-label {{ font-size: 0.78rem; color: {COLORS['text_muted']}; text-transform: uppercase; letter-spacing: 0.04em; }}
  .kpi-value {{ font-size: 1.6rem; font-weight: 600; margin-top: 0.2rem; color: {COLORS['text']}; }}
  .chart {{ margin: 2rem 0; }}
  .footnote {{
    font-size: 0.85rem; color: {COLORS['text_muted']};
    border-top: 1px solid {COLORS['border']};
    padding-top: 1rem; margin-top: 2rem;
  }}
</style>
</head>
<body>
<h1>9Sig portfolio allocation drift</h1>
<p class="lede">9Sig is <em>not</em> a fixed-allocation strategy. 60/40 only describes
the snapshot at base resets — between resets, the signal-line mechanic deliberately
lets allocation swing widely. This is what that actually looks like over 9+ years.</p>

<div class="kpis">
  <div class="kpi">
    <div class="kpi-label">Last published rebalance</div>
    <div class="kpi-value">{last_date}</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Portfolio value (then)</div>
    <div class="kpi-value">${snapshot_total/1e6:.2f}M</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">TQQQ % at last rebalance</div>
    <div class="kpi-value">{snapshot_tqqq_pct:.1f}%</div>
  </div>{live_block}
  <div class="kpi">
    <div class="kpi-label">Drift from base 60%</div>
    <div class="kpi-value">+{current_tqqq_pct - 60:.1f} pp</div>
  </div>
</div>

<div class="chart">{plot_alloc}</div>
<div class="chart">{plot_dollar}</div>

<p class="footnote">Source: <a href="https://jasonkelly.com/kellyletter/9sig/">jasonkelly.com/kellyletter/9sig/</a>
(public, no login). Data captured 2026-05-11 from the published quarterly action log.
Annotations: ◆ marks the two base resets (Jan 2021, Jan 2024); ▼ marks 30-Down activations
(Q4 2018 / Mar 2020). The strategy spent ~3 of its 9 years living above 95% TQQQ allocation.</p>

</body>
</html>"""
    out_path.write_text(body)
    return out_path


def build_panel_html(
    current_tqqq_price: float | None = None,
    current_agg_price: float | None = None,
    update_label: str | None = None,
) -> str:
    """Render a compact 9Sig dashboard panel: holdings table + KPI strip.

    Reads `data/sig_plans/9sig_current_state.json` for the latest published
    Kelly state (shares, prior_goal, etc.), runs the engine against the
    supplied or stored prices, and returns a self-contained HTML snippet
    designed to be injected into the main dashboard near the top.

    If price arguments are omitted, falls back to the rebalance fill price
    (panel will reflect last-rebalance snapshot rather than live state).
    """
    from datetime import date as _date

    from src.sig_plans import (
        PlanConfig,
        PlanState,
        QuarterInputs,
        compute_rebalance,
    )

    if not STATE_FILE.exists():
        return '<div class="ninesig-panel ninesig-empty">9Sig state file missing.</div>'

    state_d = json.loads(STATE_FILE.read_text())
    cfg = PlanConfig()

    # Live re-pricing (defaults to last rebalance fill if no live price)
    tqqq_price = current_tqqq_price or state_d["last_rebalance_fill_price"]
    agg_price = current_agg_price or 99.00

    state = PlanState(
        last_rebalance_date=_date.fromisoformat(state_d["last_rebalance_date"]),
        prior_goal=float(state_d["prior_goal"]),
        last_quarter_close_price=float(state_d["last_quarter_close_price"]),
        last_rebalance_fill_price=float(state_d["last_rebalance_fill_price"]),
        rolling_2yr_quarterly_high=float(state_d["rolling_2yr_quarterly_high"]),
        high_set_on=_date.fromisoformat(state_d["high_set_on"]),
        thirty_down_active=bool(state_d["thirty_down_active"]),
        thirty_down_started=(
            _date.fromisoformat(state_d["thirty_down_started"])
            if state_d.get("thirty_down_started") else None
        ),
        shares_stock=float(state_d["shares_stock"]),
        shares_bond=float(state_d["shares_bond"]),
    )
    next_action = _date.fromisoformat(state_d["next_action_date"])

    # Run engine as if quarter ended TODAY at supplied prices
    q = QuarterInputs(
        rebalance_date=next_action,
        quarter_close_date=next_action,
        quarter_close_price=tqqq_price,
        rebalance_price=tqqq_price,
        bond_price=agg_price,
        dividends_this_quarter=0.0,
    )
    action = compute_rebalance(state, q, cfg)

    # Compute panel values
    tqqq_value = state.shares_stock * tqqq_price
    agg_value = state.shares_bond * agg_price
    total = tqqq_value + agg_value
    tqqq_pct = 100 * tqqq_value / total
    agg_pct = 100 - tqqq_pct

    spike_trigger = state.last_rebalance_fill_price * (1 + cfg.spike_reset_quarterly_gain)
    distance_to_trigger = spike_trigger - tqqq_price
    pct_to_trigger = 100 * distance_to_trigger / tqqq_price
    quarterly_gain = (tqqq_price / state.last_rebalance_fill_price - 1) * 100

    # "What to do if quarter ended tomorrow"
    if action.spike_reset_triggered:
        verdict = "SPIKE RESET"
        verdict_kind = "sell"
        verdict_detail = (
            f"Sell {action.sell_stock_shares:,.0f} TQQQ → 60/40 base "
            f"(~${action.sell_stock_shares * tqqq_price/1e6:.2f}M to AGG)"
        )
    elif action.surplus_or_shortfall >= 0 and action.thirty_down_active:
        verdict = "HOLD"
        verdict_kind = "hold"
        verdict_detail = (
            f"30-Down active — sell signal of "
            f"${action.surplus_or_shortfall/1e6:.2f}M would be skipped"
        )
    elif action.surplus_or_shortfall >= 0:
        verdict = "SELL"
        verdict_kind = "sell"
        verdict_detail = (
            f"Sell {action.sell_stock_shares:,.0f} TQQQ "
            f"(~${action.sell_stock_shares * tqqq_price/1e6:.2f}M surplus → AGG)"
        )
    else:
        kind = "BUY (throttled)" if action.buying_power_throttled else "BUY"
        verdict = kind
        verdict_kind = "buy"
        verdict_detail = (
            f"Buy {action.buy_stock_shares:,.0f} TQQQ "
            f"(~${action.buy_stock_shares * tqqq_price/1e6:.2f}M)"
        )

    # Render
    last_rebal = state.last_rebalance_date.strftime("%b %-d, %Y")
    next_rebal = next_action.strftime("%b %-d, %Y")
    update_str = update_label or "from last published Kelly note"

    return f"""
<div class="ninesig-panel">
  <div class="ninesig-header">
    <div>
      <h2 class="ninesig-title">9Sig — Jason Kelly's leveraged NDX plan</h2>
      <p class="ninesig-meta">
        TQQQ + AGG quarterly rebalance · last action {last_rebal} ·
        next action {next_rebal} · state {update_str}
      </p>
    </div>
    <div class="ninesig-verdict ninesig-verdict-{verdict_kind}">
      <div class="ninesig-verdict-label">If quarter ended now</div>
      <div class="ninesig-verdict-value">{verdict}</div>
      <div class="ninesig-verdict-detail">{verdict_detail}</div>
    </div>
  </div>

  <table class="ninesig-table">
    <thead>
      <tr>
        <th>Holding</th>
        <th class="num">Shares</th>
        <th class="num">Price</th>
        <th class="num">Value</th>
        <th class="num">Allocation</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td><strong>TQQQ</strong> <span class="ninesig-tag">3× NDX</span></td>
        <td class="num">{state.shares_stock:,.0f}</td>
        <td class="num">${tqqq_price:,.2f}</td>
        <td class="num">${tqqq_value/1e6:,.2f}M</td>
        <td class="num">
          <span class="ninesig-bar"><span class="ninesig-bar-fill ninesig-bar-tqqq"
                style="width:{tqqq_pct:.1f}%"></span></span>
          <strong>{tqqq_pct:.1f}%</strong>
        </td>
      </tr>
      <tr>
        <td><strong>AGG</strong> <span class="ninesig-tag">bonds</span></td>
        <td class="num">{state.shares_bond:,.0f}</td>
        <td class="num">${agg_price:,.2f}</td>
        <td class="num">${agg_value/1e6:,.2f}M</td>
        <td class="num">
          <span class="ninesig-bar"><span class="ninesig-bar-fill ninesig-bar-agg"
                style="width:{agg_pct:.1f}%"></span></span>
          <strong>{agg_pct:.1f}%</strong>
        </td>
      </tr>
      <tr class="ninesig-total">
        <td>Portfolio</td>
        <td class="num"></td>
        <td class="num"></td>
        <td class="num"><strong>${total/1e6:,.2f}M</strong></td>
        <td class="num">100%</td>
      </tr>
    </tbody>
  </table>

  <div class="ninesig-kpis">
    <div class="ninesig-kpi">
      <div class="ninesig-kpi-label">Quarterly gain since rebal</div>
      <div class="ninesig-kpi-value">{quarterly_gain:+.1f}%</div>
      <div class="ninesig-kpi-hint">TQQQ from ${state.last_rebalance_fill_price:.2f} → ${tqqq_price:.2f}</div>
    </div>
    <div class="ninesig-kpi">
      <div class="ninesig-kpi-label">Spike reset trigger</div>
      <div class="ninesig-kpi-value">${spike_trigger:.2f}</div>
      <div class="ninesig-kpi-hint">TQQQ close ≥ this → reset to 60/40</div>
    </div>
    <div class="ninesig-kpi">
      <div class="ninesig-kpi-label">Distance to trigger</div>
      <div class="ninesig-kpi-value">${distance_to_trigger:+.2f}</div>
      <div class="ninesig-kpi-hint">{pct_to_trigger:+.1f}% TQQQ move needed</div>
    </div>
    <div class="ninesig-kpi">
      <div class="ninesig-kpi-label">30-Down trigger</div>
      <div class="ninesig-kpi-value">${state.rolling_2yr_quarterly_high * 0.7:.2f}</div>
      <div class="ninesig-kpi-hint">activates if quarter close &lt; this</div>
    </div>
  </div>
</div>
"""


def _cli() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--current-tqqq", type=float, default=None,
                        help="Live TQQQ price for the live-allocation KPI")
    parser.add_argument("--current-agg", type=float, default=99.00,
                        help="Live AGG price (defaults to ~$99)")
    args = parser.parse_args()
    out = write_standalone_html(
        current_tqqq_price=args.current_tqqq,
        current_agg_price=args.current_agg,
    )
    print(f"Wrote {out}")


if __name__ == "__main__":
    _cli()
