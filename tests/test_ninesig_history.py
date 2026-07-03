"""Tests for the normalized 9Sig history data contract."""
from __future__ import annotations

from src.ninesig_history import load_ninesig_history, ninesig_history_to_dicts


def test_loads_normalized_history_rows() -> None:
    rows = load_ninesig_history()

    assert len(rows) == 38
    assert rows[0].quarter == "2017 Q1"
    assert rows[0].action_type == "initial"
    assert rows[0].qoq_change is None
    assert rows[-1].quarter == "2026 Q2"
    assert rows[-1].portfolio_value == 11_159_088


def test_serializes_dashboard_payload_shape() -> None:
    row = ninesig_history_to_dicts()[0]

    assert set(row) == {
        "quarter",
        "date",
        "action",
        "action_type",
        "tqqq_allocation",
        "agg_allocation",
        "portfolio_value",
        "qoq_change",
        "notes",
    }
