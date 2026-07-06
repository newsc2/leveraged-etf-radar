#!/usr/bin/env python3
"""Static Plotly Dashboard Export — Leveraged ETF Radar.

Loads the curated universe from data/funds.yaml, fetches Yahoo Finance prices
for each fund + each unique proxy symbol (with disk cache), computes per-fund
metrics, attempts best-effort holdings scrapes, and renders dist/index.html.

Usage:
    python export_static.py                   # local
    python export_static.py --upload          # + S3
    python export_static.py --no-summary      # skip LLM section
    python export_static.py --no-holdings     # skip live issuer scrapes
    python export_static.py --refresh-cache   # bypass disk cache
    python export_static.py --limit 30        # dev convenience
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src.config import DIST_DIR
from src.data import fetch_many, fetch_yahoo_adjusted_series, fetch_yahoo_series, fetch_yahoo_volume
from src.holdings import resolve_all
from src.html import generate_html
from src.metrics import compute_fund_metrics
from src.sig_lens import compute_sig_lens
from src.sig_plan_charts import build_panel_html as build_9sig_panel
from src.signals import compute_signals
from src.summary import build_summary_html, build_summary_prompt, call_gemini
from src.universe import collect_proxy_symbols, load_universe
from src.upload import upload_to_s3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the Leveraged ETF Radar dashboard")
    parser.add_argument("--upload", action="store_true", help="Upload to S3 after generating")
    parser.add_argument("--output", default=str(DIST_DIR), help="Output directory")
    parser.add_argument("--no-summary", action="store_true", help="Skip LLM market summary")
    parser.add_argument("--no-holdings", action="store_true", help="Skip live issuer holdings scrapes")
    parser.add_argument("--refresh-cache", action="store_true", help="Bypass on-disk Yahoo cache")
    parser.add_argument("--limit", type=int, default=None, help="Only process first N funds (dev)")
    args = parser.parse_args()

    use_cache = not args.refresh_cache

    funds = load_universe()
    if args.limit is not None:
        funds = funds[: args.limit]
    logger.info(f"Universe: {len(funds)} funds")

    proxies = collect_proxy_symbols(funds)
    logger.info(f"Unique proxy symbols: {len(proxies)}")

    fund_tickers = [f.ticker for f in funds]
    logger.info(f"Fetching {len(fund_tickers)} fund symbols from Yahoo...")
    fund_data = fetch_many(fund_tickers, years_back=11, use_cache=use_cache)

    logger.info(f"Fetching {len(proxies)} proxy symbols from Yahoo...")
    proxy_data = fetch_many(proxies, years_back=11, use_cache=use_cache)

    fund_ok = sum(1 for s in fund_data.values() if not s.empty)
    proxy_ok = sum(1 for s in proxy_data.values() if not s.empty)
    logger.info(f"Data fetched: {fund_ok}/{len(fund_tickers)} funds, {proxy_ok}/{len(proxies)} proxies")

    logger.info("Computing per-fund metrics...")
    # Pull volume from the same cached chart payloads (zero extra fetches).
    volume_data = {tk: fetch_yahoo_volume(tk) for tk in fund_tickers}
    metrics = {}
    for f in funds:
        proxy_s = proxy_data.get(f.proxy_symbol) if f.proxy_symbol else None
        metrics[f.ticker] = compute_fund_metrics(
            f.ticker, fund_data.get(f.ticker), proxy_s, f.leverage,
            volume_series=volume_data.get(f.ticker),
        )

    logger.info("Computing per-fund signals + TQQQ watch context...")
    signals = {
        f.ticker: compute_signals(
            f.ticker,
            fund_data.get(f.ticker),
            actual_minus_simulated_1y=metrics[f.ticker].actual_minus_simulated_1y,
        )
        for f in funds
    }
    sig_lenses = {
        f.ticker: compute_sig_lens(f.ticker, fund_data.get(f.ticker), f.leverage, f.direction)
        for f in funds
    }

    logger.info("Resolving holdings (live=%s)...", not args.no_holdings)
    holdings = resolve_all(funds, live=not args.no_holdings)

    summary_html = ""
    if not args.no_summary:
        logger.info("Generating LLM summary...")
        prompt = build_summary_prompt(
            metrics, {f.ticker: f for f in funds},
            signals=signals, sig_lenses=sig_lenses,
        )
        raw = call_gemini(prompt)
        if raw:
            summary_html = build_summary_html(raw)
            logger.info(f"  Summary generated ({len(raw)} chars)")
        else:
            logger.warning("  Summary skipped")

    logger.info("Building 9Sig panel...")
    nine_sig_html = ""
    try:
        tqqq_series = fund_data.get("TQQQ")
        tqqq_px = float(tqqq_series.iloc[-1]) if tqqq_series is not None and not tqqq_series.empty else None
        agg_series = fetch_yahoo_series("AGG", years_back=1, use_cache=use_cache)
        agg_px = float(agg_series.iloc[-1]) if not agg_series.empty else None
        if tqqq_px and agg_px:
            update_label = f"TQQQ ${tqqq_px:.2f} / AGG ${agg_px:.2f} live"
            nine_sig_html = build_9sig_panel(
                current_tqqq_price=tqqq_px,
                current_agg_price=agg_px,
                update_label=update_label,
            )
            logger.info(f"  9Sig panel: TQQQ ${tqqq_px:.2f}, AGG ${agg_px:.2f}")
        else:
            logger.warning("  9Sig panel: missing TQQQ or AGG price; skipping")
    except Exception as e:
        logger.warning(f"  9Sig panel: build failed ({e}); skipping")

    logger.info("Fetching 9Sig adjusted close series...")
    nine_sig_adjusted_data = {
        "TQQQ": fetch_yahoo_adjusted_series("TQQQ", years_back=11, use_cache=use_cache),
        "AGG": fetch_yahoo_adjusted_series("AGG", years_back=11, use_cache=use_cache),
    }
    nine_sig_adjusted_ok = sum(1 for s in nine_sig_adjusted_data.values() if not s.empty)
    logger.info(f"  9Sig adjusted series: {nine_sig_adjusted_ok}/2 available")

    logger.info("Generating HTML...")
    html = generate_html(
        funds=funds, metrics=metrics,
        fund_data=fund_data, proxy_data=proxy_data,
        holdings=holdings, summary_html=summary_html,
        signals=signals, sig_lenses=sig_lenses,
        nine_sig_panel=nine_sig_html,
        nine_sig_adjusted_data=nine_sig_adjusted_data,
    )

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "index.html"
    out_path.write_text(html)
    logger.info(f"Dashboard written to {out_path} ({len(html):,} bytes)")

    if args.upload:
        upload_to_s3(out_path)

    logger.info("Done.")


if __name__ == "__main__":
    sys.exit(main())
