"""Configuration constants for the Leveraged ETF Radar dashboard.

Editorial design tokens (FT/Economist palette via the data-design skill):
  - White background, teal accent, serif Lora headings, Inter body.
  - 6-color categorical ramp (teal/blue/terra/gold/sage/lavender + rose).
  - Direct on-chart labels; no separate legend blocks.
"""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
DATA_DIR: Path = REPO_ROOT / "data"
CACHE_DIR: Path = REPO_ROOT / "cache"
DIST_DIR: Path = REPO_ROOT / "dist"
FUNDS_YAML: Path = DATA_DIR / "funds.yaml"

# ---------------------------------------------------------------------------
# S3 / API Keys
# ---------------------------------------------------------------------------

S3_BUCKET: str = "newsc2.com"
S3_PREFIX: str = "projects/leveraged-etf-radar/app"
GOOGLE_AI_API_KEY: str = os.environ.get("GOOGLE_AI_API_KEY", "")
BRAVE_SEARCH_API_KEY: str = os.environ.get("BRAVE_SEARCH_API_KEY", "")

# ---------------------------------------------------------------------------
# Editorial design tokens
# ---------------------------------------------------------------------------

COLORS: dict[str, str] = {
    # Surfaces
    "bg": "#FFFFFF",
    "card_bg": "#FFFFFF",
    "panel_bg": "#FAFAFA",        # KPI cell / detail-row background
    "border": "#E8E8E8",
    "grid": "#D4D4D4",
    "axis": "#BBBBBB",
    # Text
    "text": "#222222",
    "text_muted": "#555555",
    "text_light": "#707071",
    # Categorical (cat_1..cat_6 + rose)
    "accent": "#0D7680",          # teal — same as cat_1
    "cat_1": "#0D7680",           # teal
    "cat_2": "#2E6E9E",           # blue
    "cat_3": "#C4715E",           # terra
    "cat_4": "#E9B237",           # gold
    "cat_5": "#6B8F71",           # sage
    "cat_6": "#8B7D98",           # lavender
    "cat_7": "#A6485F",           # rose
    "muted": "#9B9B9B",           # gray context series
    # Semantic (slightly more muted than the v1 reds/greens)
    "green": "#2A8636",
    "red": "#CC3232",
    "highlight": "#E9B237",
}

# Ordered colorway for Plotly traces
COLORWAY: list[str] = [
    COLORS["cat_1"], COLORS["cat_2"], COLORS["cat_3"], COLORS["cat_4"],
    COLORS["cat_5"], COLORS["cat_6"], COLORS["cat_7"],
]

# Per-leverage encoding (used in scatter color + line color in performance chart)
LEVERAGE_COLORS: dict[float, str] = {
    1.5: COLORS["cat_4"],         # gold — modest leverage
    2.0: COLORS["cat_2"],         # blue
    3.0: COLORS["cat_1"],         # teal — flagship
    -1.0: COLORS["muted"],        # gray context
    -2.0: COLORS["cat_3"],        # terra
    -3.0: COLORS["cat_7"],        # rose
}

CHART_LAYOUT: dict[str, object] = dict(
    paper_bgcolor=COLORS["card_bg"],
    plot_bgcolor=COLORS["card_bg"],
    font=dict(family="Inter, system-ui, sans-serif", color=COLORS["text"], size=13),
    margin=dict(l=50, r=20, t=40, b=40),
    xaxis=dict(gridcolor=COLORS["grid"], zeroline=False),
    yaxis=dict(gridcolor=COLORS["grid"], zeroline=False),
    hoverlabel=dict(bgcolor=COLORS["card_bg"], font_size=12),
)

# Default representative funds for the compounding diagnostic (now removed but
# kept as a constant in case we re-introduce a reference panel later).
COMPOUNDING_DEFAULTS: list[str] = ["TQQQ", "UPRO", "SOXL", "NVDL", "SQQQ", "SPXU"]
