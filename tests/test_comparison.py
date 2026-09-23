"""Financial history alignment and data-source safeguards."""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.comparison import (
    build_comparison_data,
    fetch_comparison_history,
    parse_gold_history,
    parse_yahoo_history,
)


def test_crypto_midnight_keeps_utc_calendar_date() -> None:
    data = {"chart": {"result": [{"timestamp": [1704067200, 1704153600], "indicators": {
        "quote": [{"close": [999, 999]}], "adjclose": [{"adjclose": [100, 110]}],
    }}]}}
    assert parse_yahoo_history(data) == [("2024-01-01", 100), ("2024-01-02", 110)]


def test_missing_adjustment_cannot_silently_become_price_return() -> None:
    with pytest.raises(ValueError, match="Adjusted prices required"):
        parse_yahoo_history({"chart": {"result": [{"indicators": {"quote": [{"close": [100]}]}}]}})


def test_spot_gold_csv_preserves_closes_and_ignores_attribution() -> None:
    assert parse_gold_history(
        "date,close_usd_per_troy_oz\n1996-01-02,389.15\n2026-09-21,4343.545\n# Source: GoldPrice.com\n"
    ) == [("1996-01-02", 389.15), ("2026-09-21", 4343.545)]


def test_common_cutoff_excludes_intraday_and_weekend_crypto_tail() -> None:
    history = {
        "VTSAX": [("2024-01-05", 100), ("2024-01-08", 105)],
        "BTC-USD": [("2024-01-05", 200), ("2024-01-06", 210), ("2024-01-07", 205), ("2024-01-08", 215)],
    }
    data = build_comparison_data(history, as_of=date(2024, 1, 8))
    assert data["end"] == "2024-01-05"
    assert data["funds"][1]["prices"] == [["2024-01-05", 200]]
    assert data["funds"][1]["sourceEnd"] == "2024-01-07"


def test_no_common_cutoff_fails_instead_of_fabricating_prices() -> None:
    with pytest.raises(ValueError, match="no common closing date"):
        build_comparison_data({"A": [("2024-01-01", 100)], "B": [("2024-01-02", 200)]})


def test_history_cache_is_separate_from_short_dashboard_cache(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("src.comparison.CACHE_DIR", tmp_path)
    (tmp_path / "VTSAX.adjusted.json").write_text("{}")
    with patch("src.comparison.requests.get") as get:
        response = MagicMock()
        response.json.return_value = {"chart": {"result": [{"timestamp": [974125800], "indicators": {
            "adjclose": [{"adjclose": [19.24]}],
        }}]}}
        get.return_value = response
        assert fetch_comparison_history("VTSAX") == [("2000-11-13", 19.24)]
        assert get.call_args.kwargs["params"]["period1"] == 820454400
        assert (tmp_path / "comparison/VTSAX.json").exists()
        get.reset_mock()
        assert fetch_comparison_history("VTSAX") == [("2000-11-13", 19.24)]
        get.assert_not_called()
