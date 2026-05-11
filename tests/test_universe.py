"""Tests for src.universe — yaml loader and helpers."""
from __future__ import annotations

from src.universe import collect_fund_tickers, collect_proxy_symbols, load_universe


class TestLoadUniverse:
    def test_loads_real_universe(self) -> None:
        funds = load_universe()
        assert len(funds) > 100
        # Required core tickers
        tickers = {f.ticker for f in funds}
        for must_have in ["TQQQ", "UPRO", "SPXL", "NVDL", "SOXL"]:
            assert must_have in tickers

    def test_skips_excluded_placeholders(self) -> None:
        funds = load_universe()
        for f in funds:
            assert f.issuer is not None  # placeholders filtered out

    def test_leverage_is_float(self) -> None:
        funds = load_universe()
        for f in funds:
            if f.leverage is not None:
                assert isinstance(f.leverage, float)


class TestCollectors:
    def test_proxies_unique_and_sorted(self) -> None:
        funds = load_universe()
        proxies = collect_proxy_symbols(funds)
        assert proxies == sorted(set(proxies))
        assert "SPY" in proxies
        assert "QQQ" in proxies

    def test_tickers_unique_and_sorted(self) -> None:
        funds = load_universe()
        tickers = collect_fund_tickers(funds)
        assert tickers == sorted(set(tickers))
