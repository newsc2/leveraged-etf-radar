"""Yahoo Finance data fetching with on-disk JSON cache.

Same chart-API approach as macro-dashboard-v3 — but caches each symbol's
raw response under cache/<symbol>.json so dev iterations don't hammer Yahoo.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from src.config import CACHE_DIR

logger: logging.Logger = logging.getLogger(__name__)

YAHOO_CHART_URL: str = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_HEADERS: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
}
CACHE_TTL_HOURS: int = 12


def _cache_path(symbol: str) -> Path:
    safe: str = symbol.replace("/", "_").replace("=", "_").replace("^", "_")
    return CACHE_DIR / f"{safe}.json"


def _cache_is_fresh(path: Path, ttl_hours: int = CACHE_TTL_HOURS) -> bool:
    if not path.exists():
        return False
    age = datetime.now().timestamp() - path.stat().st_mtime
    return age < ttl_hours * 3600


def _parse_chart_payload(payload: dict[str, Any]) -> pd.Series:
    chart_result: list[dict[str, Any]] = payload.get("chart", {}).get("result", []) or []
    if not chart_result:
        return pd.Series(dtype=float)
    timestamps: list[int] = chart_result[0].get("timestamp", []) or []
    quotes: list[dict[str, Any]] = chart_result[0].get("indicators", {}).get("quote", []) or []
    if not quotes:
        return pd.Series(dtype=float)
    closes: list[float | None] = quotes[0].get("close", []) or []
    records: list[tuple[datetime, float]] = [
        (datetime.fromtimestamp(ts), c)
        for ts, c in zip(timestamps, closes)
        if c is not None
    ]
    if not records:
        return pd.Series(dtype=float)
    df: pd.DataFrame = pd.DataFrame(records, columns=["date", "value"])
    df["date"] = pd.to_datetime(df["date"].dt.date)
    return df.set_index("date")["value"].astype(float)


def _parse_volume_payload(payload: dict[str, Any]) -> pd.Series:
    """Extract daily volume series from the same chart payload (no extra fetch)."""
    chart_result: list[dict[str, Any]] = payload.get("chart", {}).get("result", []) or []
    if not chart_result:
        return pd.Series(dtype=float)
    timestamps: list[int] = chart_result[0].get("timestamp", []) or []
    quotes: list[dict[str, Any]] = chart_result[0].get("indicators", {}).get("quote", []) or []
    if not quotes:
        return pd.Series(dtype=float)
    volumes: list[float | None] = quotes[0].get("volume", []) or []
    records: list[tuple[datetime, float]] = [
        (datetime.fromtimestamp(ts), float(v))
        for ts, v in zip(timestamps, volumes)
        if v is not None
    ]
    if not records:
        return pd.Series(dtype=float)
    df: pd.DataFrame = pd.DataFrame(records, columns=["date", "value"])
    df["date"] = pd.to_datetime(df["date"].dt.date)
    return df.set_index("date")["value"].astype(float)


def fetch_yahoo_volume(symbol: str, years_back: int = 1) -> pd.Series:
    """Daily share-volume series. Uses the same disk cache as the price fetcher.

    Reads the cached chart JSON if present (zero extra network calls).
    """
    cpath = _cache_path(symbol)
    if cpath.exists():
        try:
            with cpath.open() as fh:
                return _parse_volume_payload(json.load(fh))
        except Exception:
            pass
    # Cache miss — fetch fresh by calling fetch_yahoo_series (which writes the cache)
    fetch_yahoo_series(symbol, years_back=years_back, use_cache=True)
    if cpath.exists():
        with cpath.open() as fh:
            return _parse_volume_payload(json.load(fh))
    return pd.Series(dtype=float)


def fetch_yahoo_series(
    symbol: str,
    years_back: int = 11,
    use_cache: bool = True,
) -> pd.Series:
    """Fetch daily close prices from Yahoo Finance chart API, with disk cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cpath = _cache_path(symbol)

    if use_cache and _cache_is_fresh(cpath):
        try:
            with cpath.open() as fh:
                return _parse_chart_payload(json.load(fh))
        except Exception as e:
            logger.warning(f"Cache read failed for {symbol}: {e}")

    end_time: int = int(datetime.now().timestamp())
    start_time: int = int((datetime.now() - timedelta(days=years_back * 365)).timestamp())
    url: str = YAHOO_CHART_URL.format(symbol=symbol)
    params: dict[str, str | int] = {
        "period1": start_time,
        "period2": end_time,
        "interval": "1d",
    }
    try:
        resp: requests.Response = requests.get(url, params=params, headers=YAHOO_HEADERS, timeout=30)
        resp.raise_for_status()
        payload: dict[str, Any] = resp.json()
        if use_cache:
            try:
                with cpath.open("w") as fh:
                    json.dump(payload, fh)
            except Exception as e:
                logger.warning(f"Cache write failed for {symbol}: {e}")
        return _parse_chart_payload(payload)
    except Exception as e:
        logger.warning(f"Yahoo fetch failed for {symbol}: {e}")
        return pd.Series(dtype=float)


