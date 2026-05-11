"""Issuer holdings providers with confidence labels.

Two distinct composition layers — they MUST stay separate:
  1. actual_holdings        — what the trust holds (swaps, futures, T-bills, equities)
  2. economic_exposure      — the target index/stock and its components

Each provider returns a Holdings record with:
  confidence   ∈ {issuer_native_actual_holdings,
                  issuer_native_index_components,
                  proxy_etf_holdings,
                  manual_target_only}
  source_url   — where it came from (or null if synthesized)
  fetched_at   — ISO timestamp
  components   — list[{name, weight, kind}]   (kind: equity|swap|future|tbill|cash|other)

Live fetches are best-effort. Network failures degrade gracefully to lower-confidence
fallbacks; we NEVER fabricate holdings.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

import requests

from src.universe import Fund

logger: logging.Logger = logging.getLogger(__name__)

USER_AGENT: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) leveraged-etf-radar/0.1"
FETCH_TIMEOUT: int = 20

CONFIDENCE_ACTUAL: str = "issuer_native_actual_holdings"
CONFIDENCE_INDEX: str = "issuer_native_index_components"
CONFIDENCE_PROXY: str = "proxy_etf_holdings"
CONFIDENCE_TARGET: str = "manual_target_only"

CONFIDENCE_ORDER: dict[str, int] = {
    CONFIDENCE_ACTUAL: 4,
    CONFIDENCE_INDEX: 3,
    CONFIDENCE_PROXY: 2,
    CONFIDENCE_TARGET: 1,
}


@dataclass
class Component:
    name: str
    weight: float | None
    kind: str  # equity | swap | future | tbill | cash | other


@dataclass
class Holdings:
    ticker: str
    confidence: str
    source_url: str | None = None
    fetched_at: str = ""
    components: list[Component] = field(default_factory=list)
    note: str | None = None


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _fetch_html(url: str) -> str:
    headers: dict[str, str] = {"User-Agent": USER_AGENT}
    resp: requests.Response = requests.get(url, headers=headers, timeout=FETCH_TIMEOUT)
    resp.raise_for_status()
    return resp.text


# ---------------------------------------------------------------------------
# ProShares — in-page holdings table parser
#   URL pattern: https://www.proshares.com/our-etfs/leveraged-and-inverse/<TICKER>
#   Holdings table: rows of "<name> ... <weight>%" with a SWAP / TREASURY / CASH classifier
# ---------------------------------------------------------------------------

PROSHARES_URL: str = "https://www.proshares.com/our-etfs/leveraged-and-inverse/{ticker}"

_PROSHARES_ROW = re.compile(
    r"<tr[^>]*>(?P<row>.*?)</tr>", re.DOTALL | re.IGNORECASE,
)
_PROSHARES_CELL = re.compile(r"<t[dh][^>]*>(?P<v>.*?)</t[dh]>", re.DOTALL | re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def _strip(html: str) -> str:
    return _TAG.sub("", html).replace("&nbsp;", " ").strip()


def _classify_kind(name: str) -> str:
    n = name.lower()
    if "swap" in n:
        return "swap"
    if "future" in n or "futures" in n:
        return "future"
    if "treasury" in n or "t-bill" in n or "u.s. treasury" in n:
        return "tbill"
    if "cash" in n or "money market" in n or "deposit" in n:
        return "cash"
    return "equity"


def _parse_proshares_holdings(html: str) -> list[Component]:
    """Extract (name, weight, kind) rows from a ProShares fund page."""
    components: list[Component] = []
    for row_match in _PROSHARES_ROW.finditer(html):
        row = row_match.group("row")
        cells = [_strip(c.group("v")) for c in _PROSHARES_CELL.finditer(row)]
        if len(cells) < 2:
            continue
        # Find a percent cell
        weight: float | None = None
        weight_idx: int = -1
        for i, c in enumerate(cells):
            m = re.match(r"^-?\d+(?:\.\d+)?\s*%$", c)
            if m:
                weight = float(c.rstrip("%").strip())
                weight_idx = i
                break
        if weight is None or weight_idx == 0:
            continue
        name_candidates = [c for c in cells[:weight_idx] if c]
        if not name_candidates:
            continue
        name = name_candidates[0]
        if not name or len(name) < 2:
            continue
        components.append(Component(name=name, weight=weight, kind=_classify_kind(name)))
    return components


def fetch_proshares_holdings(ticker: str) -> Holdings | None:
    """Fetch ProShares fund page and parse the holdings table. None on failure."""
    url = PROSHARES_URL.format(ticker=ticker.lower())
    try:
        html = _fetch_html(url)
    except Exception as e:
        logger.warning(f"ProShares fetch failed for {ticker}: {e}")
        return None
    components = _parse_proshares_holdings(html)
    if not components:
        return None
    return Holdings(
        ticker=ticker,
        confidence=CONFIDENCE_ACTUAL,
        source_url=url,
        fetched_at=datetime.now().isoformat(),
        components=components,
    )


# ---------------------------------------------------------------------------
# GraniteShares — downloadable holdings file detector
#   URL pattern: https://graniteshares.com/etfs/<TICKER>/
#   Look for a link to *holdings*.csv / *holdings*.xlsx / .csv?fund=<TICKER>
# ---------------------------------------------------------------------------

GRANITE_URL: str = "https://graniteshares.com/etfs/{ticker}/"
_GRANITE_LINK = re.compile(
    r'href="(?P<url>[^"]*?(holdings|portfolio)[^"]*\.(csv|xlsx|xls))"',
    re.IGNORECASE,
)


def detect_graniteshares_link(html: str) -> str | None:
    """Return absolute URL of the holdings download, or None."""
    m = _GRANITE_LINK.search(html)
    if not m:
        return None
    url = m.group("url")
    if url.startswith("/"):
        return f"https://graniteshares.com{url}"
    if url.startswith("http"):
        return url
    return f"https://graniteshares.com/{url.lstrip('/')}"


def fetch_graniteshares_holdings(ticker: str) -> Holdings | None:
    """Detect a holdings file link on the GraniteShares fund page.

    We don't fetch the file itself in v1 (XLSX parsing brings in pandas+openpyxl
    overhead and issuer-specific column layouts). We just record the URL with
    "actual holdings (link)" confidence so the dashboard can deep-link to it.
    """
    url = GRANITE_URL.format(ticker=ticker.lower())
    try:
        html = _fetch_html(url)
    except Exception as e:
        logger.warning(f"GraniteShares fetch failed for {ticker}: {e}")
        return None
    download_url = detect_graniteshares_link(html)
    if not download_url:
        return None
    return Holdings(
        ticker=ticker,
        confidence=CONFIDENCE_ACTUAL,
        source_url=download_url,
        fetched_at=datetime.now().isoformat(),
        components=[],
        note="Holdings available as downloadable file at source_url; not parsed in v1.",
    )


# ---------------------------------------------------------------------------
# Direxion — index sector breakdown parser
#   URL pattern: https://www.direxion.com/product/<slug>
#   Look for "Sector Weightings" table — these are *index components* (level 2),
#   not the actual swap-based fund holdings (level 1).
# ---------------------------------------------------------------------------

DIREXION_FALLBACK_URLS: list[str] = [
    "https://www.direxion.com/product/{slug}",
]

# Map a few known Direxion tickers to their product slug (best-effort; missing
# tickers fall back to the proxy-based provider).
DIREXION_SLUGS: dict[str, str] = {
    "SPXL": "daily-sp-500-bull-bear-3x-etfs",
    "SPXS": "daily-sp-500-bull-bear-3x-etfs",
    "TNA": "daily-small-cap-bull-bear-3x-etfs",
    "TZA": "daily-small-cap-bull-bear-3x-etfs",
    "TECL": "daily-technology-bull-bear-3x-etfs",
    "TECS": "daily-technology-bull-bear-3x-etfs",
    "SOXL": "daily-semiconductor-bull-bear-3x-etfs",
    "SOXS": "daily-semiconductor-bull-bear-3x-etfs",
    "FAS": "daily-financial-bull-bear-3x-etfs",
    "FAZ": "daily-financial-bull-bear-3x-etfs",
}

_DIREXION_SECTOR_BLOCK = re.compile(
    r"sector\s+weightings?(?P<body>.{0,4000})",
    re.IGNORECASE | re.DOTALL,
)
_PCT_PAIR = re.compile(
    r"(?P<name>[A-Za-z][A-Za-z0-9 &/\-]{2,40})\s*[:\s]*(?P<weight>\d{1,2}(?:\.\d+)?)\s*%",
)


def _parse_direxion_sector_weights(html: str) -> list[Component]:
    block = _DIREXION_SECTOR_BLOCK.search(html)
    if not block:
        return []
    body = block.group("body")
    seen: set[str] = set()
    out: list[Component] = []
    for m in _PCT_PAIR.finditer(body):
        name = m.group("name").strip()
        if name.lower() in seen or len(name) < 3:
            continue
        seen.add(name.lower())
        try:
            w = float(m.group("weight"))
        except ValueError:
            continue
        if 0 < w <= 100:
            out.append(Component(name=name, weight=w, kind="equity"))
        if len(out) >= 12:
            break
    return out


def fetch_direxion_holdings(ticker: str) -> Holdings | None:
    slug = DIREXION_SLUGS.get(ticker.upper())
    if not slug:
        return None
    url = f"https://www.direxion.com/product/{slug}"
    try:
        html = _fetch_html(url)
    except Exception as e:
        logger.warning(f"Direxion fetch failed for {ticker}: {e}")
        return None
    components = _parse_direxion_sector_weights(html)
    if not components:
        return None
    return Holdings(
        ticker=ticker,
        confidence=CONFIDENCE_INDEX,
        source_url=url,
        fetched_at=datetime.now().isoformat(),
        components=components,
        note="Sector weights of underlying index. Actual fund uses swaps.",
    )


# ---------------------------------------------------------------------------
# Fallback: target-only / proxy-only
# ---------------------------------------------------------------------------

def fallback_target_only(fund: Fund) -> Holdings:
    """Lowest-confidence fallback: just record the target name."""
    name = fund.target_name or fund.target_symbol_or_index or fund.ticker
    return Holdings(
        ticker=fund.ticker,
        confidence=CONFIDENCE_TARGET,
        source_url=None,
        fetched_at=datetime.now().isoformat(),
        components=[Component(name=name, weight=100.0, kind="equity")],
        note="No holdings parser ran; showing target name only.",
    )


# ---------------------------------------------------------------------------
# Top-level dispatcher
# ---------------------------------------------------------------------------

def resolve_holdings(fund: Fund, *, live: bool = True) -> Holdings:
    """Pick the highest-confidence provider that succeeds for this fund."""
    if live and fund.issuer == "ProShares":
        h = fetch_proshares_holdings(fund.ticker)
        if h:
            return h
    if live and fund.issuer == "GraniteShares":
        h = fetch_graniteshares_holdings(fund.ticker)
        if h:
            return h
    if live and fund.issuer == "Direxion":
        h = fetch_direxion_holdings(fund.ticker)
        if h:
            return h
    return fallback_target_only(fund)


def resolve_all(funds: list[Fund], *, live: bool = True) -> dict[str, Holdings]:
    return {f.ticker: resolve_holdings(f, live=live) for f in funds}
