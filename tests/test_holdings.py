"""Tests for src.holdings — provider parsers + dispatcher."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.holdings import (
    _parse_direxion_sector_weights,
    _parse_proshares_holdings,
    detect_graniteshares_link,
    fallback_target_only,
    resolve_holdings,
)
from src.universe import Fund


def _fund(ticker: str = "TQQQ", issuer: str = "ProShares") -> Fund:
    return Fund(
        ticker=ticker, name=f"{ticker} Test Fund", issuer=issuer,
        product_type="ETF", direction="long", leverage=3.0,
        exposure_type="broad_index", target_name="Nasdaq-100",
        target_symbol_or_index="^NDX", proxy_symbol="QQQ",
    )


class TestProSharesParser:
    def test_extracts_swap_tbill_cash(self) -> None:
        html = """
        <table><tbody>
        <tr><td>S&amp;P 500 INDEX SWAP</td><td>BARCLAYS</td><td>295.45%</td></tr>
        <tr><td>U.S. TREASURY BILL 0% 2026</td><td></td><td>50.10%</td></tr>
        <tr><td>CASH AND OTHER</td><td></td><td>4.20%</td></tr>
        </tbody></table>
        """
        cs = _parse_proshares_holdings(html)
        kinds = {c.kind for c in cs}
        assert kinds == {"swap", "tbill", "cash"}
        assert any(c.weight is not None and c.weight > 290 for c in cs)

    def test_returns_empty_for_no_table(self) -> None:
        assert _parse_proshares_holdings("<html><body>no table</body></html>") == []

    def test_skips_rows_without_percent(self) -> None:
        html = "<tr><td>Junk</td><td>more junk</td></tr>"
        assert _parse_proshares_holdings(html) == []


class TestDirexionParser:
    def test_extracts_sector_weights(self) -> None:
        html = """
        <h3>Sector Weightings</h3>
        <div>Information Technology: 28.5%</div>
        <div>Financials: 14.2%</div>
        <div>Health Care: 12.1%</div>
        """
        cs = _parse_direxion_sector_weights(html)
        names = [c.name for c in cs]
        assert "Information Technology" in names
        assert all(c.weight is not None and 0 < c.weight <= 100 for c in cs)

    def test_no_match_when_no_block(self) -> None:
        assert _parse_direxion_sector_weights("<p>nothing useful</p>") == []


class TestGraniteSharesLinkDetector:
    def test_finds_relative_csv(self) -> None:
        html = '<a href="/wp-content/uploads/2024/holdings_NVDL.csv">Holdings</a>'
        url = detect_graniteshares_link(html)
        assert url is not None and url.startswith("https://graniteshares.com")
        assert "holdings" in url.lower()

    def test_finds_absolute_xlsx(self) -> None:
        html = '<a href="https://example.com/portfolio.xlsx">P</a>'
        url = detect_graniteshares_link(html)
        assert url == "https://example.com/portfolio.xlsx"

    def test_returns_none_when_no_link(self) -> None:
        assert detect_graniteshares_link("<html></html>") is None


class TestFallbackAndDispatch:
    def test_fallback_uses_target_name(self) -> None:
        h = fallback_target_only(_fund())
        assert h.confidence == "manual_target_only"
        assert h.components and h.components[0].name == "Nasdaq-100"

    @patch("src.holdings.requests.get")
    def test_resolve_proshares_path(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.text = """
        <table>
        <tr><td>NDX SWAP</td><td>295.0%</td></tr>
        </table>
        """
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp
        h = resolve_holdings(_fund("TQQQ", "ProShares"), live=True)
        assert h.confidence == "issuer_native_actual_holdings"

    def test_resolve_falls_back_when_live_false(self) -> None:
        h = resolve_holdings(_fund(), live=False)
        assert h.confidence == "manual_target_only"
