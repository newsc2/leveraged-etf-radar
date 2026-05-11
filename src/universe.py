"""Universe loader: parse data/funds.yaml into a typed in-memory structure."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.config import FUNDS_YAML

VALID_DIRECTIONS = {"long", "inverse"}
VALID_PRODUCT_TYPES = {"ETF", "ETN"}
VALID_EXPOSURES = {
    "broad_index", "sector", "industry", "country",
    "single_stock", "crypto_equity_related", "thematic",
}


@dataclass
class Fund:
    """One leveraged ETF/ETN entry from funds.yaml."""
    ticker: str
    name: str | None
    issuer: str | None
    product_type: str | None
    direction: str | None
    leverage: float | None
    exposure_type: str | None
    target_name: str | None
    target_symbol_or_index: str | None
    proxy_symbol: str | None
    inception_date: str | None = None
    expense_ratio: float | None = None
    metadata_source: str | None = None
    notes: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def has_proxy(self) -> bool:
        return bool(self.proxy_symbol)

    @property
    def abs_leverage(self) -> float:
        return abs(self.leverage) if self.leverage is not None else 0.0


def load_universe(path: Path | None = None) -> list[Fund]:
    """Load and validate the funds.yaml universe.

    Skips placeholder entries (issuer is None — those are excluded markers).
    """
    target: Path = path or FUNDS_YAML
    with target.open() as fh:
        raw: dict[str, Any] = yaml.safe_load(fh)
    entries: list[dict[str, Any]] = raw.get("funds", [])
    funds: list[Fund] = []
    for e in entries:
        if e.get("issuer") is None:
            continue
        # Coerce date to ISO string if it parsed to date object
        inc = e.get("inception_date")
        if inc is not None and not isinstance(inc, str):
            inc = inc.isoformat()
        # Coerce target_symbol to string (yaml may parse all-caps as bool/None)
        tsi = e.get("target_symbol_or_index")
        if tsi is not None:
            tsi = str(tsi)
        proxy = e.get("proxy_symbol")
        if proxy is not None:
            proxy = str(proxy)
        f = Fund(
            ticker=str(e["ticker"]),
            name=e.get("name"),
            issuer=e.get("issuer"),
            product_type=e.get("product_type"),
            direction=e.get("direction"),
            leverage=float(e["leverage"]) if e.get("leverage") is not None else None,
            exposure_type=e.get("exposure_type"),
            target_name=e.get("target_name"),
            target_symbol_or_index=tsi,
            proxy_symbol=proxy,
            inception_date=inc,
            expense_ratio=e.get("expense_ratio"),
            metadata_source=e.get("metadata_source"),
            notes=e.get("notes"),
        )
        funds.append(f)
    return funds


def collect_proxy_symbols(funds: list[Fund]) -> list[str]:
    """Return the unique sorted list of proxy symbols referenced by the universe."""
    return sorted({f.proxy_symbol for f in funds if f.proxy_symbol})


def collect_fund_tickers(funds: list[Fund]) -> list[str]:
    """Return the unique sorted list of fund tickers."""
    return sorted({f.ticker for f in funds})
