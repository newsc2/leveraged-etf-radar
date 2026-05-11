"""Tests for src.html — generation, classes, filter bar, table, KPI strip, detail panel."""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from src.holdings import Component, Holdings
from src.html import _classes_for, build_filter_bar, build_screener_table, generate_html
from src.metrics import FundMetrics
from src.universe import Fund


def _fund(ticker: str, leverage: float = 3.0, exposure: str = "broad_index",
          direction: str = "long", product: str = "ETF") -> Fund:
    return Fund(
        ticker=ticker, name=f"{ticker} Test", issuer="ProShares",
        product_type=product, direction=direction, leverage=leverage,
        exposure_type=exposure, target_name="X Index",
        target_symbol_or_index="X", proxy_symbol="SPY",
    )


def _metrics(ticker: str, ytd: float | None = 5.0) -> FundMetrics:
    m = FundMetrics(ticker=ticker)
    m.latest_close = 100.0
    m.return_ytd = ytd
    m.return_1m = 1.0
    m.return_3m = 2.0
    m.return_1y = ytd * 2 if ytd is not None else None
    m.return_3y = 30.0
    m.return_5y = 50.0
    m.return_10y = 100.0
    m.realized_vol_1y = 25.0
    m.realized_beta_60d = 3.0
    m.max_drawdown_1y = -15.0
    m.actual_minus_simulated_1y = -1.5
    m.avg_daily_dollar_volume = 1.5e9
    return m


class TestClassesFor:
    def test_includes_direction_leverage_exposure(self) -> None:
        cls = _classes_for(_fund("TQQQ", 3.0))
        assert "dir-long" in cls
        assert "lev-3" in cls
        assert "exp-broad_index" in cls
        assert "prod-ETF" in cls

    def test_inverse_class(self) -> None:
        cls = _classes_for(_fund("SQQQ", -3.0, direction="inverse"))
        assert "dir-inverse" in cls
        assert "sign-neg" in cls


class TestScreenerTable:
    def test_includes_funds_and_compact_columns(self) -> None:
        funds = [_fund("TQQQ", 3.0), _fund("UPRO", 3.0)]
        metrics = {"TQQQ": _metrics("TQQQ"), "UPRO": _metrics("UPRO", ytd=10.0)}
        html = build_screener_table(funds, metrics)
        assert "TQQQ" in html
        assert "UPRO" in html
        assert 'class="screener-row' in html

    def test_compact_default_columns(self) -> None:
        html = build_screener_table([_fund("TQQQ")], {"TQQQ": _metrics("TQQQ")})
        for col in ["Ticker", "Issuer", "Lev", "Target", "YTD", "1Y", "5Y", "Vol"]:
            assert f">{col}<" in html
        # 1M, 3M, 3Y, 10Y, Drawdown, Gap moved to detail panel — should NOT be header columns
        for excluded in ['data-sort="1m"', 'data-sort="3m"', 'data-sort="3y"',
                         'data-sort="10y"', 'data-sort="dd"', 'data-sort="gap"']:
            assert excluded not in html

    def test_emits_detail_row_for_each_fund(self) -> None:
        html = build_screener_table([_fund("TQQQ"), _fund("UPRO")],
                                    {f.ticker: _metrics(f.ticker) for f in
                                     [_fund("TQQQ"), _fund("UPRO")]})
        assert html.count("detail-row") >= 2
        assert 'id="detail-TQQQ"' in html
        assert 'id="detail-panel-TQQQ"' in html

    def test_caret_marker_present(self) -> None:
        html = build_screener_table([_fund("TQQQ")], {"TQQQ": _metrics("TQQQ")})
        assert 'class="caret"' in html


