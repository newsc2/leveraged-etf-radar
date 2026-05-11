"""LLM market summary for the LETF Radar.

Calls Gemini Flash with a strictly factual prompt. Frames movers, signal
clusters (leaders / laggards / oversold / stretched), optional TQQQ 9Sig
watch context, and tracking anomalies.
Refuses predictions, "investors should…", or trade orders.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from src.config import COLORS, GOOGLE_AI_API_KEY
from src.metrics import FundMetrics
from src.sig_lens import SigLens
from src.signals import FundSignals
from src.universe import Fund

logger: logging.Logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def build_summary_prompt(
    metrics: dict[str, FundMetrics],
    funds_by_ticker: dict[str, Fund],
    signals: dict[str, FundSignals] | None = None,
    sig_lenses: dict[str, SigLens] | None = None,
) -> str:
    """Compose a focused, factual prompt from the metrics + signal tables."""
    signals = signals or {}
    sig_lenses = sig_lenses or {}

    # --- Movers (YTD top/bottom) ---
    rows = []
    for tk, m in metrics.items():
        f = funds_by_ticker.get(tk)
        if f is None or m.return_ytd is None:
            continue
        rows.append({
            "ticker": tk, "leverage": f.leverage,
            "exposure": f.exposure_type, "target": f.target_name,
            "ytd": m.return_ytd, "drawdown_1y": m.max_drawdown_1y,
            "vol_1y": m.realized_vol_1y,
            "tracking_gap": m.actual_minus_simulated_1y,
        })
    rows.sort(key=lambda r: float(r["ytd"]) if r["ytd"] is not None else 0.0)
    bottom = rows[:5]
    top = rows[-5:][::-1]

    rows_gap = sorted(
        [r for r in rows if r["tracking_gap"] is not None],
        key=lambda r: r["tracking_gap"] or 0,
    )
    worst_gap = rows_gap[:5]

    # --- Signal clusters ---
    popping: list[dict[str, Any]] = []
    oversold: list[dict[str, Any]] = []
    stretched: list[dict[str, Any]] = []
    for tk, s in signals.items():
        f = funds_by_ticker.get(tk)
        if f is None:
            continue
        base = {"ticker": tk, "leverage": f.leverage, "target": f.target_name}
        if s.popping_score is not None:
            popping.append({**base, "score": s.popping_score,
                            "reasons": ", ".join(s.reasons_popping[:3]) or "—"})
        if s.ob_os_label in ("oversold", "deeply_oversold"):
            oversold.append({**base, "rsi": s.rsi_14,
                             "dd": s.drawdown_52w,
                             "reasons": ", ".join(s.reasons_oversold[:3]) or "—"})
        if s.ob_os_label in ("stretched", "very_stretched"):
            stretched.append({**base, "rsi": s.rsi_14,
                              "dist50": s.dist_from_ma_50,
                              "reasons": ", ".join(s.reasons_stretched[:3]) or "—"})
    popping.sort(key=lambda r: r["score"], reverse=True)
    popping = popping[:6]
    oversold = oversold[:6]
    stretched = stretched[:6]

    # --- Optional TQQQ 9Sig watch context ---
    tqqq_lens: SigLens | None = sig_lenses.get("TQQQ")

    # --- Format helpers ---
    def _movers(rs: list[dict[str, Any]]) -> str:
        return "\n".join(
            f"- {r['ticker']} ({r['leverage']:+g}× {r['target']}): "
            f"YTD {r['ytd']:+.1f}%, vol {r['vol_1y']:.0f}% / DD {r['drawdown_1y']:.0f}%"
            for r in rs
        )

    def _popping(rs: list[dict[str, Any]]) -> str:
        return "\n".join(
            f"- {r['ticker']} ({r['leverage']:+g}× {r['target']}): score {r['score']:.1f} ({r['reasons']})"
            for r in rs
        )

    def _oversold(rs: list[dict[str, Any]]) -> str:
        return "\n".join(
            f"- {r['ticker']} ({r['leverage']:+g}× {r['target']}): RSI {r['rsi']:.0f}, "
            f"DD {r['dd']:.0f}% ({r['reasons']})"
            for r in rs
        )

    def _stretched(rs: list[dict[str, Any]]) -> str:
        return "\n".join(
            f"- {r['ticker']} ({r['leverage']:+g}× {r['target']}): RSI {r['rsi']:.0f}, "
            f"+{r['dist50']:.0f}% above 50d ({r['reasons']})"
            for r in rs
        )

    def _gap_rows(rs: list[dict[str, Any]]) -> str:
        return "\n".join(
            f"- {r['ticker']} ({r['leverage']:+g}× {r['target']}): "
            f"actual − simulated daily-reset = {r['tracking_gap']:+.1f}pp"
            for r in rs
        )

    tqqq_block = ""
    if tqqq_lens is not None and tqqq_lens.qtd_return_pct is not None:
        td = "ACTIVE" if tqqq_lens.tqqq_30down_active else "inactive"
        tqqq_block = (
            f"\nTQQQ canonical 9Sig reference:\n"
            f"- QTD {tqqq_lens.qtd_return_pct:+.1f}% vs +9% target → "
            f"gap {tqqq_lens.signal_gap_pct:+.1f}pp\n"
            f"- 30-down rule: {td} (rolling 2yr DD "
            f"{tqqq_lens.tqqq_30down_drawdown_pct:.1f}%)\n"
            f"- Spike-watch: {tqqq_lens.tqqq_spike_distance_pct:.1f}pp from +100% threshold"
        )

    return (
        "You are writing an educational leveraged ETF scan summary for Tony, a portfolio "
        "tracker. Focus on the broad scan: leaders, laggards, stretched funds, oversold funds, "
        "drawdowns, and tracking anomalies. Mention TQQQ's Kelly 9Sig watch status only as a "
        "small optional reference, not as the organizing frame. Avoid trade orders, predictions, "
        "or endorsements.\n\n"
        f"TODAY: {datetime.now().strftime('%B %d, %Y')}\n\n"
        f"=== Top YTD movers ===\n{_movers(top)}\n\n"
        f"=== Bottom YTD movers ===\n{_movers(bottom)}\n\n"
        f"=== Most popping (high recent momentum + new highs) ===\n{_popping(popping)}\n\n"
        f"=== Most oversold (RSI / 52W low / drawdown) ===\n{_oversold(oversold)}\n\n"
        f"=== Most stretched (RSI / above 50d MA) ===\n{_stretched(stretched)}\n\n"
        f"=== Largest tracking gaps (actual vs simulated daily-reset, 1Y) ===\n{_gap_rows(worst_gap)}\n"
        f"{tqqq_block}\n\n"
        "Write exactly 5 short paragraphs (2-3 sentences each), separated by these delimiters.\n"
        "Calm, factual, FT/Economist tone. NO predictions, NO 'investors should…', NO exclamations, "
        "NO trade orders or sizing.\n\n"
        "===MOVERS===\n"
        "Headline what is leading and lagging this year, with the unifying exposure theme.\n"
        "===SIGNAL_CLUSTERS===\n"
        "Where popping, oversold, and stretched clusters concentrate (sector / leverage / exposure_type).\n"
        "===LEVERAGE_BEHAVIOR===\n"
        "How daily-reset compounding shows up — spread between leveraged tiers, volatility drag examples.\n"
        "===TQQQ_WATCH===\n"
        "If TQQQ data is present, give a compact watch note: QTD vs +9%, 30-down status, and spike-watch "
        "distance. Do not generalize 9Sig to the whole universe.\n"
        "===TRACKING_GAPS===\n"
        "Notable funds whose actual return diverges from their simulated daily-reset (fees, swap basis, "
        "leverage changes, counterparty constraints). Name 1-2 specific tickers from the tables above.\n\n"
        "Rules: cite specific numbers from the data; do not name funds not in the tables; "
        "do not give investment advice.\n"
        "Output ONLY the 5 delimited sections."
    )


# ---------------------------------------------------------------------------
# Gemini call
# ---------------------------------------------------------------------------

def call_gemini(prompt: str) -> str:
    if not GOOGLE_AI_API_KEY:
        logger.warning("GOOGLE_AI_API_KEY not set — skipping summary")
        return ""
    try:
        from google import genai
    except ImportError:
        logger.warning("google-genai not installed — skipping summary")
        return ""
    try:
        client = genai.Client(api_key=GOOGLE_AI_API_KEY)
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        return response.text or ""
    except Exception as e:
        logger.warning(f"Gemini call failed: {e}")
        return ""


# ---------------------------------------------------------------------------
# Parse + render
# ---------------------------------------------------------------------------

def parse_summary_sections(raw: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []
    for line in raw.split("\n"):
        s = line.strip()
        if s.startswith("===") and s.endswith("==="):
            if current_key:
                sections[current_key] = " ".join(current_lines).strip()
            current_key = s.strip("=").strip().lower()
            current_lines = []
        elif current_key:
            current_lines.append(s)
    if current_key:
        sections[current_key] = " ".join(current_lines).strip()
    return sections


def build_summary_html(raw: str) -> str:
    if not raw:
        return ""
    sections = parse_summary_sections(raw)
    if not sections:
        return ""
    label_map = [
        ("movers", "Movers"),
        ("signal_clusters", "Signal Clusters"),
        ("leverage_behavior", "Leverage Behavior"),
        ("tqqq_watch", "TQQQ Watch"),
        ("tracking_gaps", "Tracking Gaps"),
    ]
    cards: list[str] = []
    for key, label in label_map:
        text = sections.get(key, "")
        if not text:
            continue
        cards.append(
            f"""
            <div style="background:{COLORS['panel_bg']};border-radius:8px;padding:18px 22px;
                        box-shadow:0 1px 2px rgba(0,0,0,0.04);">
              <div style="font-size:11px;font-weight:700;letter-spacing:0.06em;
                          text-transform:uppercase;color:{COLORS['text_light']};margin-bottom:6px;">{label}</div>
              <p style="font-size:14px;line-height:1.65;color:{COLORS['text']};margin:0;">{text}</p>
            </div>"""
        )
    if not cards:
        return ""
    grid = "\n".join(cards)
    return f"""
    <div style="margin-bottom:28px;">
      <div style="display:flex;align-items:baseline;gap:12px;margin-bottom:12px;">
        <h2 style="font-family:'Lora',Georgia,serif;font-size:20px;font-weight:700;margin:0;">Universe Summary</h2>
        <span style="font-size:11px;color:{COLORS['text_light']};font-family:'Inter',monospace;">
          AI-generated &middot; factual only &middot; not investment advice &middot;
          {datetime.now().strftime('%b %d, %Y')}</span>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
        {grid}
      </div>
    </div>"""