def fetch_many(
    symbols: list[str],
    years_back: int = 11,
    use_cache: bool = True,
) -> dict[str, pd.Series]:
    """Fetch a list of symbols sequentially, returning a {symbol: Series} dict."""
    out: dict[str, pd.Series] = {}
    for s in symbols:
        out[s] = fetch_yahoo_series(s, years_back=years_back, use_cache=use_cache)
    return out


# ---------------------------------------------------------------------------
# AUM (totalAssets) — Yahoo quoteSummary endpoint
# ---------------------------------------------------------------------------

QUOTESUMMARY_URL: str = (
    "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
    "?modules=summaryDetail,defaultKeyStatistics"
)


def _aum_cache_path(symbol: str) -> Path:
    safe: str = symbol.replace("/", "_").replace("=", "_").replace("^", "_")
    return CACHE_DIR / f"{safe}.aum.json"


def fetch_yahoo_aum(symbol: str, use_cache: bool = True) -> float | None:
    """Return total net assets (USD) for an ETF/ETN via quoteSummary.

    Yahoo's quoteSummary endpoint can be flaky / rate-limited; on any error
    we return None rather than blowing up the build. Cache is keyed
    independently from price data with a separate `.aum.json` suffix.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cpath = _aum_cache_path(symbol)
    if use_cache and _cache_is_fresh(cpath, ttl_hours=72):
        try:
            with cpath.open() as fh:
                cached = json.load(fh)
            return cached.get("totalAssets") if isinstance(cached, dict) else None
        except Exception:
            pass

    url = QUOTESUMMARY_URL.format(symbol=symbol)
    try:
        resp = requests.get(url, headers=YAHOO_HEADERS, timeout=20)
        if resp.status_code != 200:
            return None
        payload = resp.json()
        result = payload.get("quoteSummary", {}).get("result") or []
        if not result:
            return None
        node = result[0]
        # Try summaryDetail.totalAssets first, fall back to defaultKeyStatistics
        for module in ("summaryDetail", "defaultKeyStatistics"):
            mod = node.get(module) or {}
            ta = mod.get("totalAssets")
            if isinstance(ta, dict) and "raw" in ta:
                value = float(ta["raw"])
                with cpath.open("w") as fh:
                    json.dump({"totalAssets": value}, fh)
                return value
        # Cache the negative result too so we don't keep hammering
        with cpath.open("w") as fh:
            json.dump({"totalAssets": None}, fh)
        return None
    except Exception as e:
        logger.warning(f"AUM fetch failed for {symbol}: {e}")
        return None


def fetch_aum_many(symbols: list[str], use_cache: bool = True) -> dict[str, float | None]:
    return {s: fetch_yahoo_aum(s, use_cache=use_cache) for s in symbols}
