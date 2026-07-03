"""Load and validate the normalized Jason Kelly 9Sig quarterly history."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, TypeAlias

from src.config import DATA_DIR

HISTORY_FILE: Path = DATA_DIR / "sig_plans" / "9sig_quarterly_history.json"

NineSigActionType: TypeAlias = Literal[
    "initial",
    "buy_tqqq",
    "buy_tqqq_30_down",
    "buy_tqqq_limited",
    "sell_tqqq",
    "no_action",
    "reset_60_40",
]

_VALID_ACTION_TYPES: set[str] = {
    "initial",
    "buy_tqqq",
    "buy_tqqq_30_down",
    "buy_tqqq_limited",
    "sell_tqqq",
    "no_action",
    "reset_60_40",
}


@dataclass(frozen=True)
class NineSigQuarter:
    quarter: str
    date: str
    action: str
    action_type: NineSigActionType
    tqqq_allocation: float
    agg_allocation: float
    portfolio_value: float
    qoq_change: float | None
    notes: str


def load_ninesig_history(path: Path | None = None) -> list[NineSigQuarter]:
    """Return normalized 9Sig rows from the tracked JSON file."""
    path = path or HISTORY_FILE
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        raise ValueError(f"{path} must contain a JSON list of quarterly rows")

    rows: list[NineSigQuarter] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"{path} row {idx} must be an object")

        action_type = str(item.get("action_type", ""))
        if action_type not in _VALID_ACTION_TYPES:
            raise ValueError(f"{path} row {idx} has invalid action_type={action_type!r}")

        rows.append(
            NineSigQuarter(
                quarter=str(item["quarter"]),
                date=str(item["date"]),
                action=str(item["action"]),
                action_type=action_type,  # type: ignore[arg-type]
                tqqq_allocation=float(item["tqqq_allocation"]),
                agg_allocation=float(item["agg_allocation"]),
                portfolio_value=float(item["portfolio_value"]),
                qoq_change=(
                    None if item.get("qoq_change") is None else float(item["qoq_change"])
                ),
                notes=str(item["notes"]),
            )
        )

    return rows


def ninesig_history_to_dicts(
    history: list[NineSigQuarter] | None = None,
) -> list[dict[str, object]]:
    """Serialize history rows for embedding in the static dashboard payload."""
    rows = history or load_ninesig_history()
    return [asdict(row) for row in rows]
