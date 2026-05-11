# Leveraged ETF Radar

Static dashboard tracking the U.S.-listed leveraged equity ETF/ETN universe. Deployed to [newsc2.com/projects/leveraged-etf-radar/app/](https://newsc2.com/projects/leveraged-etf-radar/app/).

Generates a single self-contained `dist/index.html` with interactive Plotly charts. No server, no database.

## What it tracks

- **~120+ U.S.-listed leveraged equity ETFs/ETNs** — long and inverse, 1.5×/2×/3×, broad indexes, sectors, single stocks, thematics.
- **Two composition layers** kept distinct:
  1. *Actual fund holdings* — swaps, futures, T-bills, collateral.
  2. *Economic exposure* — the target index/stock and its components.
- **Tracking math** — actual LETF return vs naive `leverage × proxy` vs simulated daily-reset compounding.
- **Risk metrics** — realized volatility, max drawdown, rolling 20d/60d beta and correlation vs proxy.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

python export_static.py                # build dist/index.html
open dist/index.html
```

## Flags

| Flag | Purpose |
|------|---------|
| `--upload` | After build, push `dist/index.html` to `s3://newsc2.com/projects/leveraged-etf-radar/app/`. |
| `--output PATH` | Write to a custom directory. |
| `--no-summary` | Skip the LLM market summary section. |
| `--no-holdings` | Skip live issuer holdings scrapes (uses proxy fallback only). |
| `--refresh-cache` | Bypass `cache/` and re-fetch all Yahoo Finance data. |
| `--limit N` | Only process the first N funds (development convenience). |

## Environment variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `GOOGLE_AI_API_KEY` | No | Gemini Flash key — enables LLM market summary. |
| `BRAVE_SEARCH_API_KEY` | No | Brave Search key — feeds market headlines into the summary. |
| AWS credentials | for `--upload` | Standard AWS CLI credentials. |

## Quality

```bash
pytest tests/ -v        # full test suite with mocks
ruff check src/ tests/  # lint
mypy src/ --strict      # types
```

## Universe data

`data/funds.yaml` is the curated universe. To add a fund, append an entry with at least `ticker`, `name`, `issuer`, `product_type`, `direction`, `leverage`, `exposure_type`, `target_name`. Missing fields should be `null`, not guessed.

See [`AGENTS.md`](AGENTS.md) for project conventions and known traps.
