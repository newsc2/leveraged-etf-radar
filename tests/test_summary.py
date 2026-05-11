"""Tests for src.summary — prompt generation + section parsing."""
from __future__ import annotations

from src.metrics import FundMetrics
from src.summary import build_summary_html, build_summary_prompt, parse_summary_sections
from src.universe import Fund


def _fund(ticker: str = "TQQQ") -> Fund:
    return Fund(
        ticker=ticker, name=f"{ticker} Test Fund", issuer="ProShares",
        product_type="ETF", direction="long", leverage=3.0,
        exposure_type="broad_index", target_name="Nasdaq-100",
        target_symbol_or_index="^NDX", proxy_symbol="QQQ",
    )


def _metrics(ticker: str, ytd: float = 10.0) -> FundMetrics:
    m = FundMetrics(ticker=ticker)
    m.return_ytd = ytd
    m.max_drawdown_1y = -20.0
    m.realized_vol_1y = 30.0
    m.actual_minus_simulated_1y = -2.5
    m.realized_beta_60d = 3.0
    return m


class TestParseSections:
    def test_splits_on_delimiters(self) -> None:
        raw = """===MOVERS===
Top movers were X.
===CONCENTRATION===
Universe is concentrated in Y.
===LEVERAGE_BEHAVIOR===
Daily reset visible in Z.
===TRACKING_GAPS===
ABC has a notable gap."""
        d = parse_summary_sections(raw)
        assert d["movers"].startswith("Top movers")
        assert d["concentration"].startswith("Universe")
        assert d["leverage_behavior"].startswith("Daily reset")
        assert d["tracking_gaps"].startswith("ABC")


class TestBuildSummaryHtml:
    def test_returns_empty_for_empty_input(self) -> None:
        assert build_summary_html("") == ""

    def test_renders_grid_when_sections_present(self) -> None:
        raw = """===MOVERS===
Top.
===CONCENTRATION===
Conc.
===LEVERAGE_BEHAVIOR===
Lev.
===TRACKING_GAPS===
Gaps."""
        html = build_summary_html(raw)
        assert "Universe Summary" in html
        assert "Movers" in html and "Tracking Gaps" in html


class TestBuildPrompt:
    def test_includes_top_and_bottom(self) -> None:
        funds = [_fund("A"), _fund("B"), _fund("C")]
        metrics = {
            "A": _metrics("A", ytd=50.0),
            "B": _metrics("B", ytd=-30.0),
            "C": _metrics("C", ytd=5.0),
        }
        prompt = build_summary_prompt(metrics, {f.ticker: f for f in funds})
        assert "===MOVERS===" in prompt
        assert "Top YTD movers" in prompt
        assert "Bottom YTD movers" in prompt
        assert "===TRACKING_GAPS===" in prompt
