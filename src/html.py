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
            <td class="num">{_fmt_pct(m.return_ytd)}</td>
            <td class="num">{_fmt_pct(m.return_1y)}</td>
            <td class="num">{_fmt_pct(m.return_5y)}</td>
            <td class="num">{_fmt_num(m.realized_vol_1y, 0)}</td>
            <td class="num">{_fmt_num(m.realized_beta_60d)}</td>
          </tr>
          <tr class="detail-row" id="detail-{f.ticker}" hidden>
            <td colspan="9"><div class="detail-panel" id="detail-panel-{f.ticker}"></div></td>
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

    payload = {
        "timestamps": timestamps_ms,
        "funds": funds_obj,
        "metrics": metrics_obj,
        "prices": prices,
        "holdings": holdings_obj,
        "signals": signals_obj,
        "sig_lens": sig_lens_obj,
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
          <button class="filter-btn active" data-filter="*">All</button>
          <button class="filter-btn" data-filter=".dir-long">Long</button>
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
      display: grid; grid-template-columns: 1.2fr 1fr 1.2fr 1.2fr; gap: 22px;
      padding: 18px 24px; max-width: 100%;
    }}
    @media (max-width: 1200px) {{
      .detail-panel {{ grid-template-columns: 1fr 1fr; }}
    }}
    @media (max-width: 700px) {{
      .detail-panel {{ grid-template-columns: 1fr; }}
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

    {nine_sig_panel}

    {filter_bar}

    <div class="kpi-strip" id="kpi-strip"></div>

    {summary_html}

    <div class="section">
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

    <div class="section">
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

    <div class="section">
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

    <div class="section">
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

    <div class="section">
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

    <div class="section">
      <div class="chart-title-row">
        <span class="accent-bar"></span>
        <h2 class="chart-title">The screener</h2>
        <p class="chart-subtitle">All filtered funds. Click a row to expand its top holdings, full KPI grid, and source confidence. Click a column header to sort.</p>
      </div>
      {screener}
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
    document.querySelectorAll('.filter-group').forEach(g => filterState.set(g.dataset.group, '*'));

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
    function median(arr) {{
      const filt = arr.filter(v => v !== null && v !== undefined && !isNaN(v)).sort((a, b) => a - b);
      if (!filt.length) return null;
      const mid = Math.floor(filt.length / 2);
      return filt.length % 2 ? filt[mid] : (filt[mid-1] + filt[mid]) / 2;
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
        <div>
          <div class="detail-block-title">Performance &amp; risk</div>
          <div class="detail-kpis">
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

      return coverage + holdings + kpis + signalsBlock;
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
          const aT = a.children[i].innerText.replace(/[+%×—pp▸]/g, '').trim();
          const bT = b.children[i].innerText.replace(/[+%×—pp▸]/g, '').trim();
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
) -> str:
    radar_json = build_radar_json(funds, metrics, fund_data, holdings, signals, sig_lenses)
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
