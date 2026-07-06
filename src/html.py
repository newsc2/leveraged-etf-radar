"""HTML template + assembly for the Leveraged ETF Radar dashboard.

Editorial design language (FT/Economist via the data-design skill):
  - White background, teal accent, Lora serif headings, Inter body.
  - Accent bar + serif title + sans subtitle on every chart.
  - Direct on-chart labels everywhere; no separate legend boxes.

Layout (top → bottom):
  1. Page header (Lora hero title, intro subtitle)
  2. Sticky filter bar with live count
  3. KPI strip — 4 cells (best YTD, worst YTD, median YTD, median vol)
  4. Hero performance line chart with timeframe pills
  5. Summary chart row — best/worst leaderboard, vol distribution, liquidity
  6. Compact screener table with click-to-expand detail rows
  7. Footer

All charts render in JS so they react to filter + timeframe changes in one
applyFilters() call.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime
from html import escape
from typing import Any

import pandas as pd

from src.config import COLORS, LEVERAGE_COLORS
from src.holdings import Holdings
from src.metrics import FundMetrics
from src.ninesig_history import ninesig_history_to_dicts
from src.ninesig_simulation import build_ninesig_adjusted_quarters, ninesig_adjusted_quarters_to_dicts
from src.sig_lens import SigLens, sig_lens_to_dict
from src.signals import FundSignals, signals_to_dict
from src.universe import Fund

logger: logging.Logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Screener row classes (used for filter matching)
# ---------------------------------------------------------------------------

def _classes_for(f: Fund) -> str:
    parts: list[str] = [f"dir-{f.direction or 'na'}"]
    if f.leverage is not None:
        if f.leverage == int(f.leverage):
            parts.append(f"lev-{int(abs(f.leverage))}")
        else:
            parts.append("lev-fractional")
        parts.append(f"sign-{'pos' if f.leverage > 0 else 'neg'}")
    parts.append(f"exp-{f.exposure_type or 'na'}")
    parts.append(f"prod-{f.product_type or 'na'}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Screener table — compact 9-column default, click-to-expand detail row
# ---------------------------------------------------------------------------

def _fmt_pct(v: float | None) -> str:
    return "—" if v is None else f"{v:+.1f}%"


def _fmt_num(v: float | None, decimals: int = 2) -> str:
    return "—" if v is None else f"{v:.{decimals}f}"


def _fmt_price(v: float | None) -> str:
    """Render a USD price. Compact for big numbers (KORU ~$983), 2dp default."""
    if v is None:
        return "—"
    if v >= 1000:
        return f"${v:,.0f}"
    return f"${v:.2f}"


_PRICE_EPOCH = pd.Timestamp("2016-01-01")


def _decimate_series(series: pd.Series | None) -> dict[str, Any] | None:
    """Compact 10y price history for the row-expand mini chart.

    Layout: daily for the last 1Y, weekly for 1Y-5Y, monthly for older.
    Total ~520 points per fund; encoded as parallel arrays of (day-offset
    from 2016-01-01, price rounded to 4 dp). Roughly 5KB per fund in JSON.
    """
    if series is None or series.empty:
        return None
    end = series.index.max()
    one_year_ago = end - pd.Timedelta(days=365)
    five_years_ago = end - pd.Timedelta(days=5 * 365)

    daily_part = series[series.index >= one_year_ago]
    weekly_part = series[(series.index >= five_years_ago) & (series.index < one_year_ago)]
    monthly_part = series[series.index < five_years_ago]

    weekly_resampled = (
        weekly_part.resample("W-FRI").last().dropna() if not weekly_part.empty else weekly_part
    )
    monthly_resampled = (
        monthly_part.resample("ME").last().dropna() if not monthly_part.empty else monthly_part
    )

    combined = pd.concat([monthly_resampled, weekly_resampled, daily_part]).sort_index()
    if combined.empty:
        return None

    days = [int((d - _PRICE_EPOCH).days) for d in combined.index]
    prices = [round(float(v), 4) for v in combined.values]
    return {"e": "2016-01-01", "d": days, "p": prices}


def build_screener_table(funds: list[Fund], metrics: dict[str, FundMetrics]) -> str:
    rows_html: list[str] = []
    for f in sorted(funds, key=lambda f: f.ticker):
        m = metrics.get(f.ticker)
        if m is None:
            continue
        cls = _classes_for(f)
        lev = f"{f.leverage:+g}×" if f.leverage is not None else "—"
        target = escape(f.target_name or "")
        notes_dot = (
            f' <span class="note-dot" title="{escape(f.notes or "")}">●</span>'
            if f.notes else ""
        )
        rows_html.append(f"""
          <tr class="screener-row {cls}" data-ticker="{f.ticker}">
            <td class="cell-ticker"><span class="caret">▸</span><strong>{f.ticker}</strong>{notes_dot}</td>
            <td class="cell-issuer">{escape(f.issuer or "")}</td>
            <td class="cell-lev">{lev}</td>
            <td class="cell-target">{target}</td>
            <td class="num">{_fmt_price(m.latest_close)}</td>
            <td class="num">{_fmt_pct(m.return_ytd)}</td>
            <td class="num">{_fmt_pct(m.return_1y)}</td>
            <td class="num">{_fmt_pct(m.return_5y)}</td>
            <td class="num">{_fmt_num(m.realized_vol_1y, 0)}</td>
            <td class="num">{_fmt_num(m.realized_beta_60d)}</td>
          </tr>
          <tr class="detail-row" id="detail-{f.ticker}" hidden>
            <td colspan="10"><div class="detail-panel" id="detail-panel-{f.ticker}"></div></td>
          </tr>""")
    rows = "\n".join(rows_html)
    return f"""
      <div class="table-wrap">
        <table class="screener-table" id="screener">
          <thead>
            <tr>
              <th data-sort="ticker">Ticker</th>
              <th data-sort="issuer">Issuer</th>
              <th data-sort="leverage">Lev</th>
              <th data-sort="target">Target</th>
              <th class="num" data-sort="price">Price</th>
              <th class="num" data-sort="ytd">YTD</th>
              <th class="num" data-sort="1y">1Y</th>
              <th class="num" data-sort="5y">5Y</th>
              <th class="num" data-sort="vol">Vol</th>
              <th class="num" data-sort="beta">β 60d</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
      </div>"""


# ---------------------------------------------------------------------------
# RADAR JSON — embedded data feeding all client-rendered charts
# ---------------------------------------------------------------------------

def _decimate_dates(all_dates: list[pd.Timestamp]) -> list[pd.Timestamp]:
    """Keep last 90 days daily, older as Mondays only."""
    if not all_dates:
        return []
    today = pd.Timestamp.now().normalize()
    cutoff = today - pd.Timedelta(days=90)
    out: list[pd.Timestamp] = []
    for d in all_dates:
        if d >= cutoff:
            out.append(d)
        elif d.weekday() == 0:
            out.append(d)
    return out


def build_radar_json(
    funds: list[Fund],
    metrics: dict[str, FundMetrics],
    fund_data: dict[str, pd.Series],
    holdings: dict[str, Holdings],
    signals: dict[str, FundSignals] | None = None,
    sig_lenses: dict[str, SigLens] | None = None,
    nine_sig_adjusted_data: dict[str, pd.Series] | None = None,
) -> str:
    # ── Shared timestamp axis (decimated) ──
    all_dates: set[pd.Timestamp] = set()
    for series in fund_data.values():
        if series is not None and not series.empty:
            idx = pd.DatetimeIndex(series.index)
            all_dates.update(idx.normalize())
    sampled = _decimate_dates(sorted(all_dates))
    sampled_index = pd.DatetimeIndex(sampled)
    timestamps_ms = [int(d.timestamp() * 1000) for d in sampled]

    # ── Per-fund prices aligned to shared axis ──
    prices: dict[str, list[float | None]] = {}
    for f in funds:
        s_opt: pd.Series | None = fund_data.get(f.ticker)
        if s_opt is None or s_opt.empty:
            prices[f.ticker] = []
            continue
        try:
            reindexed = s_opt.reindex(sampled_index, method="ffill", limit=5)
            prices[f.ticker] = [
                None if pd.isna(v) else round(float(v), 4) for v in reindexed.values
            ]
        except Exception:
            prices[f.ticker] = []

    # ── Per-fund metadata ──
    funds_obj: dict[str, dict[str, Any]] = {}
    for f in funds:
        color = LEVERAGE_COLORS.get(f.leverage or 0, COLORS["muted"]) if f.leverage else COLORS["muted"]
        funds_obj[f.ticker] = {
            "classes": _classes_for(f),
            "leverage": f.leverage,
            "target": f.target_name,
            "issuer": f.issuer,
            "exposure": f.exposure_type,
            "direction": f.direction,
            "product_type": f.product_type,
            "expense_ratio": f.expense_ratio,
            "inception_date": f.inception_date,
            "proxy_symbol": f.proxy_symbol,
            "notes": f.notes,
            "color": color,
        }

    # ── Per-fund metrics (only fields the JS uses) ──
    metrics_obj: dict[str, dict[str, Any]] = {}
    for tk, m in metrics.items():
        metrics_obj[tk] = {
            "ytd": m.return_ytd, "1m": m.return_1m, "3m": m.return_3m,
            "1y": m.return_1y, "3y": m.return_3y, "5y": m.return_5y, "10y": m.return_10y,
            "vol": m.realized_vol_1y, "dd": m.max_drawdown_1y,
            "beta": m.realized_beta_60d, "gap": m.actual_minus_simulated_1y,
            "naive": m.naive_leverage_return_1y, "sim": m.simulated_daily_reset_1y,
            "addv": m.avg_daily_dollar_volume,
            "price": m.latest_close,
            "hist": _decimate_series(fund_data.get(tk)),
        }

    # ── Holdings (compact) for the click-to-expand panel ──
    holdings_obj: dict[str, dict[str, Any]] = {}
    for tk, h in holdings.items():
        comps = []
        for c in (h.components or [])[:8]:  # cap top-8 for the inline panel
            comp = asdict(c) if hasattr(c, "__dataclass_fields__") else {
                "name": getattr(c, "name", ""),
                "weight": getattr(c, "weight", None),
                "kind": getattr(c, "kind", "equity"),
            }
            comps.append(comp)
        holdings_obj[tk] = {
            "confidence": h.confidence,
            "source_url": h.source_url,
            "components": comps,
            "note": h.note,
        }

    # ── Signals + TQQQ watch context (compact for JSON) ──
    signals = signals or {}
    sig_lenses = sig_lenses or {}
    signals_obj = {tk: signals_to_dict(s) for tk, s in signals.items()}
    sig_lens_obj = {tk: sig_lens_to_dict(s) for tk, s in sig_lenses.items()}
    nine_sig_adjusted_data = nine_sig_adjusted_data or {}
    nine_sig_adjusted_quarters = ninesig_adjusted_quarters_to_dicts(
        build_ninesig_adjusted_quarters(
            nine_sig_adjusted_data.get("TQQQ"),
            nine_sig_adjusted_data.get("AGG"),
        )
    )

    payload = {
        "timestamps": timestamps_ms,
        "funds": funds_obj,
        "metrics": metrics_obj,
        "prices": prices,
        "holdings": holdings_obj,
        "signals": signals_obj,
        "sig_lens": sig_lens_obj,
        "nine_sig_history": ninesig_history_to_dicts(),
        "nine_sig_adjusted_quarters": nine_sig_adjusted_quarters,
    }
    return json.dumps(payload, separators=(",", ":"), default=str)


# ---------------------------------------------------------------------------
# Filter bar
# ---------------------------------------------------------------------------

def build_filter_bar(total: int) -> str:
    return f"""
    <div class="filter-shell">
      <div class="filter-bar">
        <div class="filter-group" data-group="direction">
          <span class="filter-label">Direction</span>
          <button class="filter-btn" data-filter="*">All</button>
          <button class="filter-btn active" data-filter=".dir-long">Long</button>
          <button class="filter-btn" data-filter=".dir-inverse">Inverse</button>
        </div>
        <div class="filter-group" data-group="leverage">
          <span class="filter-label">Leverage</span>
          <button class="filter-btn active" data-filter="*">All</button>
          <button class="filter-btn" data-filter=".lev-2">2×</button>
          <button class="filter-btn" data-filter=".lev-3">3×</button>
        </div>
        <div class="filter-group" data-group="exposure">
          <span class="filter-label">Exposure</span>
          <button class="filter-btn active" data-filter="*">All</button>
          <button class="filter-btn" data-filter=".exp-broad_index">Broad Index</button>
          <button class="filter-btn" data-filter=".exp-sector,.exp-industry">Sector</button>
          <button class="filter-btn" data-filter=".exp-country">Country</button>
          <button class="filter-btn" data-filter=".exp-single_stock">Single Stock</button>
          <button class="filter-btn" data-filter=".exp-crypto_equity_related">Crypto</button>
          <button class="filter-btn" data-filter=".exp-thematic">Thematic</button>
        </div>
        <div class="filter-group" data-group="product">
          <span class="filter-label">Product</span>
          <button class="filter-btn active" data-filter="*">ETF + ETN</button>
          <button class="filter-btn" data-filter=".prod-ETF">ETF only</button>
        </div>
        <div class="filter-counter"><span id="filter-count">{total}</span> of {total} funds</div>
      </div>
    </div>"""


# ---------------------------------------------------------------------------
# Top-level template
# ---------------------------------------------------------------------------

# fmt: off
HTML_TEMPLATE: str = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <title>Leveraged ETF Radar — newsc2.com</title>
  <meta name="description" content="A scanner for U.S.-listed leveraged ETFs and ETNs, ranking leaders, laggards, stretched names, oversold funds, and drawdown signals." />
  <meta property="og:title" content="Leveraged ETF Radar — newsc2.com" />
  <meta property="og:description" content="A scanner for U.S.-listed leveraged ETFs and ETNs, ranking leaders, laggards, stretched names, oversold funds, and drawdown signals." />
  <meta property="og:type" content="website" />
  <meta property="og:url" content="https://newsc2.com/projects/leveraged-etf-radar/" />
  <meta property="og:image" content="https://newsc2.com/projects/leveraged-etf-radar-og.jpg" />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="Leveraged ETF Radar — newsc2.com" />
  <meta name="twitter:description" content="A scanner for U.S.-listed leveraged ETFs and ETNs, ranking leaders, laggards, stretched names, oversold funds, and drawdown signals." />
  <meta name="twitter:image" content="https://newsc2.com/projects/leveraged-etf-radar-og.jpg" />
  <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
  <link rel="apple-touch-icon" href="/apple-touch-icon.png" />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Lora:wght@600;700&family=Inter:wght@300;400;500;600;700&family=Outfit:wght@300;400;500;600&display=swap" rel="stylesheet">
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      font-family: 'Inter', system-ui, -apple-system, sans-serif;
      font-feature-settings: "tnum" 1, "lnum" 1, "zero" 1;
      background: {bg};
      color: {text};
      line-height: 1.55;
      -webkit-font-smoothing: antialiased;
      max-width: 100%;
      overflow-x: clip;  /* `clip` instead of `hidden` so position:sticky still works on descendants */
    }}
    .container {{ max-width: 1280px; margin: 0 auto; padding: 32px 32px 56px; overflow-x: clip; }}

    /* Header */
    .page-header {{ margin-bottom: 24px; max-width: 920px; }}
    .page-header::before {{
      content: ''; display: block; width: 40px; height: 4px;
      background: {accent}; margin-bottom: 14px;
    }}
    .page-header h1 {{
      font-family: 'Lora', Georgia, serif; font-size: 30px; font-weight: 700;
      color: {text}; line-height: 1.15;
    }}
    .page-header .subtitle {{
      font-size: 14px; color: {text_muted}; margin-top: 6px; line-height: 1.55;
    }}
    .page-header .meta {{
      font-size: 11px; color: {text_light}; font-family: 'Inter', monospace;
      letter-spacing: .04em; margin-top: 10px;
    }}

    /* Intro block — macro-dashboard-style educational prose */
    .intro-block {{
      max-width: 920px; margin: 0 0 32px;
      display: grid; gap: 22px;
    }}
    .intro-section h3 {{
      font-family: 'Lora', Georgia, serif; font-size: 17px; font-weight: 700;
      color: {text}; margin-bottom: 6px; line-height: 1.25;
      border-left: 3px solid {accent}; padding-left: 12px;
    }}
    .intro-section p {{
      font-size: 14px; line-height: 1.65; color: {text_muted};
      margin: 0 0 8px 15px;
    }}
    .intro-section p strong {{ color: {text}; font-weight: 600; }}

    /* 9Sig panel */
    .ninesig-panel {{
      background: {card_bg}; border: 1px solid {border}; border-left: 3px solid {accent};
      padding: 18px 22px; margin-bottom: 28px; border-radius: 2px;
    }}
    .ninesig-header {{
      display: flex; justify-content: space-between; align-items: flex-start;
      gap: 24px; margin-bottom: 14px; flex-wrap: wrap;
    }}
    .ninesig-title {{
      font-family: 'Lora', Georgia, serif; font-size: 19px; font-weight: 600;
      margin: 0 0 4px 0; color: {text}; letter-spacing: -0.01em;
    }}
    .ninesig-meta {{
      font-size: 12px; color: {text_muted}; margin: 0;
    }}
    .ninesig-updated {{
      color: {text_muted}; opacity: 0.75; white-space: nowrap;
    }}
    .ninesig-status-dot {{
      display: inline-block; width: 7px; height: 7px;
      border-radius: 50%; vertical-align: 1px; margin: 0 2px 0 1px;
      background: {text_muted};
    }}
    .ninesig-status-fresh {{ background: {green}; }}
    .ninesig-status-stale {{ background: #f59e0b; }}
    .ninesig-status-dead  {{ background: {red}; }}
    .ninesig-status-idle  {{ background: {text_muted}; }}
    .ninesig-status-age {{ color: {text_muted}; }}
    .ninesig-verdict {{
      padding: 10px 14px; border-radius: 2px; min-width: 280px;
      background: {panel_bg}; border-left: 3px solid {text_muted};
    }}
    .ninesig-verdict-sell {{ border-left-color: {red}; }}
    .ninesig-verdict-buy {{ border-left-color: {green}; }}
    .ninesig-verdict-hold {{ border-left-color: {highlight}; }}
    .ninesig-verdict-label {{
      font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em;
      color: {text_muted}; margin-bottom: 2px;
    }}
    .ninesig-verdict-value {{
      font-size: 18px; font-weight: 700; color: {text}; margin-bottom: 2px;
      letter-spacing: -0.01em;
    }}
    .ninesig-verdict-detail {{
      font-size: 12px; color: {text_muted}; line-height: 1.4;
    }}
    .ninesig-table {{
      width: 100%; border-collapse: collapse; margin: 8px 0 14px 0;
      font-size: 13px;
    }}
    .ninesig-table th {{
      text-align: left; font-weight: 600; font-size: 11px;
      text-transform: uppercase; letter-spacing: 0.04em;
      color: {text_muted}; padding: 6px 10px; border-bottom: 1px solid {border};
    }}
    .ninesig-table td {{
      padding: 8px 10px; border-bottom: 1px solid {border}; color: {text};
    }}
    .ninesig-table tr.ninesig-total td {{
      border-bottom: none; padding-top: 12px; color: {text}; font-weight: 600;
    }}
    .ninesig-table .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .ninesig-tag {{
      display: inline-block; font-size: 10px; padding: 1px 6px; border-radius: 2px;
      background: {panel_bg}; color: {text_muted}; margin-left: 4px;
      font-weight: 500; vertical-align: middle;
    }}
    .ninesig-bar {{
      display: inline-block; width: 60px; height: 6px; background: {panel_bg};
      border-radius: 1px; vertical-align: middle; margin-right: 8px; overflow: hidden;
    }}
    .ninesig-bar-fill {{ display: block; height: 100%; }}
    .ninesig-bar-tqqq {{ background: {accent}; }}
    .ninesig-bar-agg {{ background: {cat_5}; }}
    .ninesig-kpis {{
      display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px;
      padding-top: 8px; border-top: 1px solid {border};
    }}
    .ninesig-kpi {{
      padding: 8px 0;
    }}
    .ninesig-kpi-label {{
      font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em;
      color: {text_muted}; margin-bottom: 4px;
    }}
    .ninesig-kpi-value {{
      font-size: 20px; font-weight: 600; color: {text}; line-height: 1.1;
      letter-spacing: -0.01em; font-variant-numeric: tabular-nums;
    }}
    .ninesig-kpi-hint {{
      font-size: 11px; color: {text_light}; margin-top: 3px; line-height: 1.3;
    }}
    @media (max-width: 720px) {{
      .ninesig-kpis {{ grid-template-columns: repeat(2, 1fr); }}
      .ninesig-verdict {{ min-width: 0; }}
    }}

    /* 9Sig history page */
    .ninesig-history-section {{
      margin-bottom: 36px;
      padding-bottom: 28px;
      border-bottom: 1px solid {border};
    }}
    .ninesig-mode-toggle {{
      display: inline-flex; gap: 4px; align-items: center;
      border: 1px solid {border}; background: {panel_bg};
      padding: 4px; margin: 14px 0 8px;
    }}
    .ninesig-mode-toggle button {{
      border: 0; background: transparent; color: {text_muted};
      padding: 8px 12px; border-radius: 3px;
      font: 700 12px 'Inter', system-ui, sans-serif;
      cursor: pointer;
    }}
    .ninesig-mode-toggle button.active {{
      background: {card_bg}; color: {text};
      box-shadow: 0 0 0 1px {border};
    }}
    .ninesig-mode-panel[hidden] {{ display: none; }}
    .ninesig-scenario-bar {{
      display: grid; grid-template-columns: minmax(190px, 260px) minmax(170px, 240px) auto;
      gap: 10px; align-items: end; margin: 16px 0 18px;
      background: {panel_bg}; border-left: 3px solid {accent};
      padding: 14px 16px;
    }}
    .ninesig-field {{
      display: flex; flex-direction: column; gap: 4px;
      font-size: 11px; font-weight: 700; color: {text_light};
      text-transform: uppercase; letter-spacing: .06em;
    }}
    .ninesig-field select,
    .ninesig-field input {{
      width: 100%; min-height: 34px; border: 1px solid {border};
      background: {card_bg}; color: {text}; border-radius: 4px;
      font: 500 13px 'Inter', system-ui, sans-serif;
      padding: 6px 10px; font-variant-numeric: tabular-nums;
    }}
    .ninesig-history-reset {{
      min-height: 34px; padding: 6px 12px; border: 1px solid {border};
      background: {card_bg}; color: {text_muted}; border-radius: 4px;
      font: 600 12px 'Inter', system-ui, sans-serif;
      cursor: pointer;
    }}
    .ninesig-history-reset:hover {{ color: {text}; border-color: {accent}; }}
    .ninesig-history-message {{
      margin: 14px 0; padding: 12px 14px; border-left: 3px solid {red};
      background: rgba(204, 50, 50, .08); color: {text}; font-size: 13px;
    }}
    .ninesig-history-message[hidden] {{ display: none; }}
    .ninesig-history-kpis {{
      display: grid; grid-template-columns: repeat(6, minmax(130px, 1fr));
      gap: 10px; margin-bottom: 24px;
    }}
    .ninesig-history-kpi {{
      padding: 14px 16px; background: {panel_bg};
      border-left: 3px solid {accent};
    }}
    .ninesig-history-kpi:nth-child(2) {{ border-left-color: {cat_2}; }}
    .ninesig-history-kpi:nth-child(3) {{ border-left-color: {green}; }}
    .ninesig-history-kpi:nth-child(4) {{ border-left-color: {red}; }}
    .ninesig-history-kpi:nth-child(5) {{ border-left-color: {cat_5}; }}
    .ninesig-history-kpi:nth-child(6) {{ border-left-color: {highlight}; }}
    .ninesig-history-kpi .label {{
      font-size: 10px; color: {text_light}; text-transform: uppercase;
      letter-spacing: .07em; font-weight: 700; margin-bottom: 5px;
    }}
    .ninesig-history-kpi .value {{
      font-family: 'Lora', Georgia, serif; font-size: 22px; font-weight: 700;
      color: {text}; line-height: 1.15; font-variant-numeric: tabular-nums;
    }}
    .ninesig-history-kpi .value.pos {{ color: {green}; }}
    .ninesig-history-kpi .value.neg {{ color: {red}; }}
    .ninesig-history-kpi .hint {{
      font-size: 11px; color: {text_light}; margin-top: 4px; line-height: 1.35;
    }}
    .ninesig-history-chart-grid {{
      display: grid; grid-template-columns: 1fr 1fr; gap: 26px; margin-bottom: 26px;
    }}
    .ninesig-history-chart-wide {{ grid-column: 1 / -1; }}
    .ninesig-history-chart {{
      height: 320px; min-height: 280px; width: 100%;
    }}
    .ninesig-history-chart-wide .ninesig-history-chart {{ height: 360px; }}
    .ninesig-history-table {{
      width: 100%; border-collapse: collapse; font-size: 13px;
      font-variant-numeric: tabular-nums;
    }}
    .ninesig-history-table th {{
      text-align: left; padding: 9px 10px; font-weight: 700;
      font-size: 10px; text-transform: uppercase; letter-spacing: .06em;
      color: {text_light}; background: {panel_bg};
      border-bottom: 1px solid {border}; white-space: nowrap;
    }}
    .ninesig-history-table th.num,
    .ninesig-history-table td.num {{ text-align: right; }}
    .ninesig-history-table td {{
      padding: 9px 10px; border-bottom: 1px solid {border};
      vertical-align: top; color: {text};
    }}
    .ninesig-history-table td.pos {{ color: {green}; font-weight: 600; }}
    .ninesig-history-table td.neg {{ color: {red}; font-weight: 600; }}
    .ninesig-history-table .notes {{
      color: {text_muted}; min-width: 240px; line-height: 1.4;
    }}
    .ninesig-action-badge {{
      display: inline-block; border-radius: 3px; padding: 3px 7px;
      font-size: 11px; font-weight: 700; line-height: 1.2;
      white-space: nowrap;
    }}
    .ninesig-action-buy {{ background: rgba(42, 134, 54, .12); color: {green}; }}
    .ninesig-action-sell {{ background: rgba(204, 50, 50, .10); color: {red}; }}
    .ninesig-action-no-action {{ background: {bg}; color: {text_light}; border: 1px solid {border}; }}
    .ninesig-action-skip {{ background: rgba(46, 110, 158, .10); color: {cat_2}; }}
    .ninesig-action-reset {{ background: rgba(233, 178, 55, .17); color: #8a6818; }}
    .ninesig-action-initial {{ background: rgba(46, 110, 158, .13); color: {cat_2}; }}
    .ninesig-history-source {{
      margin-top: 14px; font-size: 12px; color: {text_light};
      border-top: 1px solid {border}; padding-top: 12px;
    }}
    @media (max-width: 1050px) {{
      .ninesig-history-kpis {{ grid-template-columns: repeat(3, 1fr); }}
      .ninesig-history-chart-grid {{ grid-template-columns: 1fr; }}
      .ninesig-history-chart-wide {{ grid-column: auto; }}
    }}
    @media (max-width: 720px) {{
      .ninesig-mode-toggle {{
        display: grid; grid-template-columns: 1fr 1fr; width: 100%;
      }}
      .ninesig-mode-toggle button {{
        min-height: 44px; padding: 8px 6px; white-space: normal;
      }}
      .ninesig-scenario-bar {{
        grid-template-columns: 1fr; padding: 10px 12px; gap: 8px;
      }}
      .ninesig-field select,
      .ninesig-field input,
      .ninesig-history-reset {{
        min-height: 44px; font-size: 16px;
      }}
      .ninesig-history-reset {{ width: 100%; }}
      .ninesig-history-kpis {{ grid-template-columns: 1fr 1fr; }}
      .ninesig-history-kpi .value {{ font-size: 19px; }}
      .ninesig-history-chart {{ min-width: 560px; }}
      .ninesig-history-chart-scroll {{
        overflow-x: auto; -webkit-overflow-scrolling: touch;
      }}
    }}
    @media (max-width: 560px) {{
      .ninesig-history-kpis {{ grid-template-columns: 1fr; }}
      .ninesig-history-kpi {{ padding: 12px 14px; }}
      .ninesig-history-chart {{ min-width: 500px; height: 300px; }}
      .ninesig-history-chart-wide .ninesig-history-chart {{ height: 320px; }}
      .ninesig-history-table {{ min-width: 940px; font-size: 12px; }}
      .ninesig-history-table th,
      .ninesig-history-table td {{
        padding: 7px 8px;
      }}
      .ninesig-history-table .notes {{ min-width: 220px; }}
    }}

    /* Sticky filter bar */
    .filter-shell {{
      position: sticky; top: 0; z-index: 50;
      background: {bg}; padding: 8px 0; margin: 0 -32px 28px;
      border-bottom: 1px solid {border};
    }}
    .filter-bar {{
      display: flex; flex-wrap: wrap; gap: 6px 22px; align-items: center;
      padding: 10px 32px; max-width: 1280px; margin: 0 auto;
    }}
    .filter-group {{ display: flex; gap: 4px; align-items: center; }}
    .filter-label {{
      font-size: 10px; font-weight: 700; color: {text_light};
      text-transform: uppercase; letter-spacing: .08em; margin-right: 4px;
    }}
    .filter-btn {{
      font-family: inherit; font-size: 12px; font-weight: 500;
      padding: 4px 10px; border: 1px solid {border}; background: {bg};
      color: {text_muted}; border-radius: 4px; cursor: pointer;
      transition: all .15s ease;
    }}
    .filter-btn:hover {{ color: {text}; border-color: {accent}; }}
    .filter-btn.active {{ background: {accent}; color: white; border-color: {accent}; }}
    .filter-counter {{
      margin-left: auto; font-size: 12px; color: {text_muted};
      font-family: 'Inter', monospace;
    }}
    .filter-counter #filter-count {{ color: {text}; font-weight: 600; }}

    /* Two-column layout: left sidebar nav + main content */
    .layout {{
      display: flex; gap: 32px; align-items: flex-start;
      margin-top: 24px;
    }}
    .sidebar {{
      position: sticky; top: 16px;
      width: 200px; flex: 0 0 200px;
      align-self: flex-start;
      max-height: calc(100vh - 32px); overflow-y: auto;
      padding: 14px 0;
      border-top: 2px solid {accent};
    }}
    .sidebar-label {{
      font-size: 10px; font-weight: 700;
      color: {text_light}; text-transform: uppercase;
      letter-spacing: .08em; padding: 0 12px 8px;
    }}
    .sidebar nav {{
      display: flex; flex-direction: column; gap: 2px;
    }}
    .sidebar nav a {{
      font-family: 'Inter', system-ui, sans-serif;
      font-size: 13px; font-weight: 500; color: {text_muted};
      text-decoration: none;
      padding: 7px 12px; border-radius: 4px;
      transition: background .15s ease, color .15s ease;
      display: flex; align-items: center;
    }}
    .sidebar nav a:hover {{
      background: {panel_bg}; color: {text};
    }}
    .sidebar nav .nav-marker {{
      color: {text_light}; margin-right: 8px; font-size: 10px;
      width: 10px; display: inline-block;
    }}
    .main-content {{ flex: 1; min-width: 0; }}
    .section-heading {{
      font-family: 'Lora', Georgia, serif;
      font-size: 22px; font-weight: 600; color: {text};
      margin: 0 0 6px 0; letter-spacing: -0.015em;
    }}
    .section-desc {{
      font-size: 14px; color: {text_muted};
      margin: 0 0 22px 0; max-width: 880px; line-height: 1.55;
    }}
    .section-desc strong {{ color: {text}; }}
    .section-anchor {{ scroll-margin-top: 72px; }}

    /* Mini price chart in detail panel */
    .mini-chart-block {{ margin-top: 18px; padding-top: 14px; border-top: 1px solid {border}; }}
    .mini-tabs {{
      display: flex; flex-wrap: wrap; gap: 2px; margin: 4px 0 8px;
    }}
    .mini-tab {{
      font-family: 'Inter', system-ui, sans-serif;
      font-size: 11px; font-weight: 500; color: {text_muted};
      padding: 3px 8px; border: 1px solid {border}; background: {bg};
      border-radius: 3px; cursor: pointer; transition: all .12s ease;
    }}
    .mini-tab:hover {{ color: {text}; border-color: {accent}; }}
    .mini-tab.active {{ background: {accent}; color: white; border-color: {accent}; }}
    .mini-chart-canvas {{ height: 200px; min-height: 180px; }}

    /* KPI strip */
    .kpi-strip {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 10px; margin-bottom: 32px;
    }}
    .kpi-cell {{
      padding: 18px 18px; background: {panel_bg};
      border-left: 3px solid {accent};
    }}
    .kpi-cell.k1 {{ border-left-color: {accent}; }}
    .kpi-cell.k2 {{ border-left-color: {cat_3}; }}
    .kpi-cell.k3 {{ border-left-color: {cat_2}; }}
    .kpi-cell.k4 {{ border-left-color: {cat_5}; }}
    .kpi-num {{
      font-family: 'Lora', Georgia, serif; font-size: 28px; font-weight: 700;
      color: {text}; line-height: 1.1;
    }}
    .kpi-num.pos {{ color: {green}; }}
    .kpi-num.neg {{ color: {red}; }}
    .kpi-label {{
      font-size: 10px; color: {text_muted}; text-transform: uppercase;
      letter-spacing: .08em; margin-top: 8px; font-weight: 600;
    }}
    .kpi-sub {{ font-size: 11px; color: {text_light}; margin-top: 4px; }}

    /* Section / chart cards */
    .section {{ margin-bottom: 44px; }}
    .chart-block {{ margin-bottom: 16px; }}
    .chart-title-row {{ margin-bottom: 4px; }}
    .accent-bar {{
      display: block; width: 32px; height: 3px;
      background: {accent}; margin-bottom: 10px;
    }}
    .chart-title {{
      font-family: 'Lora', Georgia, serif; font-size: 20px; font-weight: 700;
      color: {text}; line-height: 1.2;
    }}
    .chart-subtitle {{
      font-family: 'Inter', sans-serif; font-size: 13px; color: {text_muted};
      margin-top: 4px; line-height: 1.55; max-width: 800px;
    }}
    .chart-card {{
      background: {card_bg};
      padding: 4px 0 0; margin-top: 16px;
    }}

    /* Timeframe pills */
    .timeframe-bar {{
      display: flex; gap: 4px; align-items: center; flex-wrap: wrap;
      margin-bottom: 4px;
    }}
    .timeframe-bar > span {{
      font-size: 10px; color: {text_light}; text-transform: uppercase;
      letter-spacing: .08em; font-weight: 700; margin-right: 8px;
    }}
    .timeframe-btn {{
      font-family: inherit; font-size: 12px; font-weight: 500;
      padding: 4px 12px; border: 1px solid {border}; background: {card_bg};
      color: {text_muted}; border-radius: 999px; cursor: pointer;
      transition: all .15s ease;
    }}
    .timeframe-btn:hover {{ color: {text}; border-color: {accent}; }}
    .timeframe-btn.active {{ background: {text}; color: white; border-color: {text}; }}

    .summary-grid {{
      display: grid; grid-template-columns: 1fr 1fr; gap: 28px;
    }}
    @media (max-width: 900px) {{
      .summary-grid {{ grid-template-columns: 1fr; }}
    }}

    /* Movers tables (replaces the bar chart) */
    .movers-tables {{
      display: grid; grid-template-columns: 1fr 1fr; gap: 16px;
      margin-top: 8px;
    }}
    .movers-col-title {{
      font-size: 10px; font-weight: 700; color: {text_light};
      text-transform: uppercase; letter-spacing: .08em; margin-bottom: 6px;
    }}
    .movers-table {{
      width: 100%; border-collapse: collapse; font-size: 13px;
      font-variant-numeric: tabular-nums;
    }}
    .movers-table th {{
      text-align: left; padding: 6px 10px; font-weight: 600;
      font-size: 10px; text-transform: uppercase; letter-spacing: .04em;
      color: {text_light}; border-bottom: 1px solid {border};
    }}
    .movers-table th.num {{ text-align: right; }}
    .movers-table td {{
      padding: 7px 10px; border-bottom: 1px solid {border};
    }}
    .movers-table td.num {{ text-align: right; font-weight: 600; }}
    .movers-table td.pos {{ color: {green}; }}
    .movers-table td.neg {{ color: {red}; }}
    .movers-table td.target {{
      color: {text_muted}; max-width: 180px; overflow: hidden;
      text-overflow: ellipsis; white-space: nowrap;
    }}

    /* Signal Board */
    .signal-board {{ background: {panel_bg}; padding: 14px 16px; border-left: 3px solid {accent}; }}
    .signal-tabs {{ display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 10px; }}
    .signal-tab {{
      font-family: inherit; font-size: 11px; font-weight: 600;
      padding: 4px 10px; border: 1px solid {border}; background: {card_bg};
      color: {text_muted}; border-radius: 999px; cursor: pointer;
      transition: all .15s ease; text-transform: uppercase; letter-spacing: .05em;
    }}
    .signal-tab:hover {{ color: {text}; border-color: {accent}; }}
    .signal-tab.active {{ background: {accent}; color: white; border-color: {accent}; }}
    .signal-caption {{
      font-size: 12px; color: {text_muted}; font-style: italic;
      margin: 6px 2px 10px; line-height: 1.55;
    }}
    .signal-table {{
      width: 100%; border-collapse: collapse; font-size: 13px;
      font-variant-numeric: tabular-nums;
    }}
    .signal-table thead th {{
      text-align: left; padding: 8px 10px; font-weight: 600;
      font-size: 10px; text-transform: uppercase; letter-spacing: .06em;
      color: {text_light}; background: {card_bg};
      border-bottom: 1px solid {axis};
      white-space: nowrap;
    }}
    .signal-table thead th.num {{ text-align: right; }}
    .signal-table tbody td {{
      padding: 7px 10px; border-bottom: 1px solid {border};
      vertical-align: baseline;
    }}
    .signal-table tbody td.num {{ text-align: right; font-weight: 600; }}
    .signal-table tbody td.pos {{ color: {green}; }}
    .signal-table tbody td.neg {{ color: {red}; }}
    .signal-table tbody td.tk {{ font-weight: 700; width: 80px; white-space: nowrap; }}
    .signal-table tbody td.tgt {{
      color: {text_muted}; max-width: 220px;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }}
    .signal-table tbody td.reason {{
      font-size: 11px; color: {text_light};
      white-space: nowrap;
    }}
    .signal-table tbody tr:last-child td {{ border-bottom: none; }}
    .label-chip {{
      display: inline-block; font-size: 9px; font-weight: 600;
      padding: 1px 6px; border-radius: 3px; letter-spacing: .04em;
      white-space: nowrap;
    }}
    .label-strong_uptrend {{ background: rgba(13,118,128,.12); color: {accent}; }}
    .label-uptrend_pullback {{ background: rgba(46,110,158,.12); color: {cat_2}; }}
    .label-rebound_attempt {{ background: rgba(233,178,55,.16); color: #8a6818; }}
    .label-downtrend {{ background: rgba(204,50,50,.10); color: {red}; }}
    .label-neutral {{ background: {bg}; color: {text_light}; border: 1px solid {border}; }}
    .label-insufficient_data {{ background: {bg}; color: {text_light}; border: 1px dashed {border}; }}

    /* TQQQ watch cards */
    .sig-tqqq-cards {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 10px; margin-bottom: 14px;
    }}
    .sig-tqqq-card {{
      background: {panel_bg}; padding: 14px 16px;
      border-left: 3px solid {accent};
    }}
    .sig-tqqq-card.flagged {{ border-left-color: {red}; }}
    .sig-tqqq-card .v {{
      font-family: 'Lora', Georgia, serif; font-size: 24px; font-weight: 700;
      color: {text}; line-height: 1.1;
    }}
    .sig-tqqq-card .v.pos {{ color: {green}; }}
    .sig-tqqq-card .v.neg {{ color: {red}; }}
    .sig-tqqq-card .l {{
      font-size: 10px; color: {text_light}; text-transform: uppercase;
      letter-spacing: .06em; margin-top: 6px; font-weight: 600;
    }}
    .sig-tqqq-card .sub {{ font-size: 11px; color: {text_light}; margin-top: 3px; }}
    .sig-note {{
      font-size: 12px; color: {text_light}; font-style: italic;
      margin-top: 14px; margin-bottom: 16px; line-height: 1.55;
      max-width: 820px;
    }}

    /* Drawdown Map columns */
    .drawdown-map {{
      display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px;
    }}
    .drawdown-col-title {{
      font-size: 10px; font-weight: 700; color: {text_light};
      text-transform: uppercase; letter-spacing: .06em; margin-bottom: 6px;
    }}
    .drawdown-list {{ font-size: 12px; }}
    .drawdown-list .row {{
      display: grid; grid-template-columns: 70px 1fr 70px;
      gap: 8px; align-items: baseline; padding: 5px 6px;
      border-bottom: 1px solid {border};
    }}
    .drawdown-list .row:last-child {{ border-bottom: none; }}
    .drawdown-list .tk {{ font-weight: 700; }}
    .drawdown-list .label {{ font-size: 10px; color: {text_light}; }}
    .drawdown-list .v {{ text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }}
    .drawdown-list .v.pos {{ color: {green}; }}
    .drawdown-list .v.neg {{ color: {red}; }}

    /* Detail panel signal block */
    .detail-signals {{ }}
    .detail-signals .signal-flags {{
      display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px;
    }}
    .detail-signals .flag {{
      font-size: 9px; font-weight: 700; letter-spacing: .05em;
      padding: 2px 7px; border-radius: 3px; text-transform: uppercase;
    }}
    .flag.oversold {{ background: rgba(46,110,158,.12); color: {cat_2}; }}
    .flag.deeply_oversold {{ background: rgba(204,50,50,.12); color: {red}; }}
    .flag.stretched {{ background: rgba(233,178,55,.16); color: #8a6818; }}
    .flag.very_stretched {{ background: rgba(204,50,50,.12); color: {red}; }}
    .flag.washed_out {{ background: rgba(13,118,128,.12); color: {accent}; }}
    .flag.falling_knife {{ background: rgba(204,50,50,.10); color: {red}; }}
    .flag.vol_expanding {{ background: rgba(233,178,55,.16); color: #8a6818; }}
    .flag.tracking_weirdness {{ background: rgba(199,113,94,.14); color: {cat_3}; }}
    .flag.new_high {{ background: rgba(42,134,54,.12); color: {green}; }}
    .flag.new_low {{ background: rgba(204,50,50,.10); color: {red}; }}
    .detail-signals .sig-row {{
      display: flex; justify-content: space-between; align-items: baseline;
      font-size: 12px; padding: 3px 0; border-bottom: 1px dotted {border};
    }}
    .detail-signals .sig-row:last-child {{ border-bottom: none; }}
    .detail-signals .sig-row .l {{ color: {text_light}; }}
    .detail-signals .sig-row .v {{ font-weight: 600; font-variant-numeric: tabular-nums; }}

    /* "How to read it" educational callout */
    .how-to-read {{
      margin-top: 12px; padding: 12px 14px;
      background: {panel_bg}; border-left: 3px solid {accent};
      font-size: 12px; line-height: 1.55; color: {text_muted};
    }}
    .how-to-read .label {{
      display: block; font-size: 9px; font-weight: 700;
      color: {text_light}; text-transform: uppercase;
      letter-spacing: .08em; margin-bottom: 4px;
    }}
    .how-to-read b {{ color: {text}; font-weight: 600; }}
    .how-to-read .good {{ color: {green}; font-weight: 600; }}
    .how-to-read .bad {{ color: {red}; font-weight: 600; }}

    /* Screener */
    .table-wrap {{ overflow-x: auto; background: {card_bg}; }}
    table.screener-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    .screener-table th {{
      text-align: left; padding: 10px 12px; font-weight: 600;
      font-size: 10px; text-transform: uppercase; letter-spacing: .06em;
      color: {text_light}; background: {panel_bg};
      border-bottom: 1px solid {border};
      cursor: pointer; user-select: none;
    }}
    .screener-table th:hover {{ color: {accent}; }}
    .screener-table th.num, .screener-table td.num {{
      text-align: right; font-variant-numeric: tabular-nums;
    }}
    .screener-table td {{
      padding: 9px 12px; border-bottom: 1px solid {border};
    }}
    .screener-table tr.screener-row {{ cursor: pointer; transition: background .12s ease; }}
    .screener-table tr.screener-row:hover {{ background: {panel_bg}; }}
    .screener-table tr.screener-row.expanded {{ background: {panel_bg}; }}
    .screener-table tr.screener-row.expanded .caret {{ transform: rotate(90deg); }}
    .caret {{
      display: inline-block; color: {text_light}; margin-right: 6px;
      transition: transform .15s ease; font-size: 10px; width: 8px;
    }}
    .note-dot {{ color: {highlight}; cursor: help; margin-left: 4px; }}
    .cell-target {{ max-width: 220px; overflow: hidden; text-overflow: ellipsis;
                    white-space: nowrap; color: {text_muted}; }}
    .cell-issuer {{ color: {text_muted}; }}
    .cell-lev {{ font-weight: 600; }}

    /* Detail panel */
    tr.detail-row > td {{ padding: 0; background: {panel_bg};
                          border-bottom: 1px solid {border}; }}
    .detail-panel {{
      display: grid;
      grid-template-columns: 1.2fr 1fr 1.5fr;
      grid-template-areas:
        "coverage holdings perf"
        "signals  signals  chart";
      gap: 22px;
      padding: 18px 24px; max-width: 100%;
    }}
    .detail-panel > .detail-coverage  {{ grid-area: coverage; }}
    .detail-panel > .detail-holdings  {{ grid-area: holdings; }}
    .detail-panel > .detail-perf      {{ grid-area: perf; }}
    .detail-panel > .detail-signals   {{ grid-area: signals; }}
    .detail-panel > .mini-chart-block {{ grid-area: chart; }}
    @media (max-width: 1200px) {{
      .detail-panel {{
        grid-template-columns: 1fr 1fr;
        grid-template-areas:
          "coverage holdings"
          "perf     perf"
          "signals  chart";
      }}
    }}
    @media (max-width: 700px) {{
      .detail-panel {{
        grid-template-columns: 1fr;
        grid-template-areas: "coverage" "holdings" "perf" "chart" "signals";
      }}
    }}
    .detail-block-title {{
      font-family: 'Inter', sans-serif; font-size: 10px; font-weight: 700;
      color: {text_light}; text-transform: uppercase; letter-spacing: .08em;
      margin-bottom: 8px;
    }}
    .detail-coverage h4 {{
      font-family: 'Lora', Georgia, serif; font-size: 16px; font-weight: 700;
      color: {text}; margin-bottom: 4px;
    }}
    .detail-coverage p {{ font-size: 13px; color: {text_muted}; line-height: 1.55; }}
    .detail-coverage .meta-line {{
      font-size: 11px; color: {text_light}; margin-top: 8px;
      font-family: 'Inter', monospace;
    }}
    .detail-coverage .source-link {{ color: {accent}; text-decoration: none; }}
    .detail-coverage .source-link:hover {{ text-decoration: underline; }}

    .detail-holdings .row {{
      display: flex; justify-content: space-between; align-items: baseline;
      font-size: 12px; padding: 3px 0; border-bottom: 1px dotted {border};
    }}
    .detail-holdings .row:last-child {{ border-bottom: none; }}
    .detail-holdings .row .name {{ color: {text}; flex: 1; padding-right: 10px;
                                    overflow: hidden; text-overflow: ellipsis;
                                    white-space: nowrap; }}
    .detail-holdings .row .kind {{
      font-size: 9px; color: {text_light}; text-transform: uppercase;
      letter-spacing: .06em; margin-right: 8px;
    }}
    .detail-holdings .row .weight {{ font-variant-numeric: tabular-nums; color: {text}; font-weight: 600; }}
    .detail-confidence {{
      display: inline-block; font-size: 9px; font-weight: 700;
      text-transform: uppercase; letter-spacing: .05em;
      padding: 2px 6px; border-radius: 3px;
    }}

    .detail-kpis {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }}
    .detail-kpi {{ }}
    .detail-kpi .v {{
      font-family: 'Lora', Georgia, serif; font-size: 16px; font-weight: 700;
      color: {text}; font-variant-numeric: tabular-nums;
    }}
    .detail-kpi .v.pos {{ color: {green}; }}
    .detail-kpi .v.neg {{ color: {red}; }}
    .detail-kpi .l {{
      font-size: 9px; color: {text_light}; text-transform: uppercase;
      letter-spacing: .05em; font-weight: 600; margin-top: 2px;
    }}

    /* Footer */
    .footer {{
      text-align: center; padding: 32px 0 0; margin-top: 36px;
      border-top: 1px solid {border}; font-size: 12px; color: {text_light};
    }}
    .footer a {{ color: {accent}; text-decoration: none; }}
    .footer a:hover {{ text-decoration: underline; }}
    .js-plotly-plot .plotly .modebar {{ display: none !important; }}
    .perf-notice {{ font-size: 11px; color: {text_light}; margin: 4px 0 8px; }}

    @media (max-width: 900px) {{
      .signal-table .reason {{ display: none; }}
      .signal-table thead th:nth-child(6) {{ display: none; }}
      .drawdown-map {{ grid-template-columns: 1fr; }}
      .sig-tqqq-cards {{ grid-template-columns: 1fr 1fr; }}
    }}
    @media (max-width: 768px) {{
      html, body {{ width: 100%; max-width: 100%; overflow-x: clip; }}
      .container {{ width: 100%; max-width: 100%; padding: 16px; min-width: 0; overflow-x: clip; }}
      .page-header, .intro-block, .intro-section, .section,
      .chart-title-row, .chart-subtitle, .kpi-strip, .summary-grid {{
        max-width: 100%; min-width: 0; overflow-wrap: break-word;
      }}
      .intro-section p, .page-header .subtitle {{ overflow-wrap: break-word; }}
      .layout {{
        flex-direction: column; gap: 0; align-items: stretch;
        width: 100%; max-width: 100%; overflow-x: clip;
      }}
      .main-content {{ width: 100%; max-width: 100%; min-width: 0; }}
      .sidebar {{
        position: static; width: 100%; flex: 1; max-height: none;
        overflow-y: visible; padding: 8px 0;
        border-top: 1px solid {border}; border-bottom: 1px solid {border};
      }}
      .sidebar-label {{ display: none; }}
      .sidebar nav {{
        flex-direction: row; flex-wrap: wrap; gap: 4px;
      }}
      .sidebar nav a {{ padding: 5px 10px; font-size: 12px; }}
      .filter-shell {{ margin: 0 -16px 16px; }}
      .filter-bar {{
        flex-direction: column; align-items: stretch;
        padding: 10px 16px; gap: 10px; max-width: 100%;
      }}
      .filter-group {{ flex-wrap: wrap; gap: 4px 6px; max-width: 100%; min-width: 0; }}
      .filter-counter {{ margin-left: 0; }}
      .page-header h1 {{ font-size: 22px; }}
      .summary-grid {{ grid-template-columns: 1fr; }}
      .kpi-strip {{ grid-template-columns: 1fr 1fr; }}
      .detail-panel {{ grid-template-columns: 1fr; padding: 14px 16px; }}
      .signal-board {{ padding: 12px; }}
      .signal-tabs {{ overflow-x: auto; flex-wrap: nowrap; }}
      .signal-tab {{ flex: 0 0 auto; }}
      .signal-table thead th:nth-child(4),
      .signal-table thead th:nth-child(5),
      .signal-table thead th:nth-child(6),
      .signal-table tbody td:nth-child(4),
      .signal-table tbody td:nth-child(5),
      .signal-table tbody td:nth-child(6) {{ display: none; }}
      .table-wrap {{ overflow-x: auto; -webkit-overflow-scrolling: touch; max-width: 100%; }}
      .js-plotly-plot {{ max-width: 100%; }}
      .chart-card {{ overflow-x: auto; max-width: 100%; }}
      #performance-chart, #risk-return-chart, #trend-stretch-chart {{
        width: 100% !important; max-width: 100%; min-width: 0;
      }}
    }}
  </style>
</head>
<body>
  <div class="container">

    <header class="page-header">
      <h1>Leveraged ETF Radar</h1>
      <p class="subtitle">A live tracker of every U.S.-listed leveraged equity ETF and ETN — the universe a daily-rebalanced product opens up to you, ranked by recent performance, risk, and technical signals.</p>
      <p class="meta">{total_funds} funds tracked &middot; updated {timestamp}</p>
    </header>

    <div class="layout">
    <aside class="sidebar">
      <div class="sidebar-label">Sections</div>
      <nav aria-label="Section navigation">
        <a href="#9sig"><span class="nav-marker">◆</span>9Sig Plan</a>
        <a href="#9sig-history"><span class="nav-marker">◆</span>9Sig History</a>
        <a href="#scan"><span class="nav-marker">▶</span>Daily Scan</a>
        <a href="#performance"><span class="nav-marker">▶</span>Performance</a>
        <a href="#movers"><span class="nav-marker">▶</span>Movers &amp; Risk</a>
        <a href="#trend-drawdown"><span class="nav-marker">▶</span>Trend &amp; Drawdown</a>
        <a href="#signals"><span class="nav-marker">▶</span>Signal Lists</a>
        <a href="#screener"><span class="nav-marker">▶</span>Screener</a>
      </nav>
    </aside>
    <main class="main-content">

    <div class="intro-block">
      <div class="intro-section">
        <h3>What this is</h3>
        <p>
          A scan tool for U.S.-listed <strong>leveraged ETFs and ETNs</strong> — funds that promise a
          daily multiple (±1.5×, ±2×, ±3×) of some target: a broad index like the S&amp;P 500 or Nasdaq-100,
          a sector or industry basket, a single mega-cap stock, or a thematic basket. The dashboard does
          not give advice. It surfaces what's popping, what's stretched, what's oversold, and what's
          near 52-week lows, so you can scan the whole universe in a few minutes rather than reading
          {total_funds} factsheets.
        </p>
      </div>
      <div class="intro-section">
        <h3>How to read it</h3>
        <p>
          The <strong>filter bar</strong> at the top narrows the universe by direction, leverage,
          exposure type, and product (ETF vs ETN). Every chart, table, and KPI below reacts to the
          filter in one click. The <strong>timeframe pills</strong> next to the main line chart drive
          the headline KPIs, the Movers tables, and the Risk vs Drawdown scatter — flip them to see
          how the picture changes over 1M, 3M, YTD, 1Y, 3Y, 5Y, or 10Y. The <strong>Signal Board</strong>
          is your daily scan: Leaders, Laggards, Stretched, Oversold, and Near Lows. Click any row in
          the screener for holdings, KPIs, signal flags, and the optional TQQQ 9Sig watch context
          when it applies.
        </p>
      </div>
    </div>

    <div id="9sig" class="section-anchor">
      {nine_sig_panel}
    </div>

    <div class="section section-anchor ninesig-history-section" id="9sig-history">
      <h3 class="section-heading">9Sig History</h3>
      <p class="section-desc">
        Jason Kelly's published quarterly 9Sig history is a read-only reference. New Plan Simulation
        starts a fresh independent 60/40 plan from the selected date and capital.
      </p>

      <div class="ninesig-mode-toggle" aria-label="9Sig view mode">
        <button type="button" class="active" data-ninesig-mode="published">Published History</button>
        <button type="button" data-ninesig-mode="simulation">New Plan Simulation</button>
      </div>

      <div class="ninesig-mode-panel" id="ninesig-simulation-panel" hidden>
        <div class="ninesig-scenario-bar" aria-label="9Sig simulation controls">
          <label class="ninesig-field" for="ninesig-sim-start-quarter">
            <span>Start Quarter</span>
            <select id="ninesig-sim-start-quarter"></select>
          </label>
          <label class="ninesig-field" for="ninesig-sim-start-value">
            <span>Starting Capital</span>
            <input id="ninesig-sim-start-value" type="number" min="1" step="1000" inputmode="numeric">
          </label>
          <button class="ninesig-history-reset" id="ninesig-sim-reset" type="button">Reset</button>
        </div>
      </div>

      <div class="ninesig-history-message" id="ninesig-history-message" hidden></div>
      <div class="ninesig-history-kpis" id="ninesig-history-kpis"></div>

      <div class="ninesig-history-chart-grid">
        <div class="chart-block ninesig-history-chart-wide">
          <div class="chart-title-row">
            <span class="accent-bar"></span>
            <h2 class="chart-title">Portfolio value</h2>
            <p class="chart-subtitle" id="ninesig-value-subtitle">Published quarterly post-action portfolio value.</p>
          </div>
          <div class="ninesig-history-chart-scroll">
            <div id="ninesig-value-chart" class="ninesig-history-chart"></div>
          </div>
        </div>
        <div class="chart-block">
          <div class="chart-title-row">
            <span class="accent-bar"></span>
            <h2 class="chart-title">TQQQ / AGG allocation</h2>
            <p class="chart-subtitle" id="ninesig-allocation-subtitle">Post-action allocation at each quarterly action date.</p>
          </div>
          <div class="ninesig-history-chart-scroll">
            <div id="ninesig-allocation-chart" class="ninesig-history-chart"></div>
          </div>
        </div>
        <div class="chart-block">
          <div class="chart-title-row">
            <span class="accent-bar"></span>
            <h2 class="chart-title">Quarter-over-quarter change</h2>
            <p class="chart-subtitle" id="ninesig-qoq-subtitle">Published QoQ portfolio change.</p>
          </div>
          <div class="ninesig-history-chart-scroll">
            <div id="ninesig-qoq-chart" class="ninesig-history-chart"></div>
          </div>
        </div>
      </div>

      <div class="table-wrap">
        <table class="ninesig-history-table" id="ninesig-history-table">
          <thead id="ninesig-history-head">
            <tr>
              <th>Quarter</th>
              <th>Action Date</th>
              <th>Action</th>
              <th class="num">TQQQ %</th>
              <th class="num">AGG %</th>
              <th class="num">Portfolio Value</th>
              <th class="num">QoQ Change</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody id="ninesig-history-body"></tbody>
        </table>
      </div>
      <p class="ninesig-history-source">
        Source: 9Sig History PDF, quarterly post-action balances. Portfolio value = TQQQ + AGG + cash.
        <span id="ninesig-simulation-source-note" hidden>New Plan Simulation uses Yahoo adjusted close for TQQQ and AGG, so splits and distributions are embedded. The 100% intra-quarter spike reset is unsupported/not modeled because this view uses quarterly adjusted closes.</span>
      </p>
    </div>

    {filter_bar}

    <div class="kpi-strip" id="kpi-strip"></div>

    {summary_html}

    <div class="section section-anchor" id="scan">
      <h3 class="section-heading">Daily Scan</h3>
      <p class="section-desc">
        Five quick scans across the filtered universe — <strong>Leaders</strong> and
        <strong>Laggards</strong> by overall return, <strong>Stretched</strong> and
        <strong>Oversold</strong> by RSI and 52-week distance, and <strong>Near Lows</strong>
        for funds within 15% of their year low. Click a tab to switch the lens. Designed as a
        60-second "what's standing out today" check.
      </p>
      <div class="chart-title-row">
        <span class="accent-bar"></span>
        <h2 class="chart-title">Daily scan board — what's standing out today</h2>
        <p class="chart-subtitle">
          Five quick scans across the filtered universe. Each tab ranks the top 10 funds by its own signal.
          <strong>Leaders</strong>: strongest overall performers.
          <strong>Laggards</strong>: weakest overall performers.
          <strong>Oversold</strong>: RSI 14 below 30, or sitting near the 52-week low.
          <strong>Stretched</strong>: RSI above 70 or far above the 50-day moving average.
          <strong>Near Lows</strong>: within 15% of the 52-week low. All labels are analytical
          &mdash; not trade signals.
        </p>
      </div>
      <div class="signal-board">
        <div class="signal-tabs" id="signal-tabs">
          <button class="signal-tab active" data-stab="leaders">Leaders</button>
          <button class="signal-tab" data-stab="laggards">Laggards</button>
          <button class="signal-tab" data-stab="stretched">Stretched</button>
          <button class="signal-tab" data-stab="oversold">Oversold</button>
          <button class="signal-tab" data-stab="near_lows">Near Lows</button>
        </div>
        <p class="signal-caption" id="signal-caption"></p>
        <div class="table-wrap">
          <table class="signal-table" id="signal-table">
            <thead><tr id="signal-thead"></tr></thead>
            <tbody id="signal-tbody"></tbody>
          </table>
        </div>
      </div>
    </div>

    <div class="section section-anchor" id="performance">
      <h3 class="section-heading">Performance</h3>
      <p class="section-desc">
        Headline cumulative return chart with timeframe pills (<strong>1M / 3M / YTD / 1Y / 3Y / 5Y / 10Y</strong>).
        The pills here drive the KPIs above and the Movers / Risk-vs-Drawdown views below — switch them to
        compress or expand the time horizon for every analytic in the dashboard. Useful for sanity-checking
        whether the period you're looking at is dominated by one big move or by steady drift.
      </p>
      <div class="chart-title-row">
        <span class="accent-bar"></span>
        <h2 class="chart-title">Cumulative return over the selected window</h2>
        <p class="chart-subtitle">
          Each line is one fund, rebased to zero at the start of the timeframe selected on the pills
          below. Colour follows the leverage tier (3× teal, 2× blue, ±2×/±3× terra/rose, etc.) and
          ticker labels sit at the right edge of each line. To keep the chart readable we show only
          the 15 funds with the largest absolute return over the selected window; everything else
          is filterable in the screener.
        </p>
      </div>
      <div class="chart-card">
        <div class="timeframe-bar" id="timeframe-bar">
          <span>Range</span>
          <button class="timeframe-btn" data-tf="1M">1M</button>
          <button class="timeframe-btn" data-tf="3M">3M</button>
          <button class="timeframe-btn active" data-tf="YTD">YTD</button>
          <button class="timeframe-btn" data-tf="1Y">1Y</button>
          <button class="timeframe-btn" data-tf="3Y">3Y</button>
          <button class="timeframe-btn" data-tf="5Y">5Y</button>
          <button class="timeframe-btn" data-tf="10Y">10Y</button>
        </div>
        <div class="perf-notice" id="perf-notice"></div>
        <div id="performance-chart" style="height: 480px;"></div>
      </div>
    </div>

    <div class="section section-anchor" id="movers">
      <h3 class="section-heading">Movers &amp; Risk</h3>
      <p class="section-desc">
        Two views of the timeframe you've picked above: <strong>ranked best/worst movers</strong> (the
        asymmetry between leveraged-long winners and inverse-fund losers is most visible during strong
        directional moves), and the classic <strong>risk-vs-drawdown scatter</strong> — return earned
        per dollar of pain, with both axes bounded at the −100% mathematical floor of a daily-reset LETF.
      </p>
      <div class="summary-grid">
        <div class="chart-block">
          <div class="chart-title-row">
            <span class="accent-bar"></span>
            <h2 class="chart-title">Best and worst movers <span id="movers-tf-label" style="font-style:italic;font-weight:600;">YTD</span></h2>
            <p class="chart-subtitle">
              Top 8 and bottom 8 funds in the filtered set, ranked over the timeframe selected on the
              chart above. Useful for the at-a-glance "what's leading vs lagging right now" question,
              and for spotting the asymmetry between leveraged-long winners and inverse-fund losers
              during strong directional moves.
            </p>
          </div>
          <div class="movers-tables">
            <div class="movers-col">
              <div class="movers-col-title">Best <span class="movers-col-tf">YTD</span></div>
              <table class="movers-table" id="movers-top">
                <thead><tr><th>Ticker</th><th>Target</th><th class="num movers-col-tf">YTD</th></tr></thead>
                <tbody></tbody>
              </table>
            </div>
            <div class="movers-col">
              <div class="movers-col-title">Worst <span class="movers-col-tf">YTD</span></div>
              <table class="movers-table" id="movers-bottom">
                <thead><tr><th>Ticker</th><th>Target</th><th class="num movers-col-tf">YTD</th></tr></thead>
                <tbody></tbody>
              </table>
            </div>
          </div>
        </div>
        <div class="chart-block">
          <div class="chart-title-row">
            <span class="accent-bar"></span>
            <h2 class="chart-title">Return vs maximum drawdown</h2>
            <p class="chart-subtitle">
              For each filtered fund: return over the selected timeframe (y-axis) plotted against the
              worst peak-to-trough fall over the past year (x-axis). The classic risk/return chart for
              leveraged products — drawdown is more visceral than annualized volatility because it's
              the actual money you'd have watched drain at the worst moment. Both axes are bounded at
              −100% (the math limit for a daily-reset LETF — you can lose all your principal but no more).
            </p>
          </div>
          <div id="risk-return-chart" style="height: 420px;"></div>
          <div class="how-to-read">
            <span class="label">How to read it</span>
            <p>Each dot is a fund. The <b>x-axis</b> shows worst-case pain — the largest drawdown over the past year, where <b>0%</b> means never fell from a high and <b>-100%</b> means total loss. The <b>y-axis</b> shows the fund's return over the timeframe pill you have selected. Marker size and colour both group funds by leverage tier (3× lines are largest and teal/rose). <span class="good">Best zone</span>: upper-right — strong returns earned with shallow drawdowns. <span class="bad">Worst zone</span>: lower-left — deep drawdowns with no upside to compensate. Trading liquidity is not shown here on purpose; click any row in the table below to see it for a specific fund.</p>
          </div>
        </div>
      </div>
    </div>

    <div class="section section-anchor" id="trend-drawdown">
      <h3 class="section-heading">Trend &amp; Drawdown</h3>
      <p class="section-desc">
        Two technical views. <strong>Trend vs Stretch</strong> plots distance from the 200-day moving
        average against RSI 14 — a quick map of which funds are stretched, oversold, or sitting in
        neutral territory. The <strong>Drawdown map</strong> separates funds within striking distance
        of their 52-week lows from those that just printed fresh 1-year highs.
      </p>
      <div class="summary-grid">
        <div class="chart-block">
          <div class="chart-title-row">
            <span class="accent-bar"></span>
            <h2 class="chart-title">Trend vs Stretch</h2>
            <p class="chart-subtitle">
              Two technical indicators on one chart: x-axis shows percent distance from the 200-day
              moving average (long-term trend), y-axis shows RSI 14 (short-term momentum oscillator,
              0–100 scale). To keep it readable we label only the most stretched and most oversold
              outliers, plus TQQQ always. Marker size scales with realized volatility — bigger dots are
              the more volatile funds.
            </p>
          </div>
          <div id="trend-stretch-chart" style="height: 420px;"></div>
          <div class="how-to-read">
            <span class="label">How to read it</span>
            <p>Each dot is a fund. <b>x-axis</b>: % distance from 200-day moving average — funds far to the right are well above their long-term trend (stretched), funds far to the left have broken below it. <b>y-axis</b>: RSI 14 — momentum oscillator, above 70 = overheated, below 30 = oversold. <b>Colour</b>: trend regime. <span class="good">Best</span>: middle-right (uptrending, momentum healthy). <span class="bad">Caution</span>: top-right (stretched + overheated) or bottom-left (broken + oversold). Crosshairs mark the canonical neutral lines (0% MA distance and RSI 50).</p>
          </div>
        </div>
        <div class="chart-block">
          <div class="chart-title-row">
            <span class="accent-bar"></span>
            <h2 class="chart-title">Drawdown map</h2>
            <p class="chart-subtitle">
              Three ranked columns showing where the filtered universe sits in its 52-week range.
              <strong>Near 52W low</strong> = closest to the year's bottom (think "things usually don't
              go much lower from here" candidates). <strong>Deepest from ATH</strong> = largest
              pullbacks from all-time high regardless of when that high was set. <strong>New 1Y
              highs</strong> = funds that just printed a 252-day high, sorted by 20-day return.
            </p>
          </div>
          <div class="drawdown-map">
            <div>
              <div class="drawdown-col-title">Near 52W low</div>
              <div class="drawdown-list" id="dd-near-low"></div>
            </div>
            <div>
              <div class="drawdown-col-title">Deepest from ATH</div>
              <div class="drawdown-list" id="dd-from-ath"></div>
            </div>
            <div>
              <div class="drawdown-col-title">New 1Y highs</div>
              <div class="drawdown-list" id="dd-new-highs"></div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="section section-anchor" id="signals">
      <h3 class="section-heading">Signal Lists</h3>
      <p class="section-desc">
        Three compact ranked lists for the daily scan workflow: <strong>what's working</strong>,
        <strong>what's stretched</strong>, and <strong>what's weakening</strong>. Designed to be
        skimmed top-to-bottom in under a minute. The same five signal categories from the Daily Scan
        board, but reorganized into "actionable" groupings rather than tabs.
      </p>
      <div class="chart-title-row">
        <span class="accent-bar"></span>
        <h2 class="chart-title">Performance, stretch, and weak spots</h2>
        <p class="chart-subtitle">
          Three compact ranked lists for the core scan: what is working, what is stretched, and what
          is still weak or washed out. The full sortable universe remains in the screener below.
        </p>
      </div>
      <div class="drawdown-map">
        <div>
          <div class="drawdown-col-title">Best YTD</div>
          <div class="drawdown-list" id="rank-leaders"></div>
        </div>
        <div>
          <div class="drawdown-col-title">Stretched</div>
          <div class="drawdown-list" id="rank-stretched"></div>
        </div>
        <div>
          <div class="drawdown-col-title">Weak / low</div>
          <div class="drawdown-list" id="rank-weak"></div>
        </div>
      </div>
      <div class="sig-tqqq-cards" id="tqqq-watch-cards" style="margin-top:16px;"></div>
      <p class="sig-note">TQQQ gets a small Kelly 9Sig watch card because it is the canonical fund for that discipline. Other ETFs are evaluated by ordinary performance, trend, stretch, and drawdown signals first.</p>
    </div>

    <div class="section section-anchor" id="screener">
      <h3 class="section-heading">Screener</h3>
      <p class="section-desc">
        The filtered universe as a sortable table — <strong>click any row</strong> to expand holdings,
        per-fund KPIs, signal flags, and the optional TQQQ 9Sig watch context when applicable. Filter
        and timeframe selections from above propagate here, so what you see is consistent with the rest
        of the dashboard. Best as the destination tool when you want to drill into a specific ticker.
      </p>
      <div class="chart-title-row">
        <span class="accent-bar"></span>
        <h2 class="chart-title">The screener</h2>
        <p class="chart-subtitle">All filtered funds. Click a row to expand its top holdings, full KPI grid, and source confidence. Click a column header to sort.</p>
      </div>
      {screener}
    </div>

    </main>
    </div>

    <div class="footer">
      Data: <a href="https://finance.yahoo.com" target="_blank" rel="noopener">Yahoo Finance</a>
      &amp; issuer pages. Confidence labels reflect provider; not investment advice.
      &middot; <a href="https://newsc2.com">&larr; newsc2.com</a>
    </div>

  </div>

  <script id="radar-data" type="application/json">{radar_json}</script>

  <script>
  (function() {{
    const RADAR = JSON.parse(document.getElementById('radar-data').textContent);
    const COLORS = {colors_json};

    const FONT_SANS = "'Inter', system-ui, sans-serif";
    const FONT_SERIF = "'Lora', Georgia, serif";

    // -----------------------------------------------------------
    // Filter state
    // -----------------------------------------------------------
    const filterState = new Map();
    document.querySelectorAll('.filter-group').forEach(g => {{
      const active = g.querySelector('.filter-btn.active');
      filterState.set(g.dataset.group, active ? active.dataset.filter : '*');
    }});

    function tickerMatches(ticker) {{
      const cls = (RADAR.funds[ticker] && RADAR.funds[ticker].classes) || '';
      const tokens = cls.split(' ');
      for (const sel of filterState.values()) {{
        if (sel === '*') continue;
        const alts = sel.split(',').map(s => s.trim().replace(/^\\./, ''));
        if (!alts.some(a => tokens.includes(a))) return false;
      }}
      return true;
    }}

    function filteredTickers() {{
      return Object.keys(RADAR.funds).filter(tickerMatches);
    }}

    // -----------------------------------------------------------
    // Number formatting
    // -----------------------------------------------------------
    function fmtPct(v, decimals=1) {{
      if (v === null || v === undefined || isNaN(v)) return '—';
      const sign = v >= 0 ? '+' : '';
      return sign + v.toFixed(decimals) + '%';
    }}
    function fmtMoney(v) {{
      if (v === null || v === undefined || isNaN(v) || v === 0) return '—';
      if (v >= 1e9) return '$' + (v/1e9).toFixed(2) + 'B';
      if (v >= 1e6) return '$' + (v/1e6).toFixed(0) + 'M';
      if (v >= 1e3) return '$' + (v/1e3).toFixed(0) + 'K';
      return '$' + v.toFixed(0);
    }}
    function fmtPrice(v) {{
      if (v === null || v === undefined || isNaN(v)) return '—';
      if (v >= 1000) return '$' + v.toLocaleString('en-US', {{ maximumFractionDigits: 0 }});
      return '$' + v.toFixed(2);
    }}
    function median(arr) {{
      const filt = arr.filter(v => v !== null && v !== undefined && !isNaN(v)).sort((a, b) => a - b);
      if (!filt.length) return null;
      const mid = Math.floor(filt.length / 2);
      return filt.length % 2 ? filt[mid] : (filt[mid-1] + filt[mid]) / 2;
    }}
    const usdFullFmt = new Intl.NumberFormat('en-US', {{
      style: 'currency', currency: 'USD', maximumFractionDigits: 0,
    }});
    function fmtUsdFull(v) {{
      if (v === null || v === undefined || isNaN(v)) return '—';
      return usdFullFmt.format(v);
    }}
    function fmtAlloc(v) {{
      if (v === null || v === undefined || isNaN(v)) return '—';
      return (v * 100).toFixed(1) + '%';
    }}
    function fmtQoq(v) {{
      if (v === null || v === undefined || isNaN(v)) return '—';
      const pct = v * 100;
      return (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%';
    }}
    function fmtIsoDate(iso) {{
      if (!iso) return '—';
      const p = iso.split('-');
      return p.length === 3 ? p[1] + '/' + p[2] + '/' + p[0] : iso;
    }}
    function escapeHtml(s) {{
      return String(s || '').replace(/[&<>"']/g, function(ch) {{
        return {{ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }}[ch];
      }});
    }}

    // -----------------------------------------------------------
    // 9Sig published history + independent new-plan simulation
    // -----------------------------------------------------------
    const NINESIG_HISTORY = RADAR.nine_sig_history || [];
    const NINESIG_ADJUSTED_QUARTERS = RADAR.nine_sig_adjusted_quarters || [];
    const DEFAULT_NINESIG_SIM_START_VALUE = 1000000;
    let ninesigMode = 'published';

    function nineSigActionClass(type) {{
      if (type === 'initial') return 'ninesig-action-initial';
      if (type === 'reset_60_40') return 'ninesig-action-reset';
      if (type === 'no_action') return 'ninesig-action-no-action';
      if (type === 'skip_sell_30_down') return 'ninesig-action-skip';
      if (type === 'sell_tqqq') return 'ninesig-action-sell';
      return 'ninesig-action-buy';
    }}

    function yearsBetween(startIso, endIso) {{
      const start = Date.parse(startIso + 'T00:00:00');
      const end = Date.parse(endIso + 'T00:00:00');
      if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return 0;
      return (end - start) / (365.25 * 86400000);
    }}

    function maxNineSigDrawdown(rows) {{
      let peak = -Infinity;
      let maxDd = 0;
      rows.forEach(function(row) {{
        const value = row.portfolioValue;
        if (!Number.isFinite(value) || value <= 0) return;
        peak = Math.max(peak, value);
        if (peak > 0) maxDd = Math.min(maxDd, value / peak - 1);
      }});
      return maxDd;
    }}

    function firstAvailableNineSigStartDate() {{
      return NINESIG_ADJUSTED_QUARTERS.length ? NINESIG_ADJUSTED_QUARTERS[0].date : '';
    }}

    function lastAvailableNineSigStartDate() {{
      return NINESIG_ADJUSTED_QUARTERS.length
        ? NINESIG_ADJUSTED_QUARTERS[NINESIG_ADJUSTED_QUARTERS.length - 1].date
        : '';
    }}

    function clampNineSigStartDate(iso) {{
      const first = firstAvailableNineSigStartDate();
      const last = lastAvailableNineSigStartDate();
      if (!first || !last) return iso || '';
      if (!iso || iso < first) return first;
      if (iso > last) return last;
      return iso;
    }}

    function firstNineSigQuarterOnOrAfter(iso) {{
      const startDate = clampNineSigStartDate(iso);
      if (!startDate) return '';
      const row = NINESIG_ADJUSTED_QUARTERS.find(function(quarter) {{
        return quarter.date >= startDate;
      }});
      return row ? row.date : lastAvailableNineSigStartDate();
    }}

    function defaultNineSigSimulationStartDate() {{
      if (NINESIG_HISTORY.length && NINESIG_HISTORY[0].date) {{
        return firstNineSigQuarterOnOrAfter(NINESIG_HISTORY[0].date);
      }}
      return firstAvailableNineSigStartDate();
    }}

    function readNineSigSimulationInputs() {{
      const quarterSelect = document.getElementById('ninesig-sim-start-quarter');
      const valueInput = document.getElementById('ninesig-sim-start-value');
      const startDate = firstNineSigQuarterOnOrAfter(
        quarterSelect ? quarterSelect.value : defaultNineSigSimulationStartDate()
      );
      const rawValue = valueInput ? parseFloat(valueInput.value) : DEFAULT_NINESIG_SIM_START_VALUE;
      const startingCapital = Number.isFinite(rawValue) && rawValue > 0
        ? rawValue
        : DEFAULT_NINESIG_SIM_START_VALUE;
      return {{ startDate, startingCapital }};
    }}

    function priorTwoYearNineSigHigh(idx) {{
      const start = Math.max(0, idx - 8);
      let high = null;
      for (let i = start; i < idx; i += 1) {{
        const close = NINESIG_ADJUSTED_QUARTERS[i].tqqq_adjusted_close;
        if (Number.isFinite(close)) high = high === null ? close : Math.max(high, close);
      }}
      return high;
    }}

    function buildNineSigSimulationRow(priceRow, signalTarget, tqqqValue, aggValue, actionType, actionSummary, thirtyDownActive, skippedSellSignals, previousPortfolioValue) {{
      const tqqqPrice = priceRow.tqqq_adjusted_close;
      const aggPrice = priceRow.agg_adjusted_close;
      const portfolioValue = tqqqValue + aggValue;
      const qoqChange = previousPortfolioValue && previousPortfolioValue > 0
        ? portfolioValue / previousPortfolioValue - 1
        : null;
      return {{
        quarter: priceRow.quarter,
        rebalanceDate: priceRow.date,
        tqqqAdjustedClose: tqqqPrice,
        aggAdjustedClose: aggPrice,
        signalTarget: signalTarget,
        tqqqShares: tqqqPrice > 0 ? tqqqValue / tqqqPrice : 0,
        aggShares: aggPrice > 0 ? aggValue / aggPrice : 0,
        tqqqValue: tqqqValue,
        aggValue: aggValue,
        portfolioValue: portfolioValue,
        tqqqAllocation: portfolioValue > 0 ? tqqqValue / portfolioValue : 0,
        aggAllocation: portfolioValue > 0 ? aggValue / portfolioValue : 0,
        actionType: actionType,
        actionSummary: actionSummary,
        thirtyDownActive: thirtyDownActive,
        skippedSellSignals: skippedSellSignals,
        qoqChange: qoqChange,
      }};
    }}

    function simulateNineSigNewPlan() {{
      if (!NINESIG_ADJUSTED_QUARTERS.length) return [];
      const inputs = readNineSigSimulationInputs();
      if (!inputs.startDate || inputs.startingCapital <= 0) return [];
      const startIndex = NINESIG_ADJUSTED_QUARTERS.findIndex(function(row) {{
        return row.date >= inputs.startDate;
      }});
      if (startIndex < 0) return [];

      const rows = [];
      const initial = NINESIG_ADJUSTED_QUARTERS[startIndex];
      let tqqqValue = inputs.startingCapital * 0.60;
      let aggValue = inputs.startingCapital * 0.40;
      let signalTarget = tqqqValue;
      let thirtyDownActive = false;
      let skippedSellSignals = 0;
      let postThirtyDownAwaitingReset = false;

      rows.push(buildNineSigSimulationRow(
        initial, signalTarget, tqqqValue, aggValue,
        'initial', 'Start fresh at 60/40.',
        thirtyDownActive, skippedSellSignals, null,
      ));
      let previousPortfolioValue = rows[rows.length - 1].portfolioValue;

      for (let idx = startIndex + 1; idx < NINESIG_ADJUSTED_QUARTERS.length; idx += 1) {{
        const priceRow = NINESIG_ADJUSTED_QUARTERS[idx];
        const priorRow = rows[rows.length - 1];
        tqqqValue = priorRow.tqqqShares * priceRow.tqqq_adjusted_close;
        aggValue = priorRow.aggShares * priceRow.agg_adjusted_close;
        signalTarget *= 1.09;

        const priorHigh = priorTwoYearNineSigHigh(idx);
        if (priorHigh !== null && priceRow.tqqq_adjusted_close <= priorHigh * 0.70 && !thirtyDownActive) {{
          thirtyDownActive = true;
          skippedSellSignals = 0;
          postThirtyDownAwaitingReset = false;
        }}

        let actionType = 'no_action';
        let actionSummary = 'No trade; TQQQ matched the signal target.';
        const epsilon = 0.005;

        if (tqqqValue < signalTarget - epsilon) {{
          const shortfall = signalTarget - tqqqValue;
          const buyingPower = aggValue * 0.90;
          const amountToMove = Math.min(shortfall, buyingPower);
          aggValue -= amountToMove;
          tqqqValue += amountToMove;
          if (amountToMove < shortfall - epsilon) {{
            signalTarget = tqqqValue;
            actionType = 'buy_tqqq_limited';
            actionSummary = 'Bought ' + fmtUsdFull(amountToMove) + ' TQQQ; AGG buying-power throttle limited the buy, so the signal target was reset to actual TQQQ value.';
          }} else {{
            actionType = 'buy_tqqq';
            actionSummary = 'Bought ' + fmtUsdFull(amountToMove) + ' TQQQ to restore the signal target.';
          }}
        }} else if (tqqqValue > signalTarget + epsilon) {{
          const surplus = tqqqValue - signalTarget;
          if (thirtyDownActive && skippedSellSignals < 2) {{
            skippedSellSignals += 1;
            actionType = 'skip_sell_30_down';
            actionSummary = 'Skipped sell signal ' + skippedSellSignals + ' of 2 during 30 Down.';
            if (skippedSellSignals === 2) {{
              thirtyDownActive = false;
              postThirtyDownAwaitingReset = true;
            }}
          }} else if (postThirtyDownAwaitingReset) {{
            const portfolioValue = tqqqValue + aggValue;
            tqqqValue = portfolioValue * 0.60;
            aggValue = portfolioValue * 0.40;
            signalTarget = tqqqValue;
            postThirtyDownAwaitingReset = false;
            actionType = 'reset_60_40';
            actionSummary = 'Reset the independent plan to 60/40 after 30 Down.';
          }} else {{
            tqqqValue -= surplus;
            aggValue += surplus;
            actionType = 'sell_tqqq';
            actionSummary = 'Sold ' + fmtUsdFull(surplus) + ' TQQQ above the signal target.';
          }}
        }}

        rows.push(buildNineSigSimulationRow(
          priceRow, signalTarget, tqqqValue, aggValue,
          actionType, actionSummary,
          thirtyDownActive, skippedSellSignals, previousPortfolioValue,
        ));
        previousPortfolioValue = rows[rows.length - 1].portfolioValue;
      }}
      return rows;
    }}

    function renderNineSigHistoryKpis(rows, mode) {{
      const host = document.getElementById('ninesig-history-kpis');
      if (!host) return;
      if (!rows.length) {{
        host.innerHTML = '';
        return;
      }}

      const first = rows[0];
      const latest = rows[rows.length - 1];

      function card(label, value, hint, cls) {{
        return `<div class="ninesig-history-kpi">
          <div class="label">${{label}}</div>
          <div class="value ${{cls || ''}}">${{value}}</div>
          <div class="hint">${{hint || ''}}</div>
        </div>`;
      }}

      if (mode === 'simulation') {{
        const years = yearsBetween(first.rebalanceDate, latest.rebalanceDate);
        const cagr = years > 0
          ? Math.pow(latest.portfolioValue / first.portfolioValue, 1 / years) - 1
          : null;
        const dd = maxNineSigDrawdown(rows);
        const state = latest.thirtyDownActive
          ? 'Active (' + latest.skippedSellSignals + '/2 sells skipped)'
          : 'Inactive';
        host.innerHTML = [
          card('Latest portfolio value', fmtUsdFull(latest.portfolioValue), latest.quarter + ' · ' + fmtIsoDate(latest.rebalanceDate)),
          card('CAGR', fmtQoq(cagr), first.quarter + ' → ' + latest.quarter, cagr != null && cagr >= 0 ? 'pos' : 'neg'),
          card('Max drawdown', fmtQoq(dd), 'Peak-to-trough portfolio value', 'neg'),
          card('Current allocation', fmtAlloc(latest.tqqqAllocation) + ' / ' + fmtAlloc(latest.aggAllocation), 'TQQQ / AGG'),
          card('30 Down state', state, 'Sell skips reset when a fresh 30 Down triggers'),
          card('Start', fmtUsdFull(first.portfolioValue), fmtIsoDate(first.rebalanceDate) + ' · fresh 60/40'),
        ].join('');
        return;
      }}

      const years = yearsBetween(first.date, latest.date);
      const cagr = years > 0
        ? Math.pow(latest.portfolio_value / first.portfolio_value, 1 / years) - 1
        : null;
      const qoqRows = rows.filter(function(r) {{
        return r.qoq_change !== null && r.qoq_change !== undefined;
      }});
      const best = qoqRows.length
        ? qoqRows.reduce(function(a, b) {{ return b.qoq_change > a.qoq_change ? b : a; }})
        : null;
      const worst = qoqRows.length
        ? qoqRows.reduce(function(a, b) {{ return b.qoq_change < a.qoq_change ? b : a; }})
        : null;

      host.innerHTML = [
        card('Latest portfolio value', fmtUsdFull(latest.portfolio_value), latest.quarter + ' · ' + fmtIsoDate(latest.date)),
        card('CAGR since inception', fmtQoq(cagr), first.quarter + ' → ' + latest.quarter, cagr != null && cagr >= 0 ? 'pos' : 'neg'),
        card('Best quarter', best ? fmtQoq(best.qoq_change) : '—', best ? best.quarter : '', 'pos'),
        card('Worst quarter', worst ? fmtQoq(worst.qoq_change) : '—', worst ? worst.quarter : '', 'neg'),
        card('Current allocation', fmtAlloc(latest.tqqq_allocation) + ' / ' + fmtAlloc(latest.agg_allocation), 'TQQQ / AGG'),
      ].join('');
    }}

    function setNineSigTableHead(mode) {{
      const head = document.getElementById('ninesig-history-head');
      if (!head) return;
      if (mode === 'simulation') {{
        head.innerHTML = `<tr>
          <th>Quarter</th>
          <th>Rebalance Date</th>
          <th>Action</th>
          <th class="num">TQQQ %</th>
          <th class="num">AGG %</th>
          <th class="num">Portfolio Value</th>
          <th class="num">QoQ Change</th>
          <th class="num">Signal Target</th>
          <th>30 Down</th>
          <th>Notes</th>
        </tr>`;
        return;
      }}
      head.innerHTML = `<tr>
        <th>Quarter</th>
        <th>Action Date</th>
        <th>Action</th>
        <th class="num">TQQQ %</th>
        <th class="num">AGG %</th>
        <th class="num">Portfolio Value</th>
        <th class="num">QoQ Change</th>
        <th>Notes</th>
      </tr>`;
    }}

    function renderNineSigHistoryTable(rows, mode) {{
      const body = document.getElementById('ninesig-history-body');
      if (!body) return;
      setNineSigTableHead(mode);
      if (mode === 'simulation') {{
        body.innerHTML = rows.map(function(row) {{
          const qoq = row.qoqChange;
          const qoqClass = qoq === null || qoq === undefined ? '' : (qoq >= 0 ? 'pos' : 'neg');
          return `<tr>
            <td data-label="Quarter"><strong>${{escapeHtml(row.quarter)}}</strong></td>
            <td data-label="Rebalance Date">${{fmtIsoDate(row.rebalanceDate)}}</td>
            <td data-label="Action"><span class="ninesig-action-badge ${{nineSigActionClass(row.actionType)}}">${{escapeHtml(row.actionType.replaceAll('_', ' '))}}</span></td>
            <td data-label="TQQQ %" class="num">${{fmtAlloc(row.tqqqAllocation)}}</td>
            <td data-label="AGG %" class="num">${{fmtAlloc(row.aggAllocation)}}</td>
            <td data-label="Portfolio Value" class="num">${{fmtUsdFull(row.portfolioValue)}}</td>
            <td data-label="QoQ Change" class="num ${{qoqClass}}">${{fmtQoq(qoq)}}</td>
            <td data-label="Signal Target" class="num">${{fmtUsdFull(row.signalTarget)}}</td>
            <td data-label="30 Down">${{row.thirtyDownActive ? 'Active' : 'Inactive'}}</td>
            <td data-label="Notes" class="notes">${{escapeHtml(row.actionSummary)}}</td>
          </tr>`;
        }}).join('');
        return;
      }}
      body.innerHTML = rows.map(function(row) {{
        const qoq = row.qoq_change;
        const qoqClass = qoq === null || qoq === undefined ? '' : (qoq >= 0 ? 'pos' : 'neg');
        return `<tr>
          <td data-label="Quarter"><strong>${{escapeHtml(row.quarter)}}</strong></td>
          <td data-label="Action Date">${{fmtIsoDate(row.date)}}</td>
          <td data-label="Action"><span class="ninesig-action-badge ${{nineSigActionClass(row.action_type)}}">${{escapeHtml(row.action)}}</span></td>
          <td data-label="TQQQ %" class="num">${{fmtAlloc(row.tqqq_allocation)}}</td>
          <td data-label="AGG %" class="num">${{fmtAlloc(row.agg_allocation)}}</td>
          <td data-label="Portfolio Value" class="num">${{fmtUsdFull(row.portfolio_value)}}</td>
          <td data-label="QoQ Change" class="num ${{qoqClass}}">${{fmtQoq(qoq)}}</td>
          <td data-label="Notes" class="notes">${{escapeHtml(row.notes)}}</td>
        </tr>`;
      }}).join('');
    }}

    function setNineSigSubtitles(mode) {{
      const valueSubtitle = document.getElementById('ninesig-value-subtitle');
      const allocationSubtitle = document.getElementById('ninesig-allocation-subtitle');
      const qoqSubtitle = document.getElementById('ninesig-qoq-subtitle');
      if (mode === 'simulation') {{
        if (valueSubtitle) valueSubtitle.textContent = 'Independent plan value using TQQQ and AGG adjusted closes.';
        if (allocationSubtitle) allocationSubtitle.textContent = 'Post-action allocation after each simulated quarterly rebalance.';
        if (qoqSubtitle) qoqSubtitle.textContent = 'Quarter-over-quarter simulated portfolio return.';
        return;
      }}
      if (valueSubtitle) valueSubtitle.textContent = 'Published quarterly post-action portfolio value.';
      if (allocationSubtitle) allocationSubtitle.textContent = 'Published post-action allocation at each quarterly action date.';
      if (qoqSubtitle) qoqSubtitle.textContent = 'Published QoQ portfolio change.';
    }}

    function normalizedNineSigRows(rows, mode) {{
      if (mode === 'simulation') {{
        return rows.map(function(row) {{
          return {{
            date: row.rebalanceDate,
            quarter: row.quarter,
            value: row.portfolioValue,
            tqqqAllocation: row.tqqqAllocation,
            aggAllocation: row.aggAllocation,
            qoq: row.qoqChange,
          }};
        }});
      }}
      return rows.map(function(row) {{
        return {{
          date: row.date,
          quarter: row.quarter,
          value: row.portfolio_value,
          tqqqAllocation: row.tqqq_allocation,
          aggAllocation: row.agg_allocation,
          qoq: row.qoq_change,
        }};
      }});
    }}

    function clearNineSigCharts() {{
      ['ninesig-value-chart', 'ninesig-allocation-chart', 'ninesig-qoq-chart'].forEach(function(id) {{
        const el = document.getElementById(id);
        if (el && window.Plotly) Plotly.purge(el);
      }});
    }}

    function renderNineSigHistoryCharts(rows, mode) {{
      if (!rows.length) return;
      const chartRows = normalizedNineSigRows(rows, mode);
      const chartOpts = {{ displayModeBar: false, scrollZoom: false, responsive: true, doubleClick: false }};
      const x = chartRows.map(function(r) {{ return r.date; }});
      const latest = chartRows[chartRows.length - 1];

      Plotly.newPlot('ninesig-value-chart', [{{
        x: x,
        y: chartRows.map(function(r) {{ return r.value; }}),
        type: 'scatter',
        mode: 'lines+markers',
        line: {{ color: COLORS.accent, width: 2.2 }},
        marker: {{ size: 5, color: COLORS.accent, line: {{ color: 'white', width: 1 }} }},
        hovertemplate: '<b>%{{x}}</b><br>Value: $%{{y:,.0f}}<extra></extra>',
      }}], editorialLayout({{
        margin: {{ l: 74, r: 78, t: 8, b: 42 }},
        showlegend: false,
        yaxis: {{
          gridcolor: COLORS.grid, gridwidth: 0.5, zeroline: false, fixedrange: true,
          showline: false, ticks: '', tickfont: {{ color: COLORS.text_light, size: 11 }},
          tickprefix: '$', tickformat: ',.0f',
          title: {{ text: 'Portfolio value', font: {{ size: 11, color: COLORS.text_light }} }},
        }},
        annotations: [{{
          x: latest.date, y: latest.value,
          text: '<b>' + fmtMoney(latest.value) + '</b>',
          showarrow: false, xanchor: 'left', yanchor: 'middle', xshift: 8,
          font: {{ size: 11, color: COLORS.accent, family: FONT_SANS }},
        }}],
        hovermode: 'x unified',
      }}), chartOpts);

      const lastAggMid = latest.aggAllocation * 50;
      const lastTqqqMid = latest.aggAllocation * 100 + latest.tqqqAllocation * 50;
      Plotly.newPlot('ninesig-allocation-chart', [
        {{
          x: x,
          y: chartRows.map(function(r) {{ return r.aggAllocation * 100; }}),
          type: 'scatter', mode: 'lines', stackgroup: 'alloc',
          line: {{ width: 0, color: COLORS.cat_5 }},
          fillcolor: COLORS.cat_5,
          name: 'AGG',
          hovertemplate: 'AGG: %{{y:.1f}}%<extra></extra>',
        }},
        {{
          x: x,
          y: chartRows.map(function(r) {{ return r.tqqqAllocation * 100; }}),
          type: 'scatter', mode: 'lines', stackgroup: 'alloc',
          line: {{ width: 0, color: COLORS.accent }},
          fillcolor: COLORS.accent,
          name: 'TQQQ',
          hovertemplate: 'TQQQ: %{{y:.1f}}%<extra></extra>',
        }},
      ], editorialLayout({{
        margin: {{ l: 58, r: 64, t: 8, b: 42 }},
        showlegend: false,
        yaxis: {{
          gridcolor: COLORS.grid, gridwidth: 0.5, fixedrange: true,
          showline: false, ticks: '', tickfont: {{ color: COLORS.text_light, size: 11 }},
          ticksuffix: '%', range: [0, 100],
          title: {{ text: 'Allocation', font: {{ size: 11, color: COLORS.text_light }} }},
        }},
        annotations: [
          {{
            x: latest.date, y: lastTqqqMid, text: 'TQQQ',
            showarrow: false, xanchor: 'left', yanchor: 'middle', xshift: 8,
            font: {{ size: 11, color: COLORS.accent, family: FONT_SANS }},
          }},
          {{
            x: latest.date, y: lastAggMid, text: 'AGG',
            showarrow: false, xanchor: 'left', yanchor: 'middle', xshift: 8,
            font: {{ size: 11, color: COLORS.cat_5, family: FONT_SANS }},
          }},
        ],
        hovermode: 'x unified',
      }}), chartOpts);

      const qoqRows = chartRows.filter(function(r) {{
        return r.qoq !== null && r.qoq !== undefined;
      }});
      Plotly.newPlot('ninesig-qoq-chart', [{{
        x: qoqRows.map(function(r) {{ return r.date; }}),
        y: qoqRows.map(function(r) {{ return r.qoq * 100; }}),
        type: 'bar',
        marker: {{
          color: qoqRows.map(function(r) {{
            return r.qoq >= 0 ? COLORS.green : COLORS.red;
          }}),
        }},
        hovertemplate: '<b>%{{x}}</b><br>QoQ: %{{y:+.1f}}%<extra></extra>',
      }}], editorialLayout({{
        margin: {{ l: 56, r: 24, t: 8, b: 42 }},
        showlegend: false,
        yaxis: {{
          gridcolor: COLORS.grid, gridwidth: 0.5, fixedrange: true,
          showline: false, ticks: '', tickfont: {{ color: COLORS.text_light, size: 11 }},
          ticksuffix: '%',
          title: {{ text: 'QoQ change', font: {{ size: 11, color: COLORS.text_light }} }},
        }},
        shapes: [{{
          type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: 0, y1: 0,
          line: {{ color: COLORS.text_light, dash: 'dot', width: 1 }},
        }}],
      }}), chartOpts);
    }}

    function syncNineSigModeUi() {{
      document.querySelectorAll('[data-ninesig-mode]').forEach(function(button) {{
        button.classList.toggle('active', button.dataset.ninesigMode === ninesigMode);
      }});
      const simPanel = document.getElementById('ninesig-simulation-panel');
      if (simPanel) simPanel.hidden = ninesigMode !== 'simulation';
      const sourceNote = document.getElementById('ninesig-simulation-source-note');
      if (sourceNote) sourceNote.hidden = ninesigMode !== 'simulation';
      setNineSigSubtitles(ninesigMode);
    }}

    function renderNineSigHistory() {{
      const section = document.getElementById('9sig-history');
      const message = document.getElementById('ninesig-history-message');
      if (!NINESIG_HISTORY.length && !NINESIG_ADJUSTED_QUARTERS.length) {{
        if (section) section.style.display = 'none';
        return;
      }}
      if (section) section.style.display = '';
      syncNineSigModeUi();

      const rows = ninesigMode === 'simulation' ? simulateNineSigNewPlan() : NINESIG_HISTORY;
      if (!rows.length) {{
        if (message) {{
          message.hidden = false;
          message.textContent = ninesigMode === 'simulation'
            ? 'New Plan Simulation is unavailable because full adjusted-close data for both TQQQ and AGG is missing.'
            : 'Published 9Sig history is unavailable.';
        }}
        renderNineSigHistoryKpis([], ninesigMode);
        renderNineSigHistoryTable([], ninesigMode);
        clearNineSigCharts();
        return;
      }}
      if (message) {{
        message.hidden = true;
        message.textContent = '';
      }}
      renderNineSigHistoryKpis(rows, ninesigMode);
      renderNineSigHistoryCharts(rows, ninesigMode);
      renderNineSigHistoryTable(rows, ninesigMode);
    }}

    function wireNineSigHistoryControls() {{
      const quarterSelect = document.getElementById('ninesig-sim-start-quarter');
      const valueInput = document.getElementById('ninesig-sim-start-value');
      const reset = document.getElementById('ninesig-sim-reset');
      document.querySelectorAll('[data-ninesig-mode]').forEach(function(button) {{
        button.addEventListener('click', function() {{
          ninesigMode = button.dataset.ninesigMode || 'published';
          renderNineSigHistory();
        }});
      }});

      if (quarterSelect) {{
        quarterSelect.innerHTML = NINESIG_ADJUSTED_QUARTERS.map(function(row) {{
          return '<option value="' + row.date + '">' + escapeHtml(row.quarter + ' · ' + fmtIsoDate(row.date)) + '</option>';
        }}).join('');
        quarterSelect.value = defaultNineSigSimulationStartDate();
        quarterSelect.addEventListener('change', renderNineSigHistory);
      }}
      if (valueInput) {{
        valueInput.value = Math.round(DEFAULT_NINESIG_SIM_START_VALUE);
        valueInput.addEventListener('input', renderNineSigHistory);
      }}
      if (reset) {{
        reset.addEventListener('click', function() {{
          if (quarterSelect) quarterSelect.value = defaultNineSigSimulationStartDate();
          if (valueInput) valueInput.value = Math.round(DEFAULT_NINESIG_SIM_START_VALUE);
          renderNineSigHistory();
        }});
      }}
    }}

    // -----------------------------------------------------------
    // Timeframe state (drives 4 widgets in concert)
    // -----------------------------------------------------------
    const TIMEFRAME_DAYS = {{ "1M": 30, "3M": 90, "1Y": 365, "3Y": 365*3, "5Y": 365*5, "10Y": 365*10 }};
    let activeTimeframe = "YTD";
    function tfMetricKey() {{ return activeTimeframe.toLowerCase(); }}  // YTD->'ytd', 1M->'1m', etc.
    function tfReturn(tk) {{
      const m = RADAR.metrics[tk];
      return m ? m[tfMetricKey()] : null;
    }}

    // -----------------------------------------------------------
    // KPI strip — timeframe-aware
    // -----------------------------------------------------------
    function renderKPIStrip() {{
      const tickers = filteredTickers();
      const rets = tickers.map(tfReturn).filter(v => v != null);
      const vols = tickers.map(t => RADAR.metrics[t] && RADAR.metrics[t].vol).filter(v => v != null);

      let bestTk = null, worstTk = null, bestY = -Infinity, worstY = Infinity;
      for (const tk of tickers) {{
        const y = tfReturn(tk);
        if (y == null) continue;
        if (y > bestY) {{ bestY = y; bestTk = tk; }}
        if (y < worstY) {{ worstY = y; worstTk = tk; }}
      }}

      const medianRet = median(rets);
      const medianVol = median(vols);
      const bestNum = bestTk ? `<span class="pos">${{fmtPct(bestY)}}</span>` : '—';
      const worstNum = worstTk ? `<span class="neg">${{fmtPct(worstY)}}</span>` : '—';
      const medClass = medianRet != null ? (medianRet >= 0 ? 'pos' : 'neg') : '';
      const medianStr = medianRet != null ? `<span class="${{medClass}}">${{fmtPct(medianRet)}}</span>` : '—';

      document.getElementById('kpi-strip').innerHTML = `
        <div class="kpi-cell k1">
          <div class="kpi-num">${{bestNum}}</div>
          <div class="kpi-label">Best ${{activeTimeframe}} &middot; ${{bestTk || '—'}}</div>
          <div class="kpi-sub">${{(bestTk && RADAR.funds[bestTk] && RADAR.funds[bestTk].target) || ''}}</div>
        </div>
        <div class="kpi-cell k2">
          <div class="kpi-num">${{worstNum}}</div>
          <div class="kpi-label">Worst ${{activeTimeframe}} &middot; ${{worstTk || '—'}}</div>
          <div class="kpi-sub">${{(worstTk && RADAR.funds[worstTk] && RADAR.funds[worstTk].target) || ''}}</div>
        </div>
        <div class="kpi-cell k3">
          <div class="kpi-num">${{medianStr}}</div>
          <div class="kpi-label">Median ${{activeTimeframe}}</div>
          <div class="kpi-sub">across filtered set</div>
        </div>
        <div class="kpi-cell k4">
          <div class="kpi-num">${{medianVol != null ? medianVol.toFixed(0) + '%' : '—'}}</div>
          <div class="kpi-label">Median realised vol</div>
          <div class="kpi-sub">annualised, last 1Y</div>
        </div>
      `;
    }}

    // -----------------------------------------------------------
    // Editorial layout helpers
    // -----------------------------------------------------------
    function editorialLayout(extra) {{
      return Object.assign({{
        paper_bgcolor: COLORS.bg, plot_bgcolor: COLORS.bg,
        font: {{ family: FONT_SANS, color: COLORS.text, size: 12 }},
        margin: {{ l: 60, r: 30, t: 14, b: 50 }},
        xaxis: {{
          gridcolor: COLORS.grid, gridwidth: 0.5, zeroline: false, fixedrange: true,
          showline: true, linecolor: COLORS.axis, linewidth: 1,
          ticks: 'outside', ticklen: 4, tickcolor: COLORS.axis,
          tickfont: {{ color: COLORS.text_light, size: 11 }},
        }},
        yaxis: {{
          gridcolor: COLORS.grid, gridwidth: 0.5, zeroline: false, fixedrange: true,
          showline: false, ticks: '',
          tickfont: {{ color: COLORS.text_light, size: 11 }},
        }},
        showlegend: false,
      }}, extra || {{}});
    }}

    // -----------------------------------------------------------
    // Performance line chart
    // -----------------------------------------------------------
    function timeframeStartIdx(tf) {{
      const ts = RADAR.timestamps;
      if (!ts.length) return 0;
      if (tf === "YTD") {{
        const jan1 = new Date(new Date().getFullYear(), 0, 1).getTime();
        for (let i = 0; i < ts.length; i++) if (ts[i] >= jan1) return Math.max(0, i - 1);
        return ts.length - 1;
      }}
      const days = TIMEFRAME_DAYS[tf];
      const cutoff = Date.now() - days * 86400 * 1000;
      for (let i = 0; i < ts.length; i++) if (ts[i] >= cutoff) return i;
      return 0;
    }}

    function renderPerformance() {{
      const startIdx = timeframeStartIdx(activeTimeframe);
      const dates = RADAR.timestamps.slice(startIdx).map(t => new Date(t));
      const candidates = filteredTickers();
      // Rank by absolute return OVER THE ACTIVE TIMEFRAME (not always YTD).
      // This spreads the y-range so the chart doesn't get crushed by a
      // single outlier on an unrelated time window.
      const ranked = candidates
        .map(tk => ({{ tk, mag: Math.abs(tfReturn(tk) || 0) }}))
        .sort((a, b) => b.mag - a.mag);
      const cap = 15;
      const visible = ranked.slice(0, cap).map(x => x.tk);
      const notice = document.getElementById('perf-notice');
      if (candidates.length > cap) {{
        notice.textContent = "Showing top " + cap + " of " + candidates.length + " filtered funds (by |" + activeTimeframe + "|).";
      }} else {{
        notice.textContent = candidates.length + " fund" + (candidates.length === 1 ? "" : "s") + " shown.";
      }}

      const traces = [];
      for (const tk of visible) {{
        const series = RADAR.prices[tk];
        if (!series || !series.length) continue;
        const slice = series.slice(startIdx);
        let baseIdx = -1, base = null;
        for (let i = 0; i < slice.length; i++) {{
          if (slice[i] !== null) {{ baseIdx = i; base = slice[i]; break; }}
        }}
        if (base === null || base === 0) continue;
        const y = slice.map(p => p === null ? null : (p / base - 1) * 100);
        traces.push({{
          x: dates, y: y, mode: 'lines', name: tk,
          line: {{ color: (RADAR.funds[tk] && RADAR.funds[tk].color) || COLORS.muted, width: 1.4 }},
          hovertemplate: tk + ': %{{y:+.1f}}%<extra></extra>',
          connectgaps: false,
        }});
      }}

      // Anti-overlap label stacking at the right edge
      const labelData = [];
      const allY = [];
      for (const tr of traces) {{
        let lastIdx = -1;
        for (let i = tr.y.length - 1; i >= 0; i--) {{
          if (tr.y[i] !== null && tr.y[i] !== undefined) {{ lastIdx = i; break; }}
        }}
        if (lastIdx === -1) continue;
        labelData.push({{ x: tr.x[lastIdx], y: tr.y[lastIdx], text: tr.name, color: tr.line.color }});
        for (const v of tr.y) if (v !== null && v !== undefined) allY.push(v);
      }}
      labelData.sort((a, b) => b.y - a.y);
      const yMax = allY.length ? Math.max.apply(null, allY) : 0;
      const yMin = allY.length ? Math.min.apply(null, allY) : 0;
      const yRange = (yMax - yMin) || 1;
      const minGap = yRange * 0.028;
      for (let i = 1; i < labelData.length; i++) {{
        if (labelData[i - 1].y - labelData[i].y < minGap) {{
          labelData[i].y = labelData[i - 1].y - minGap;
        }}
      }}
      const annotations = labelData.map(d => ({{
        x: d.x, y: d.y, text: d.text,
        showarrow: false, xanchor: 'left', yanchor: 'middle', xshift: 4,
        font: {{ size: 10, color: d.color, family: FONT_SANS }},
      }}));

      // Y-axis range: floor at -100% (LETF cumulative returns can't go below);
      // small headroom above the max so labels don't clip.
      const obsMin = allY.length ? Math.min.apply(null, allY) : 0;
      const obsMax = allY.length ? Math.max.apply(null, allY) : 0;
      const yFloor = Math.max(obsMin - 5, -100);
      const yCeil = obsMax + Math.max(5, (obsMax - yFloor) * 0.08);

      const layout = editorialLayout({{
        margin: {{ l: 50, r: 80, t: 14, b: 40 }},
        yaxis: {{
          gridcolor: COLORS.grid, gridwidth: 0.5, zeroline: false, fixedrange: true,
          showline: false, ticks: '',
          tickfont: {{ color: COLORS.text_light, size: 11 }},
          title: {{ text: 'Return (%)', font: {{ size: 11, color: COLORS.text_light }} }},
          ticksuffix: '%',
          range: [yFloor, yCeil],
        }},
        annotations: annotations,
        shapes: [{{ type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: 0, y1: 0,
                   line: {{ color: COLORS.text_light, dash: 'dot', width: 1 }} }}],
      }});
      Plotly.newPlot('performance-chart', traces, layout,
        {{ displayModeBar: false, scrollZoom: false, responsive: true, doubleClick: false }});
    }}

    // -----------------------------------------------------------
    // Best/worst movers — two ranked tables (replaces the bar chart)
    // -----------------------------------------------------------
    function renderMoversTables() {{
      const tickers = filteredTickers();
      const rows = tickers
        .map(tk => ({{ tk, ret: tfReturn(tk), target: RADAR.funds[tk].target }}))
        .filter(r => r.ret != null);
      const top = [...rows].sort((a, b) => b.ret - a.ret).slice(0, 8);
      const bot = [...rows].sort((a, b) => a.ret - b.ret).slice(0, 8);

      function rowHtml(r) {{
        const cls = r.ret >= 0 ? 'pos' : 'neg';
        const target = r.target ? r.target.replace(/</g, '&lt;') : '—';
        return '<tr>'
          + '<td><strong>' + r.tk + '</strong></td>'
          + '<td class="target">' + target + '</td>'
          + '<td class="num ' + cls + '">' + fmtPct(r.ret) + '</td>'
          + '</tr>';
      }}

      const topBody = document.querySelector('#movers-top tbody');
      const botBody = document.querySelector('#movers-bottom tbody');
      const emptyRow = '<tr><td colspan="3" style="color:' + COLORS.text_light
        + ';font-style:italic;padding:12px;">No funds match.</td></tr>';
      if (topBody) topBody.innerHTML = top.map(rowHtml).join('') || emptyRow;
      if (botBody) botBody.innerHTML = bot.map(rowHtml).join('') || emptyRow;

      // Update the timeframe label inside the section header + column headers
      document.querySelectorAll('.movers-col-tf').forEach(el => el.textContent = activeTimeframe);
      const movTfLabel = document.getElementById('movers-tf-label');
      if (movTfLabel) movTfLabel.textContent = activeTimeframe;
    }}

    // -----------------------------------------------------------
    // Return vs drawdown — timeframe-aware
    //   x: max drawdown 1Y (negative; 0 = no drawdown, -100 = total loss)
    //   y: return over the active timeframe
    //   color/size: leverage tier
    // Upper-right = best (high return, shallow drawdown).
    // Lower-left = worst (deep drawdown, no upside).
    // -----------------------------------------------------------
    function renderRiskReturn() {{
      const tickers = filteredTickers();
      const byLev = new Map();
      for (const tk of tickers) {{
        const m = RADAR.metrics[tk]; const f = RADAR.funds[tk];
        if (!m || m.dd == null) continue;
        const ret = tfReturn(tk);
        if (ret == null) continue;
        if (!f || f.leverage == null) continue;
        if (!byLev.has(f.leverage)) byLev.set(f.leverage, []);
        byLev.get(f.leverage).push({{
          tk, dd: Math.max(m.dd, -100), ret: Math.max(ret, -100),
          color: f.color, lev: f.leverage,
        }});
      }}
      const traces = [];
      const sortedLevs = [...byLev.keys()].sort((a, b) => Math.abs(b) - Math.abs(a));
      const allRet = [];
      for (const lev of sortedLevs) {{
        const rows = byLev.get(lev);
        for (const r of rows) allRet.push(r.ret);
        traces.push({{
          x: rows.map(r => r.dd), y: rows.map(r => r.ret),
          mode: 'markers+text',
          text: rows.map(r => r.tk),
          textposition: 'top center',
          textfont: {{ size: 9, color: COLORS.text_muted, family: FONT_SANS }},
          name: (lev > 0 ? '+' : '') + lev + '×',
          marker: {{
            color: rows[0].color,
            size: rows.map(() => 7 + Math.abs(lev) * 2.5),
            line: {{ color: 'white', width: 1 }},
            opacity: 0.85,
          }},
          hovertemplate: '<b>%{{text}}</b><br>Max DD 1Y: %{{x:+.1f}}%<br>'
            + activeTimeframe + ': %{{y:+.1f}}%<extra></extra>',
        }});
      }}
      const yMin = allRet.length ? Math.min.apply(null, allRet) : 0;
      const yMax = allRet.length ? Math.max.apply(null, allRet) : 0;
      const yFloor = Math.max(yMin - 5, -100);
      const yCeil = yMax + Math.max(5, (yMax - yFloor) * 0.10);
      const layout = editorialLayout({{
        margin: {{ l: 70, r: 30, t: 14, b: 56 }},
        xaxis: {{
          gridcolor: COLORS.grid, gridwidth: 0.5, zeroline: false, fixedrange: true,
          showline: true, linecolor: COLORS.axis, linewidth: 1,
          ticks: 'outside', ticklen: 4, tickcolor: COLORS.axis,
          tickfont: {{ color: COLORS.text_light, size: 11 }},
          title: {{ text: 'Max drawdown over 1Y (%)',
                    font: {{ size: 11, color: COLORS.text_light }}, standoff: 12 }},
          ticksuffix: '%',
          range: [-100, 5],
        }},
        yaxis: {{
          gridcolor: COLORS.grid, gridwidth: 0.5, fixedrange: true,
          showline: false, ticks: '',
          tickfont: {{ color: COLORS.text_light, size: 11 }},
          title: {{ text: activeTimeframe + ' return (%)',
                    font: {{ size: 11, color: COLORS.text_light }}, standoff: 12 }},
          ticksuffix: '%',
          range: [yFloor, yCeil],
        }},
        shapes: [
          // Horizontal zero-return line
          {{ type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: 0, y1: 0,
             line: {{ color: COLORS.text_light, dash: 'dot', width: 1 }} }},
          // Vertical zero-drawdown line (rightmost = best)
          {{ type: 'line', xref: 'x', x0: 0, x1: 0, yref: 'paper', y0: 0, y1: 1,
             line: {{ color: COLORS.text_light, dash: 'dot', width: 1 }} }},
        ],
      }});
      Plotly.newPlot('risk-return-chart', traces, layout,
        {{ displayModeBar: false, scrollZoom: false, responsive: true, doubleClick: false }});
    }}

    // -----------------------------------------------------------
    // Signal Board — 5 tabs
    // -----------------------------------------------------------
    let activeSignalTab = "leaders";
    const TREND_REGIME_LABELS = {{
      strong_uptrend: "Strong uptrend",
      uptrend_pullback: "Pullback in uptrend",
      rebound_attempt: "Rebound attempt",
      downtrend: "Downtrend",
      neutral: "Neutral",
      insufficient_data: "—",
    }};

    function regimeChip(regime) {{
      const text = TREND_REGIME_LABELS[regime] || regime;
      return `<span class="label-chip label-${{regime}}">${{text}}</span>`;
    }}

    const SIGNAL_TAB_META = {{
      leaders: {{
        caption: "Best-performing funds in the filtered set, ranked by YTD return. This is the cleanest 'what is working' view.",
        col4_h: '20D return', col5_h: 'RSI 14',
      }},
      laggards: {{
        caption: "Weakest funds in the filtered set, ranked by YTD return. Use this with Near Lows to separate ordinary laggards from true washouts.",
        col4_h: '20D return', col5_h: 'From 52W high',
      }},
      oversold: {{
        caption: "Funds flagged as oversold — RSI 14 ≤ 30 or sitting within a few percent of the 52-week low.",
        col4_h: 'RSI 14', col5_h: 'Above 52W low',
      }},
      stretched: {{
        caption: "Funds flagged as stretched — RSI 14 ≥ 70 or trading well above their 50-day moving average. Reversion risk is higher in this zone.",
        col4_h: 'RSI 14', col5_h: 'From 52W high',
      }},
      near_lows: {{
        caption: "Funds within 15% of their 52-week low, sorted by proximity. Useful for spotting the deepest pullbacks.",
        col4_h: 'RSI 14', col5_h: 'Above 52W low',
      }},
    }};

    function renderSignalBoard() {{
      const tickers = filteredTickers();
      const rows = [];
      for (const tk of tickers) {{
        const sig = RADAR.signals[tk]; const m = RADAR.metrics[tk];
        const f = RADAR.funds[tk]; const sl = RADAR.sig_lens[tk];
        if (!sig || !f) continue;
        rows.push({{ tk, sig, m, f, sl }});
      }}
      let ranked = [];
      const tab = activeSignalTab;
      if (tab === "leaders") {{
        ranked = rows.filter(r => r.m && r.m.ytd != null)
          .sort((a, b) => b.m.ytd - a.m.ytd);
      }} else if (tab === "laggards") {{
        ranked = rows.filter(r => r.m && r.m.ytd != null)
          .sort((a, b) => a.m.ytd - b.m.ytd);
      }} else if (tab === "oversold") {{
        ranked = rows.filter(r => ["oversold", "deeply_oversold"].includes(r.sig.ob_os_label))
          .sort((a, b) => (a.sig.rsi_14 || 100) - (b.sig.rsi_14 || 100));
      }} else if (tab === "stretched") {{
        ranked = rows.filter(r => ["stretched", "very_stretched"].includes(r.sig.ob_os_label))
          .sort((a, b) => (b.sig.rsi_14 || 0) - (a.sig.rsi_14 || 0));
      }} else if (tab === "near_lows") {{
        ranked = rows.filter(r => r.sig.pct_above_52w_low != null && r.sig.pct_above_52w_low <= 15)
          .sort((a, b) => (a.sig.pct_above_52w_low || 999) - (b.sig.pct_above_52w_low || 999));
      }}
      const top = ranked.slice(0, 10);
      const meta = SIGNAL_TAB_META[tab];

      // Caption
      document.getElementById('signal-caption').textContent = meta.caption;

      // Headers — proper <th> cells that match the row layout
      const col3_h = 'YTD return';
      document.getElementById('signal-thead').innerHTML = `
        <th>Ticker</th>
        <th>Target</th>
        <th class="num">${{col3_h}}</th>
        <th class="num">${{meta.col4_h}}</th>
        <th class="num">${{meta.col5_h}}</th>
        <th>Trend / Reason</th>
      `;

      function rowHtml(r) {{
        const targetTxt = (r.f.target || '').replace(/</g, '&lt;');
        let col3 = '', col4 = '—', col5 = '—', trendReason = '';

        const ytd = r.m && r.m.ytd;
        const ytdCls = ytd != null ? (ytd >= 0 ? 'pos' : 'neg') : '';
        col3 = `<td class="num ${{ytdCls}}">${{fmtPct(ytd)}}</td>`;
        if (tab === 'leaders' || tab === 'laggards') {{
          const ret20 = r.sig.return_20d;
          const ret20Cls = ret20 != null ? (ret20 >= 0 ? 'pos' : 'neg') : '';
          col4 = `<td class="num ${{ret20Cls}}">${{fmtPct(ret20)}}</td>`;
          if (tab === 'leaders') {{
            col5 = `<td class="num">${{r.sig.rsi_14 != null ? r.sig.rsi_14.toFixed(0) : '—'}}</td>`;
          }} else {{
            const dd = r.sig.drawdown_52w;
            const ddCls = dd != null && dd < 0 ? 'neg' : '';
            col5 = `<td class="num ${{ddCls}}">${{fmtPct(dd)}}</td>`;
          }}
        }} else {{
          col4 = `<td class="num">${{r.sig.rsi_14 != null ? r.sig.rsi_14.toFixed(0) : '—'}}</td>`;
          if (tab === 'near_lows' || tab === 'oversold') {{
            col5 = `<td class="num">${{r.sig.pct_above_52w_low != null ? '+' + r.sig.pct_above_52w_low.toFixed(0) + '%' : '—'}}</td>`;
          }} else {{
            const dd = r.sig.drawdown_52w;
            const ddCls = dd != null && dd < 0 ? 'neg' : '';
            col5 = `<td class="num ${{ddCls}}">${{fmtPct(dd)}}</td>`;
          }}
        }}

        let reasons = [];
        if (tab === 'leaders') reasons = r.sig.reasons_popping || [];
        else if (tab === 'laggards') {{
          if (r.sig.drawdown_52w != null) reasons.push(r.sig.drawdown_52w.toFixed(0) + '% from 52W high');
          if (r.sig.reasons_near_lows) reasons.push(...r.sig.reasons_near_lows);
          if (r.sig.trend_regime === 'downtrend') reasons.push('downtrend');
        }}
        else if (tab === 'oversold') reasons = r.sig.reasons_oversold || [];
        else if (tab === 'stretched') reasons = r.sig.reasons_stretched || [];
        else if (tab === 'near_lows') reasons = r.sig.reasons_near_lows || [];
        const reasonsText = reasons.slice(0, 2).join(' · ');
        trendReason = `${{regimeChip(r.sig.trend_regime)}}${{reasonsText ? ' &middot; ' + reasonsText : ''}}`;

        return `<tr>
          <td class="tk">${{r.tk}}</td>
          <td class="tgt">${{targetTxt}}</td>
          ${{col3}}${{col4}}${{col5}}
          <td class="reason">${{trendReason}}</td>
        </tr>`;
      }}

      const body = top.length
        ? top.map(rowHtml).join('')
        : '<tr><td colspan="6" style="padding:14px;color:' + COLORS.text_light + ';font-style:italic;">No funds match this scan.</td></tr>';
      document.getElementById('signal-tbody').innerHTML = body;
    }}

    // -----------------------------------------------------------
    // Trend vs Stretch scatter — outlier-only labels
    // -----------------------------------------------------------
    const REGIME_COLORS = {{
      strong_uptrend: COLORS.accent,
      uptrend_pullback: COLORS.cat_2,
      rebound_attempt: COLORS.cat_4,
      downtrend: COLORS.red,
      neutral: COLORS.muted,
      insufficient_data: COLORS.muted,
    }};

    function renderTrendStretch() {{
      const tickers = filteredTickers();
      const rows = [];
      for (const tk of tickers) {{
        const sig = RADAR.signals[tk];
        if (!sig || sig.dist_from_ma_200 == null || sig.rsi_14 == null) continue;
        rows.push({{
          tk, x: sig.dist_from_ma_200, y: sig.rsi_14,
          regime: sig.trend_regime,
          vol: (RADAR.metrics[tk] && RADAR.metrics[tk].vol) || 30,
        }});
      }}
      // Outlier selection: top 5 most stretched (high x) + top 5 most oversold (low y or low x)
      const labelSet = new Set();
      const byStretch = [...rows].sort((a, b) => b.x - a.x).slice(0, 5);
      const byOversold = [...rows].sort((a, b) => a.y - b.y).slice(0, 5);
      byStretch.forEach(r => labelSet.add(r.tk));
      byOversold.forEach(r => labelSet.add(r.tk));
      if (rows.some(r => r.tk === 'TQQQ')) labelSet.add('TQQQ');

      // Group by regime
      const byRegime = new Map();
      for (const r of rows) {{
        if (!byRegime.has(r.regime)) byRegime.set(r.regime, []);
        byRegime.get(r.regime).push(r);
      }}
      const traces = [];
      for (const [regime, rs] of byRegime.entries()) {{
        traces.push({{
          x: rs.map(r => r.x), y: rs.map(r => r.y),
          mode: 'markers+text',
          text: rs.map(r => labelSet.has(r.tk) ? r.tk : ''),
          textposition: 'top center',
          textfont: {{ size: 9, color: COLORS.text_muted, family: FONT_SANS }},
          name: TREND_REGIME_LABELS[regime] || regime,
          marker: {{
            color: REGIME_COLORS[regime] || COLORS.muted,
            size: rs.map(r => Math.max(6, Math.min(16, r.vol / 6))),
            line: {{ color: 'white', width: 1 }},
            opacity: 0.85,
          }},
          hovertemplate: '<b>%{{text}}</b><extra></extra>',
          customdata: rs.map(r => r.tk),
        }});
      }}
      const layout = editorialLayout({{
        margin: {{ l: 60, r: 30, t: 14, b: 50 }},
        xaxis: {{
          gridcolor: COLORS.grid, gridwidth: 0.5, zeroline: false, fixedrange: true,
          showline: true, linecolor: COLORS.axis, linewidth: 1,
          ticks: 'outside', ticklen: 4, tickcolor: COLORS.axis,
          tickfont: {{ color: COLORS.text_light, size: 11 }},
          title: {{ text: 'Distance from 200d MA (%)', font: {{ size: 11, color: COLORS.text_light }}, standoff: 10 }},
          ticksuffix: '%',
        }},
        yaxis: {{
          gridcolor: COLORS.grid, gridwidth: 0.5, fixedrange: true,
          showline: false, ticks: '',
          tickfont: {{ color: COLORS.text_light, size: 11 }},
          title: {{ text: 'RSI 14', font: {{ size: 11, color: COLORS.text_light }}, standoff: 10 }},
          range: [0, 100],
        }},
        shapes: [
          {{ type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: 50, y1: 50,
             line: {{ color: COLORS.text_light, dash: 'dot', width: 1 }} }},
          {{ type: 'line', xref: 'x', x0: 0, x1: 0, yref: 'paper', y0: 0, y1: 1,
             line: {{ color: COLORS.text_light, dash: 'dot', width: 1 }} }},
        ],
      }});
      Plotly.newPlot('trend-stretch-chart', traces, layout,
        {{ displayModeBar: false, scrollZoom: false, responsive: true, doubleClick: false }});
    }}

    // -----------------------------------------------------------
    // Drawdown Map — three small ranked lists
    // -----------------------------------------------------------
    function renderDrawdownMap() {{
      const tickers = filteredTickers();
      const rows = [];
      for (const tk of tickers) {{
        const sig = RADAR.signals[tk]; const f = RADAR.funds[tk];
        if (!sig || !f) continue;
        rows.push({{ tk, sig, f, target: f.target }});
      }}
      const nearLow = [...rows]
        .filter(r => r.sig.pct_above_52w_low != null)
        .sort((a, b) => a.sig.pct_above_52w_low - b.sig.pct_above_52w_low)
        .slice(0, 8);
      const fromAth = [...rows]
        .filter(r => r.sig.pct_from_ath != null)
        .sort((a, b) => a.sig.pct_from_ath - b.sig.pct_from_ath)
        .slice(0, 8);
      const newHighs = rows
        .filter(r => r.sig.new_high_252d)
        .sort((a, b) => (b.sig.return_20d || 0) - (a.sig.return_20d || 0))
        .slice(0, 8);

      function row(label, tk, v, vClass) {{
        return `<div class="row">
          <span class="tk">${{tk}}</span>
          <span class="label">${{label}}</span>
          <span class="v ${{vClass}}">${{v}}</span>
        </div>`;
      }}
      function emptyOr(rs, formatter) {{
        if (!rs.length) return '<div style="font-size:11px;color:' + COLORS.text_light + ';font-style:italic;padding:6px;">No funds match.</div>';
        return rs.map(formatter).join('');
      }}

      document.getElementById('dd-near-low').innerHTML = emptyOr(nearLow, r => {{
        const flag = r.sig.new_low_252d ? 'new 1Y low' : r.sig.new_low_65d ? 'new 65d low' : '+near 52W low';
        return row(flag, r.tk, '+' + r.sig.pct_above_52w_low.toFixed(0) + '%', 'neg');
      }});
      document.getElementById('dd-from-ath').innerHTML = emptyOr(fromAth, r =>
        row('from ATH', r.tk, r.sig.pct_from_ath.toFixed(0) + '%', 'neg'),
      );
      document.getElementById('dd-new-highs').innerHTML = emptyOr(newHighs, r => {{
        const flag = r.sig.new_high_252d ? 'new 1Y high' : 'new 65d high';
        const ret = r.sig.return_20d != null ? '+' + r.sig.return_20d.toFixed(0) + '%' : '—';
        return row(flag, r.tk, ret, 'pos');
      }});
    }}

    // -----------------------------------------------------------
    // Summary rank lists — broad scanner, not Sig-driven
    // -----------------------------------------------------------
    function renderSignalLists() {{
      const tickers = filteredTickers();
      const rows = [];
      for (const tk of tickers) {{
        const sig = RADAR.signals[tk]; const f = RADAR.funds[tk]; const m = RADAR.metrics[tk];
        if (!sig || !f || !m) continue;
        rows.push({{ tk, sig, f, m }});
      }}
      const leaders = [...rows]
        .filter(r => r.m.ytd != null)
        .sort((a, b) => b.m.ytd - a.m.ytd)
        .slice(0, 10);
      const stretched = [...rows]
        .filter(r => ["stretched", "very_stretched"].includes(r.sig.ob_os_label))
        .sort((a, b) => (b.sig.rsi_14 || 0) - (a.sig.rsi_14 || 0))
        .slice(0, 10);
      const weak = [...rows]
        .filter(r => r.m.ytd != null)
        .sort((a, b) => a.m.ytd - b.m.ytd)
        .slice(0, 10);

      function row(label, tk, v, vClass) {{
        return `<div class="row">
          <span class="tk">${{tk}}</span>
          <span class="label">${{label}}</span>
          <span class="v ${{vClass}}">${{v}}</span>
        </div>`;
      }}
      function emptyOr(rs, formatter) {{
        if (!rs.length) return '<div style="font-size:11px;color:' + COLORS.text_light + ';font-style:italic;padding:6px;">No funds match.</div>';
        return rs.map(formatter).join('');
      }}

      document.getElementById('rank-leaders').innerHTML = emptyOr(leaders, r => {{
        const label = r.sig.return_20d != null ? '20D ' + fmtPct(r.sig.return_20d, 0) : r.f.target || '';
        return row(label, r.tk, fmtPct(r.m.ytd), 'pos');
      }});
      document.getElementById('rank-stretched').innerHTML = emptyOr(stretched, r => {{
        const label = r.sig.dist_from_ma_50 != null ? '+' + r.sig.dist_from_ma_50.toFixed(0) + '% vs 50d' : 'stretched';
        const value = r.sig.rsi_14 != null ? 'RSI ' + r.sig.rsi_14.toFixed(0) : fmtPct(r.m.ytd);
        return row(label, r.tk, value, 'pos');
      }});
      document.getElementById('rank-weak').innerHTML = emptyOr(weak, r => {{
        const label = r.sig.pct_above_52w_low != null ? '+' + r.sig.pct_above_52w_low.toFixed(0) + '% above 52W low' : r.f.target || '';
        return row(label, r.tk, fmtPct(r.m.ytd), 'neg');
      }});
    }}

    // -----------------------------------------------------------
    // Compact TQQQ Kelly 9Sig watch cards
    // -----------------------------------------------------------
    function renderTqqqWatch() {{
      const tickers = filteredTickers();
      // TQQQ cards (only show if TQQQ is filtered in)
      const tqqq = RADAR.sig_lens['TQQQ'];
      const tqqqInFilter = tickers.includes('TQQQ');
      const cardsHost = document.getElementById('tqqq-watch-cards');
      if (tqqq && tqqq.is_canonical_9sig && tqqqInFilter) {{
        const qtd = tqqq.qtd_return_pct;
        const gap = tqqq.signal_gap_pct;
        const qtdCls = qtd != null && qtd >= 0 ? 'pos' : 'neg';
        const gapCls = gap != null && gap >= 0 ? 'pos' : 'neg';
        const tdActive = tqqq.tqqq_30down_active;
        const tdDD = tqqq.tqqq_30down_drawdown_pct;
        const spike = tqqq.tqqq_spike_distance_pct;
        cardsHost.innerHTML = `
          <div class="sig-tqqq-card">
            <div class="v ${{qtdCls}}">${{fmtPct(qtd)}}</div>
            <div class="l">TQQQ QTD</div>
            <div class="sub">vs +9% target</div>
          </div>
          <div class="sig-tqqq-card">
            <div class="v ${{gapCls}}">${{fmtPct(gap, 1)}}</div>
            <div class="l">9Sig gap</div>
            <div class="sub">QTD − 9% target</div>
          </div>
          <div class="sig-tqqq-card ${{tdActive ? 'flagged' : ''}}">
            <div class="v" style="font-size:18px;">${{tdActive ? 'ACTIVE' : 'Inactive'}}</div>
            <div class="l">30-down rule</div>
            <div class="sub">rolling 2yr DD ${{tdDD != null ? tdDD.toFixed(0) + '%' : '—'}}</div>
          </div>
          <div class="sig-tqqq-card">
            <div class="v" style="font-size:18px;">${{spike != null ? spike.toFixed(0) + '%' : '—'}}</div>
            <div class="l">Spike-watch</div>
            <div class="sub">distance to +100% quarter</div>
          </div>
        `;
      }} else {{
        cardsHost.innerHTML = '<div style="font-size:12px;color:' + COLORS.text_light + ';font-style:italic;padding:6px 0;">TQQQ status cards appear when TQQQ is in the filtered set.</div>';
      }}
    }}

    // -----------------------------------------------------------
    // Apply filters → KPI + 3 charts + screener row visibility
    // -----------------------------------------------------------
    function applyFilters() {{
      let visible = 0;
      document.querySelectorAll('.screener-row').forEach(r => {{
        let show = true;
        for (const sel of filterState.values()) {{
          if (sel === '*') continue;
          const alts = sel.split(',').map(s => s.trim());
          if (!alts.some(a => r.matches(a))) {{ show = false; break; }}
        }}
        r.style.display = show ? '' : 'none';
        // Also hide the matching detail-row if hidden
        const tk = r.getAttribute('data-ticker');
        const detail = document.getElementById('detail-' + tk);
        if (detail && (!show || !r.classList.contains('expanded'))) {{
          detail.hidden = !show ? true : !r.classList.contains('expanded');
        }}
        if (show) visible++;
      }});
      document.getElementById('filter-count').textContent = visible;
      renderKPIStrip();
      renderPerformance();
      renderMoversTables();
      renderRiskReturn();
      renderSignalBoard();
      renderTrendStretch();
      renderDrawdownMap();
      renderSignalLists();
      renderTqqqWatch();
    }}

    // Wire signal-tab clicks
    document.querySelectorAll('.signal-tab').forEach(btn => {{
      btn.addEventListener('click', () => {{
        document.querySelectorAll('.signal-tab').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        activeSignalTab = btn.dataset.stab;
        renderSignalBoard();
      }});
    }});

    // Wire filter buttons
    document.querySelectorAll('.filter-group').forEach(g => {{
      g.querySelectorAll('.filter-btn').forEach(btn => {{
        btn.addEventListener('click', () => {{
          g.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          filterState.set(g.dataset.group, btn.dataset.filter);
          applyFilters();
        }});
      }});
    }});

    // Wire timeframe pills — single source of truth driving multiple widgets
    document.querySelectorAll('.timeframe-btn').forEach(btn => {{
      btn.addEventListener('click', () => {{
        document.querySelectorAll('.timeframe-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        activeTimeframe = btn.dataset.tf;
        renderKPIStrip();
        renderPerformance();
        renderMoversTables();
        renderRiskReturn();
      }});
    }});

    // -----------------------------------------------------------
    // Click-to-expand detail panel
    // -----------------------------------------------------------
    const CONFIDENCE_COLORS = {{
      issuer_native_actual_holdings: COLORS.cat_5,
      issuer_native_index_components: COLORS.cat_2,
      proxy_etf_holdings: COLORS.cat_4,
      manual_target_only: COLORS.text_light,
    }};
    const CONFIDENCE_LABELS = {{
      issuer_native_actual_holdings: 'Issuer · actual holdings',
      issuer_native_index_components: 'Issuer · index components',
      proxy_etf_holdings: 'Proxy ETF holdings',
      manual_target_only: 'Target name only',
    }};

    function buildDetailPanel(tk) {{
      const f = RADAR.funds[tk] || {{}};
      const m = RADAR.metrics[tk] || {{}};
      const h = RADAR.holdings[tk] || {{}};

      // Coverage block
      const inception = f.inception_date ? f.inception_date.slice(0, 10) : '—';
      const exp = f.expense_ratio != null ? (f.expense_ratio * 100).toFixed(2) + '%' : '—';
      const proxy = f.proxy_symbol || '—';
      const dirLev = (f.direction || '?') + ' &middot; ' + (f.leverage != null ? (f.leverage > 0 ? '+' : '') + f.leverage + '×' : '?');
      const noteHtml = f.notes
        ? `<p style="margin-top:6px;font-size:12px;color:${{COLORS.text_muted}};font-style:italic;">${{f.notes}}</p>`
        : '';
      const coverage = `
        <div class="detail-coverage">
          <div class="detail-block-title">Coverage</div>
          <h4>${{f.target || tk}}</h4>
          <p>${{tk}} delivers ${{dirLev}} daily exposure to <b>${{f.target || 'its target'}}</b>${{f.exposure ? ' — a ' + f.exposure.replace('_', ' ') + ' fund' : ''}}.
             Issuer ${{f.issuer || '—'}} ${{f.product_type ? '(' + f.product_type + ')' : ''}}.</p>
          <div class="meta-line">
            Proxy ${{proxy}} &middot; Inception ${{inception}} &middot; ER ${{exp}}
          </div>
          ${{noteHtml}}
        </div>`;

      // Holdings block
      const conf = h.confidence || 'manual_target_only';
      const confColor = CONFIDENCE_COLORS[conf] || COLORS.text_light;
      const confLabel = CONFIDENCE_LABELS[conf] || conf;
      const sourceLink = h.source_url
        ? ` &middot; <a href="${{h.source_url}}" target="_blank" rel="noopener" class="source-link">source</a>`
        : '';
      const compRows = (h.components || []).map(c => `
        <div class="row">
          <span class="kind">${{(c.kind || '').slice(0,8)}}</span>
          <span class="name">${{c.name}}</span>
          <span class="weight">${{c.weight != null ? c.weight.toFixed(1) + '%' : '—'}}</span>
        </div>`).join('') || '<div style="font-size:12px;color:' + COLORS.text_light + ';">No holdings parsed.</div>';
      const holdings = `
        <div class="detail-holdings">
          <div class="detail-block-title">Top holdings</div>
          <div class="detail-confidence" style="background:${{confColor}}1a;color:${{confColor}};">
            ${{confLabel}}${{sourceLink}}
          </div>
          <div style="margin-top:10px;">${{compRows}}</div>
        </div>`;

      // KPI grid
      function kpi(v, label, fmtFn) {{
        const val = fmtFn(v);
        const cls = (v != null && !isNaN(v)) ? (v >= 0 ? 'pos' : 'neg') : '';
        return `<div class="detail-kpi"><div class="v ${{cls}}">${{val}}</div><div class="l">${{label}}</div></div>`;
      }}
      const noClass = (v, label, fmtFn) =>
        `<div class="detail-kpi"><div class="v">${{fmtFn(v)}}</div><div class="l">${{label}}</div></div>`;
      const kpis = `
        <div class="detail-perf">
          <div class="detail-block-title">Performance &amp; risk</div>
          <div class="detail-kpis">
            ${{noClass(m['price'], 'Last close', fmtPrice)}}
            ${{kpi(m['1m'], '1M return', fmtPct)}}
            ${{kpi(m['3m'], '3M return', fmtPct)}}
            ${{kpi(m['ytd'], 'YTD return', fmtPct)}}
            ${{kpi(m['1y'], '1Y return', fmtPct)}}
            ${{kpi(m['3y'], '3Y return', fmtPct)}}
            ${{kpi(m['5y'], '5Y return', fmtPct)}}
            ${{kpi(m['10y'], '10Y return', fmtPct)}}
            ${{kpi(m['dd'], 'Max DD 1Y', fmtPct)}}
            ${{noClass(m['vol'], 'Vol (ann %)', v => v != null ? v.toFixed(0) + '%' : '—')}}
            ${{noClass(m['beta'], 'β 60d', v => v != null ? v.toFixed(2) : '—')}}
            ${{kpi(m['gap'], 'Tracking gap', fmtPct)}}
            ${{noClass(m['addv'], 'Avg daily $ vol', fmtMoney)}}
          </div>
        </div>`;

      // Signals block — trend regime, RSI, MA distances, 52W proximity, signal flags
      const sig = RADAR.signals[tk] || {{}};
      const sl = RADAR.sig_lens[tk] || {{}};
      const flagChips = [];
      if (['oversold', 'deeply_oversold', 'stretched', 'very_stretched'].includes(sig.ob_os_label)) {{
        flagChips.push(`<span class="flag ${{sig.ob_os_label}}">${{sig.ob_os_label.replace('_', ' ')}}</span>`);
      }}
      if (sig.reversion_label === 'washed_out_stabilizing') flagChips.push('<span class="flag washed_out">washed-out stabilizing</span>');
      if (sig.reversion_label === 'falling_knife') flagChips.push('<span class="flag falling_knife">falling knife</span>');
      if (sig.vol_pressure_label === 'vol_expanding') flagChips.push('<span class="flag vol_expanding">vol expanding</span>');
      if (sig.new_high_252d) flagChips.push('<span class="flag new_high">new 1Y high</span>');
      else if (sig.new_high_65d) flagChips.push('<span class="flag new_high">new 65d high</span>');
      if (sig.new_low_252d) flagChips.push('<span class="flag new_low">new 1Y low</span>');
      else if (sig.new_low_65d) flagChips.push('<span class="flag new_low">new 65d low</span>');
      if (sig.tracking_weirdness) flagChips.push('<span class="flag tracking_weirdness">tracking gap large</span>');
      const flagsHtml = flagChips.length
        ? `<div class="signal-flags">${{flagChips.join('')}}</div>`
        : '';

      function sigRow(label, value) {{
        return `<div class="sig-row"><span class="l">${{label}}</span><span class="v">${{value}}</span></div>`;
      }}
      const fmtRsi = sig.rsi_14 != null ? sig.rsi_14.toFixed(0) : '—';
      const sigLensRow = tk === 'TQQQ' && sl.eligible && sl.signal_gap_pct != null
        ? sigRow(`TQQQ 9Sig watch`,
                 `${{fmtPct(sl.qtd_return_pct)}} (gap ${{fmtPct(sl.signal_gap_pct, 1)}})`)
        : '';

      const signalsBlock = `
        <div class="detail-signals">
          <div class="detail-block-title">Signals</div>
          ${{flagsHtml}}
          ${{sigRow('Trend regime', regimeChip(sig.trend_regime || 'insufficient_data'))}}
          ${{sigRow('RSI 14', fmtRsi)}}
          ${{sigRow('Dist from 20d / 50d / 200d',
                    `${{fmtPct(sig.dist_from_ma_20)}} / ${{fmtPct(sig.dist_from_ma_50)}} / ${{fmtPct(sig.dist_from_ma_200)}}`)}}
          ${{sigRow('From 52W high / above 52W low',
                    `${{fmtPct(sig.drawdown_52w)}} / +${{sig.pct_above_52w_low != null ? sig.pct_above_52w_low.toFixed(0) + '%' : '—'}}`)}}
          ${{sigRow('From ATH', fmtPct(sig.pct_from_ath))}}
          ${{sigLensRow}}
        </div>`;

      const priceChartBlock = `
        <div class="mini-chart-block">
          <div class="detail-block-title">Price history</div>
          <div class="mini-tabs" data-tk="${{tk}}">
            ${{['1D','1W','1M','3M','6M','YTD','1Y','2Y','5Y','10Y','ALL']
              .map(function(tf) {{
                return '<button class="mini-tab' + (tf==='1Y'?' active':'') + '" data-tf="' + tf + '">' + tf + '</button>';
              }}).join('')}}
          </div>
          <div id="mini-chart-${{tk}}" class="mini-chart-canvas"></div>
        </div>`;

      return coverage + holdings + kpis + priceChartBlock + signalsBlock;
    }}

    function renderPriceChart(tk, tf) {{
      const target = document.getElementById('mini-chart-' + tk);
      const m = RADAR.metrics[tk] || {{}};
      const ts = m.hist;
      if (!target) return;
      if (!ts || !ts.d || !ts.d.length) {{
        target.innerHTML = '<p style="font-size:12px;color:' + COLORS.text_muted + ';">No price history available.</p>';
        return;
      }}
      const epoch = new Date(ts.e + 'T00:00:00');
      const dates = ts.d.map(function(d) {{ return new Date(epoch.getTime() + d * 86400000); }});
      const prices = ts.p;
      const now = new Date();
      let cutoff = null;
      const days = (n) => new Date(now.getTime() - n * 86400000);
      switch(tf) {{
        case '1D':  cutoff = days(2); break;
        case '1W':  cutoff = days(8); break;
        case '1M':  cutoff = days(31); break;
        case '3M':  cutoff = days(92); break;
        case '6M':  cutoff = days(183); break;
        case 'YTD': cutoff = new Date(now.getFullYear(), 0, 1); break;
        case '1Y':  cutoff = days(366); break;
        case '2Y':  cutoff = days(2*366); break;
        case '5Y':  cutoff = days(5*366); break;
        case '10Y': cutoff = days(10*366); break;
        case 'ALL': cutoff = null; break;
      }}
      let i0 = 0;
      if (cutoff) {{
        i0 = dates.findIndex(function(d) {{ return d >= cutoff; }});
        if (i0 < 0) i0 = dates.length - 1;
      }}
      const x = dates.slice(i0);
      const y = prices.slice(i0);
      if (x.length < 2) {{
        target.innerHTML = '<p style="font-size:12px;color:' + COLORS.text_muted + ';">Not enough data for this window.</p>';
        return;
      }}
      const start = y[0], end = y[y.length-1];
      const ret = ((end / start) - 1) * 100;
      const lineColor = ret >= 0 ? COLORS.green : COLORS.red;
      // Plotly does NOT accept 8-digit hex — convert to rgba for fill (memory rule)
      const hexParts = lineColor.replace('#','').match(/.{{2}}/g) || ['16','a3','4a'];
      const fillColor = 'rgba(' + parseInt(hexParts[0],16) + ',' + parseInt(hexParts[1],16) + ',' + parseInt(hexParts[2],16) + ',0.08)';
      Plotly.newPlot(target, [{{
        x: x, y: y, type: 'scatter', mode: 'lines',
        line: {{ color: lineColor, width: 1.8 }},
        fill: 'tozeroy', fillcolor: fillColor,
        hovertemplate: '%{{x|%b %-d, %Y}}: $%{{y:,.2f}}<extra></extra>',
      }}], {{
        margin: {{ l: 48, r: 8, t: 4, b: 28 }},
        height: 180,
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        xaxis: {{ showgrid: false, color: COLORS.text_muted, tickfont: {{ size: 10 }} }},
        yaxis: {{ gridcolor: COLORS.grid, color: COLORS.text_muted, tickfont: {{ size: 10 }}, tickprefix: '$', automargin: true }},
        showlegend: false,
        annotations: [{{
          xref: 'paper', yref: 'paper', x: 1, y: 1, xanchor: 'right', yanchor: 'top',
          text: '<b>' + (ret >= 0 ? '+' : '') + ret.toFixed(1) + '%</b> over ' + tf,
          showarrow: false, font: {{ size: 11, color: lineColor }},
        }}]
      }}, {{ displayModeBar: false, responsive: true }});
    }}

    document.querySelectorAll('tr.screener-row').forEach(row => {{
      row.addEventListener('click', (e) => {{
        // Don't toggle if user clicked a link inside the row
        if (e.target.closest('a')) return;
        const tk = row.getAttribute('data-ticker');
        const detail = document.getElementById('detail-' + tk);
        const panel = document.getElementById('detail-panel-' + tk);
        if (!detail || !panel) return;
        const isOpen = !detail.hidden;
        if (!isOpen) {{
          panel.innerHTML = buildDetailPanel(tk);
          detail.hidden = false;
          row.classList.add('expanded');
          // Render mini price chart with default 1Y window
          renderPriceChart(tk, '1Y');
          // Wire timeframe tabs
          panel.querySelectorAll('.mini-tabs .mini-tab').forEach(function(btn) {{
            btn.addEventListener('click', function(ev) {{
              ev.stopPropagation();
              panel.querySelectorAll('.mini-tabs .mini-tab').forEach(function(b) {{ b.classList.remove('active'); }});
              btn.classList.add('active');
              renderPriceChart(tk, btn.dataset.tf);
            }});
          }});
        }} else {{
          detail.hidden = true;
          row.classList.remove('expanded');
        }}
      }});
    }});

    // -----------------------------------------------------------
    // Click-to-sort screener headers
    // -----------------------------------------------------------
    const tbody = document.querySelector('#screener tbody');
    document.querySelectorAll('#screener thead th').forEach((th, i) => {{
      let asc = true;
      th.addEventListener('click', () => {{
        // Only sort screener-row pairs (with their detail-row siblings)
        const allTrs = Array.from(tbody.querySelectorAll('tr.screener-row'));
        const detailMap = new Map();
        allTrs.forEach(tr => {{
          const dr = document.getElementById('detail-' + tr.getAttribute('data-ticker'));
          if (dr) detailMap.set(tr, dr);
        }});
        allTrs.sort((a, b) => {{
          const aT = a.children[i].innerText.replace(/[+%×—pp▸$,]/g, '').trim();
          const bT = b.children[i].innerText.replace(/[+%×—pp▸$,]/g, '').trim();
          const aN = parseFloat(aT), bN = parseFloat(bT);
          const isNum = !isNaN(aN) && !isNaN(bN);
          if (isNum) return asc ? aN - bN : bN - aN;
          return asc ? aT.localeCompare(bT) : bT.localeCompare(aT);
        }});
        allTrs.forEach(tr => {{
          tbody.appendChild(tr);
          const dr = detailMap.get(tr);
          if (dr) tbody.appendChild(dr);
        }});
        asc = !asc;
      }});
    }});

    wireNineSigHistoryControls();
    renderNineSigHistory();
    applyFilters();
  }})();
  </script>
</body>
</html>"""
# fmt: on


