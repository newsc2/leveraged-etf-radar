"""Full-history, reinvested-return comparison data and embedded panel."""
from __future__ import annotations

import csv
import io
import json
import logging
import math
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from src.config import CACHE_DIR
from src.data import YAHOO_CHART_URL, YAHOO_HEADERS, _cache_is_fresh

START_DATE = "1996-01-01"
SYMBOLS = ["VTSAX", "VGT", "SCHD", "QQQ", "VOO", "VOOG", "QLD", "TECL", "TQQQ", "SPOT", "AMZN", "BTC-USD"]
GOLD_URL = "https://goldprice.com/gold-price-history.csv"
GOLD_SOURCE = "https://goldprice.com/gold-price-history"
logger = logging.getLogger(__name__)
PriceRows = list[tuple[str, float]]


def parse_yahoo_history(payload: dict[str, Any]) -> PriceRows:
    results = payload.get("chart", {}).get("result") or []
    if not results:
        raise ValueError("Missing Yahoo price history")
    result = results[0]
    adjusted = result.get("indicators", {}).get("adjclose") or []
    if not adjusted:
        raise ValueError("Adjusted prices required; raw prices are not a total-return substitute")
    rows = []
    for timestamp, value in zip(result.get("timestamp", []), adjusted[0].get("adjclose", [])):
        if value is not None and math.isfinite(value) and value > 0:
            # Crypto timestamps are UTC midnight; local conversion would shift their date backward.
            day = datetime.fromtimestamp(timestamp, UTC).date().isoformat()
            rows.append((day, float(value)))
    if not rows:
        raise ValueError("Empty adjusted price history")
    return sorted(dict(rows).items())


def parse_gold_history(content: str) -> PriceRows:
    reader = csv.DictReader(line for line in io.StringIO(content) if not line.startswith("#"))
    rows = []
    for row in reader:
        day = date.fromisoformat(row["date"]).isoformat()
        value = float(row["close_usd_per_troy_oz"])
        if math.isfinite(value) and value > 0:
            rows.append((day, value))
    if not rows:
        raise ValueError("Empty spot gold history")
    return sorted(dict(rows).items())


def fetch_comparison_history(symbol: str, use_cache: bool = True) -> PriceRows:
    cache = CACHE_DIR / "comparison" / f"{symbol}.json"
    if use_cache and _cache_is_fresh(cache):
        saved: list[list[Any]] = json.loads(cache.read_text())
        return [(str(row[0]), float(row[1])) for row in saved]
    try:
        if symbol == "XAU-USD":
            response = requests.get(GOLD_URL, headers=YAHOO_HEADERS, timeout=30)
            response.raise_for_status()
            rows = parse_gold_history(response.text)
        else:
            params: dict[str, str | int] = {
                "period1": 820454400,
                "period2": int(datetime.now(UTC).timestamp()),
                "interval": "1d",
                "events": "div,splits,capitalGains",
                "includeAdjustedClose": "true",
            }
            response = requests.get(
                YAHOO_CHART_URL.format(symbol=symbol),
                params=params,
                headers=YAHOO_HEADERS,
                timeout=30,
            )
            response.raise_for_status()
            rows = parse_yahoo_history(response.json())
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_suffix(".tmp")
        temporary.write_text(json.dumps(rows, separators=(",", ":")))
        temporary.replace(cache)
        return rows
    except (requests.RequestException, ValueError, KeyError) as error:
        if use_cache and cache.exists():
            logger.warning("Comparison %s refresh failed; retaining dated cached history: %s", symbol, error)
            saved = json.loads(cache.read_text())
            return [(str(row[0]), float(row[1])) for row in saved]
        raise RuntimeError(f"Comparison history unavailable for {symbol}") from error


def build_comparison_data(histories: dict[str, PriceRows], as_of: date | None = None) -> dict[str, Any]:
    today = (as_of or datetime.now(ZoneInfo("America/New_York")).date()).isoformat()
    cleaned = {
        symbol: [(day, value) for day, value in rows if START_DATE <= day < today]
        for symbol, rows in histories.items()
    }
    for symbol, rows in cleaned.items():
        if not rows:
            raise ValueError(f"No completed daily observations for {symbol}")
    common_dates = set.intersection(*(set(day for day, _ in rows) for rows in cleaned.values()))
    if not common_dates:
        raise ValueError("Comparison assets have no common closing date")
    end = max(common_dates)
    funds = []
    for symbol, rows in cleaned.items():
        trimmed = [(day, value) for day, value in rows if day <= end]
        funds.append({
            "symbol": symbol,
            "label": {"BTC-USD": "Bitcoin", "XAU-USD": "Gold spot"}.get(symbol, symbol),
            "leveraged": symbol in {"QLD", "TECL", "TQQQ"},
            "source": GOLD_SOURCE if symbol == "XAU-USD" else f"https://finance.yahoo.com/quote/{symbol}/history/",
            "sourceEnd": rows[-1][0],
            "prices": [[day, float(f"{value:.10g}")] for day, value in trimmed],
        })
    return {"start": START_DATE, "end": end, "defaultStart": "2021-01-01", "funds": funds}


def build_comparison_panel(use_cache: bool = True) -> str:
    histories = {symbol: fetch_comparison_history(symbol, use_cache) for symbol in [*SYMBOLS, "XAU-USD"]}
    data = build_comparison_data(histories)
    directory = Path(__file__).parent
    template = (directory / "comparison.html").read_text()
    script = (directory / "comparison.js").read_text()
    payload = json.dumps(data, separators=(",", ":"), allow_nan=False).replace("<", "\\u003c")
    return template.replace("__COMPARISON_DATA__", payload).replace("__COMPARISON_SCRIPT__", script)
