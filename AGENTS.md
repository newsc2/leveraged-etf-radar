# Leveraged ETF Radar — Agent Brief

Shared brief for any agent (Claude, Codex). For deeper notes see `CLAUDE.md`.

## What it is
Static dashboard tracking the **full U.S.-listed leveraged equity ETF/ETN universe** (~120+ funds), deployed to `newsc2.com/projects/leveraged-etf-radar/app/`. Generates a single self-contained HTML file with interactive Plotly charts from Yahoo Finance + a curated `data/funds.yaml` universe + best-effort issuer holdings scrapes. No server, no DB.

Modeled on `macro-dashboard-v3` — same Python static-generator pattern, same single-file HTML output, same theme.

## Stack
- Python 3.10+ (raw `requests`, no yfinance).
- Plotly 5.18+ via CDN (`plotly-2.35.2.min.js`).
- PyYAML for the universe file.
- Light theme matching newsc2.com (Inter font, `#FAFAFA` bg).

## Layout
```
export_static.py           CLI wrapper
src/
  config.py                Colors, S3 settings, theme
  data.py                  Yahoo Finance fetch + disk cache
  universe.py              Loads + validates data/funds.yaml
  metrics.py               Returns, drawdown, beta, simulated daily-reset
  holdings.py              Issuer holdings parsers (ProShares, Direxion, GraniteShares)
  charts.py                Plotly chart builders
  html.py                  HTML template + assembly
  summary.py               LLM summary (Gemini Flash, optional)
  upload.py                S3 upload via AWS CLI
data/
  funds.yaml               Curated LETF universe
cache/                     Disk cache of Yahoo responses (gitignored)
tests/                     Test suite with fixtures
dist/index.html            Generated output
```

## Run
```bash
python export_static.py                                # local
python export_static.py --upload                       # + S3
python export_static.py --no-summary                   # skip LLM
python export_static.py --no-holdings                  # skip live issuer scrapes
python export_static.py --refresh-cache                # bypass disk cache

# Quality
pytest tests/ -v
mypy src/ --strict
ruff check src/ tests/
```

## Env
- `GOOGLE_AI_API_KEY` (optional, Gemini Flash for `--summary`).
- `BRAVE_SEARCH_API_KEY` (optional, headlines for summary).
- AWS credentials for `--upload`.

## Deploy target
- `s3://newsc2.com/projects/leveraged-etf-radar/app/index.html` (max-age=300).

## Product model — IMPORTANT
Leveraged ETFs have **two distinct composition layers**. The dashboard must NOT blur them:

1. **Actual fund holdings** — swaps, futures, T-bills, cash, collateral, direct equities (what's in the trust).
2. **Economic exposure** — the target index/stock and major components of that target.

The Composition panel labels each row with `confidence`:
- `issuer_native_actual_holdings` (high — scraped from issuer page)
- `issuer_native_index_components` (high — issuer-published index breakdown)
- `proxy_etf_holdings` (medium — fallback to proxy ETF top holdings)
- `manual_target_only` (low — only the target name is known)

## Universe (`data/funds.yaml`)
~120 U.S.-listed leveraged equity ETFs/ETNs. Fields:
`ticker, name, issuer, product_type (ETF|ETN), direction (long|inverse), leverage (±1.5/2/3), exposure_type, target_name, target_symbol_or_index, proxy_symbol, inception_date, expense_ratio, metadata_source, notes`. Missing values are `null`, never guessed.

Exposure types: `broad_index, sector, industry, country, single_stock, crypto_equity_related, thematic`.

## Known traps
- **YTD baseline:** Use previous year's last close (Dec 31), NOT first trading day. See `_get_prev_year_close()`. **Critical for LETFs** — small baseline drift compounds 3×.
- **Plotly hex alpha:** Plotly does NOT accept 8-digit hex (e.g., `#16a34a1A`). Use `rgba(r,g,b,alpha)` via the `_hex_to_rgba()` helper.
- **Yahoo Finance:** uses `query1.finance.yahoo.com/v8/finance/chart/` directly. Timestamps are Unix epoch, prices in `indicators.quote[0].close`. ~150 symbol fetches → cache to `cache/` to avoid hammering.
- **Astro route vs `public/` collision:** `/projects/leveraged-etf-radar/` (newsc2.com Astro page) MUST redirect to `/projects/leveraged-etf-radar/app/` so it doesn't collide with the S3-uploaded HTML.
- **`aws s3 sync --delete` removes cron-uploaded files.** Add `--exclude "projects/leveraged-etf-radar/app/*"` to the newsc2.com deploy.sh sync.
- **Favicon policy:** ALL projects on newsc2.com MUST include `<link rel="icon" type="image/svg+xml" href="/favicon.svg" />`. No exceptions.
- **LETF leverage changes:** Some funds (e.g., NVDL) changed multiples mid-life. Note in `notes` field; don't compare current leverage × historical proxy returns naively for those tickers.
- **Naive cumulative leverage ≠ daily-reset simulation.** Always compute both — that gap (compounding decay) is the whole point of the dashboard.

## Holdings scraping (best-effort, with confidence labels)
- ProShares: in-page holdings parser (e.g., UPRO).
- Direxion: index top-holdings + sector-weight blocks (e.g., SPXL).
- GraniteShares: detect downloadable holdings file links (e.g., NVDL).
- Otherwise fall back to target/proxy metadata.

If a parser fails, downgrade confidence; never fabricate holdings.

---

## Reference: Jason Kelly 9Sig
`docs/jason-kelly/` contains offline notes on Kelly's 3Sig/6Sig/**9Sig** quarterly-rebalance plans (TQQQ + AGG, 9% target, 60/40 base, with 30-Down / spike-reset / buying-power-throttle override rules). Tony loosely follows 9Sig.

`src/sig_plans.py` is a pure-function engine implementing all four override rules. Run `python -m src.sig_plans` for a live what-if against the latest published Kelly state. Tests in `tests/test_sig_plans.py` (9 cases, each maps to a real historical scenario from `docs/jason-kelly/9sig-history.md`). Spike-reset rule has an interpretation ambiguity in Kelly's user guide — see comment in `compute_rebalance()`. Not yet wired into the dashboard HTML; that's a follow-up.

## How we coordinate (Claude ↔ Codex)
- **`AGENTS.md`** (this file) is the shared brief. Both agents read & maintain it. Keep it terse.
- **`CLAUDE.md`** is Claude's private scratch. Codex doesn't touch it.
- **Commit when a logical chunk is done** — git is the sync layer between us.
- If you fix something the other agent got wrong, note the correction in the commit message.