def generate_html(
    funds: list[Fund],
    metrics: dict[str, FundMetrics],
    fund_data: dict[str, pd.Series],
    proxy_data: dict[str, pd.Series],   # noqa: ARG001 — kept for interface stability
    holdings: dict[str, Holdings],
    summary_html: str = "",
    representative_tickers: list[str] | None = None,  # noqa: ARG001
    signals: dict[str, FundSignals] | None = None,
    sig_lenses: dict[str, SigLens] | None = None,
    nine_sig_panel: str = "",
    nine_sig_adjusted_data: dict[str, pd.Series] | None = None,
) -> str:
    radar_json = build_radar_json(
        funds,
        metrics,
        fund_data,
        holdings,
        signals,
        sig_lenses,
        nine_sig_adjusted_data,
    )
    colors_json = json.dumps({k: v for k, v in COLORS.items()})

    return HTML_TEMPLATE.format(
        bg=COLORS["bg"],
        text=COLORS["text"],
        text_muted=COLORS["text_muted"],
        text_light=COLORS["text_light"],
        border=COLORS["border"],
        axis=COLORS["axis"],
        card_bg=COLORS["card_bg"],
        panel_bg=COLORS["panel_bg"],
        accent=COLORS["accent"],
        cat_2=COLORS["cat_2"],
        cat_3=COLORS["cat_3"],
        cat_5=COLORS["cat_5"],
        green=COLORS["green"],
        red=COLORS["red"],
        highlight=COLORS["highlight"],
        timestamp=datetime.now().strftime("%b %d, %Y at %I:%M %p"),
        total_funds=len(funds),
        filter_bar=build_filter_bar(len(funds)),
        nine_sig_panel=nine_sig_panel,
        summary_html=summary_html,
        screener=build_screener_table(funds, metrics),
        radar_json=radar_json,
        colors_json=colors_json,
    )
