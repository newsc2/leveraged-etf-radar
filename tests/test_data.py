"""Tests for src.data — Yahoo parser and disk cache."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data import _parse_chart_payload, fetch_yahoo_series


def _payload(timestamps: list[int], closes: list[float | None]) -> dict:
    return {
        "chart": {
            "result": [{
                "timestamp": timestamps,
                "indicators": {"quote": [{"close": closes}]},
            }]
        }
    }


class TestParseChartPayload:
    def test_returns_series_on_normal_payload(self) -> None:
        s = _parse_chart_payload(_payload([1704067200, 1704153600], [4700.5, 4720.3]))
        assert isinstance(s, pd.Series)
        assert len(s) == 2
        assert s.iloc[0] == pytest.approx(4700.5)

    def test_empty_when_no_result(self) -> None:
        assert _parse_chart_payload({"chart": {"result": []}}).empty

    def test_skips_none_closes(self) -> None:
        s = _parse_chart_payload(_payload([1, 2, 3], [10.0, None, 11.0]))
        assert len(s) == 2

    def test_empty_when_all_none(self) -> None:
        s = _parse_chart_payload(_payload([1, 2], [None, None]))
        assert s.empty


class TestFetchYahooSeries:
    @patch("src.data.requests.get")
    def test_writes_cache_on_success(self, mock_get: MagicMock, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("src.data.CACHE_DIR", tmp_path)
        mock_resp = MagicMock()
        mock_resp.json.return_value = _payload([1704067200], [100.0])
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        s = fetch_yahoo_series("FOO", years_back=1)
        assert len(s) == 1
        # Cache file should exist
        cache_file = tmp_path / "FOO.json"
        assert cache_file.exists()

    @patch("src.data.requests.get")
    def test_uses_cache_on_repeat(self, mock_get: MagicMock, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("src.data.CACHE_DIR", tmp_path)
        # Pre-populate cache with a fresh file
        cache_file = tmp_path / "BAR.json"
        cache_file.write_text(json.dumps(_payload([1, 2], [50.0, 55.0])))
        s = fetch_yahoo_series("BAR")
        assert len(s) == 2
        mock_get.assert_not_called()

    @patch("src.data.requests.get")
    def test_returns_empty_on_network_error(self, mock_get: MagicMock, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("src.data.CACHE_DIR", tmp_path)
        mock_get.side_effect = Exception("Network down")
        s = fetch_yahoo_series("BAZ", use_cache=False)
        assert s.empty