class TestFilterBar:
    def test_buttons_present(self) -> None:
        html = build_filter_bar(143)
        for keyword in ["Long", "Inverse", "Broad Index", "Single Stock", "ETF only"]:
            assert keyword in html
        assert "filter-counter" in html
        assert "filter-count" in html

    def test_total_count_rendered(self) -> None:
        html = build_filter_bar(143)
        assert ">143</span> of 143 funds" in html

    def test_every_exposure_type_has_a_button(self) -> None:
        """Guard against the KORU bug: every exposure type in funds.yaml must
        have a corresponding filter button. Otherwise funds get silently
        hidden from any non-'All' exposure filter."""
        from src.universe import load_universe
        funds = load_universe()
        actual_types = {f.exposure_type for f in funds if f.exposure_type}
        html = build_filter_bar(len(funds))
        for t in actual_types:
            assert f".exp-{t}" in html, f"No filter button covers exposure_type={t!r}"


class TestGenerateHtml:
    def _build(self, funds: list[Fund], metrics: dict[str, FundMetrics]) -> str:
        idx = pd.date_range(end=datetime.now(), periods=300, freq="B")
        s = pd.Series([100.0 + i * 0.1 for i in range(len(idx))], index=idx)
        holdings = {f.ticker: Holdings(
            ticker=f.ticker, confidence="manual_target_only",
            fetched_at=datetime.now().isoformat(),
            components=[Component("X Index", 100.0, "equity")],
        ) for f in funds}
        return generate_html(
            funds=funds, metrics=metrics, fund_data={f.ticker: s for f in funds},
            proxy_data={"SPY": s}, holdings=holdings, summary_html="",
        )

    def test_includes_favicon_and_fonts(self) -> None:
        html = self._build([_fund("TQQQ")], {"TQQQ": _metrics("TQQQ")})
        assert '<link rel="icon" type="image/svg+xml" href="/favicon.svg" />' in html
        assert "Lora" in html and "Inter" in html

    def test_includes_kpi_strip_and_filter_bar(self) -> None:
        html = self._build([_fund("TQQQ")], {"TQQQ": _metrics("TQQQ")})
        assert 'id="kpi-strip"' in html
        assert 'class="filter-shell"' in html

    def test_includes_performance_and_summary_charts(self) -> None:
        html = self._build([_fund("TQQQ")], {"TQQQ": _metrics("TQQQ")})
        for kw in ['id="performance-chart"', 'id="risk-return-chart"',
                   'data-tf="YTD"', 'data-tf="10Y"']:
            assert kw in html
        # The old risk-vs-liquidity chart is gone
        assert 'id="risk-liquidity-chart"' not in html

    def test_movers_are_tables_not_charts(self) -> None:
        html = self._build([_fund("TQQQ")], {"TQQQ": _metrics("TQQQ")})
        # Bar chart removed
        assert 'id="movers-chart"' not in html
        # Tables present
        assert 'id="movers-top"' in html
        assert 'id="movers-bottom"' in html
        # Titles use dynamic timeframe spans now (default "YTD" until JS updates)
        assert "Best <span" in html and "Worst <span" in html
        assert "movers-col-tf" in html

    def test_how_to_read_callout_present(self) -> None:
        html = self._build([_fund("TQQQ")], {"TQQQ": _metrics("TQQQ")})
        assert 'class="how-to-read"' in html
        assert "How to read it" in html
        assert "Best zone" in html and "Worst zone" in html
        # Mentions drawdown axis labels rather than liquidity
        assert "drawdown" in html.lower()

    def test_movers_have_timeframe_label_hooks(self) -> None:
        """Movers tables expose dynamic timeframe labels (JS updates them)."""
        html = self._build([_fund("TQQQ")], {"TQQQ": _metrics("TQQQ")})
        assert 'id="movers-tf-label"' in html
        assert "movers-col-tf" in html

    def test_no_legacy_sections(self) -> None:
        """Tracking / Compounding / Composition / Universe-Shape removed in v3."""
        html = self._build([_fund("TQQQ")], {"TQQQ": _metrics("TQQQ")})
        for removed in ["Largest 1Y Tracking Gaps", "Compounding Diagnostic",
                        "Composition", "Leverage Distribution"]:
            assert removed not in html

    def test_radar_json_present(self) -> None:
        html = self._build([_fund("TQQQ")], {"TQQQ": _metrics("TQQQ")})
        assert 'id="radar-data"' in html
        assert '"prices"' in html
        assert '"holdings"' in html
        assert '"addv"' in html  # avg daily dollar volume in metrics payload
