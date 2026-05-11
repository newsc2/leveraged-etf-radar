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

## Jason Kelly 9Sig — engine + dashboard panel
`docs/jason-kelly/` contains offline notes on Kelly's 3Sig/6Sig/**9Sig** quarterly-rebalance plans (TQQQ + AGG, 9% target, 60/40 base, with 30-Down / spike-reset / buying-power-throttle override rules). Tony loosely follows 9Sig.

**Modules:**
- `src/sig_plans.py` — pure-function engine, all four override rules. Run `python -m src.sig_plans` for live what-if. Tests in `tests/test_sig_plans.py` (9 cases mapping to real historical scenarios).
- `src/sig_plan_charts.py` — Plotly charts (`build_allocation_chart`, `build_dollar_balance_chart`) + `build_panel_html()` for the dashboard panel.
- `src/sig_lens.py` — Codex's earlier read-only analytical lens (signal/gap calc, no orders). Complementary, not duplicative.

**State files:**
- `data/sig_plans/9sig_quarterly_history.json` — 38 quarters of post-rebalance balances (verified against Kelly's published page to the dollar).
- `data/sig_plans/9sig_current_state.json` — latest Kelly snapshot (shares, prior_goal, last fill price, 30-Down state). Refresh weekly when Kelly's Sunday note arrives.

**Dashboard panel:** A 9Sig table + KPI strip injected near the top of the dashboard (between intro-block and filter-bar). Auto-builds on every `python export_static.py` run using live TQQQ + AGG closes from Yahoo Finance. Verified against Kelly Note 19 (2026-05-10) to the dollar.

**Known nuances** (kept the engine simple by design):
- 30-Down threshold check fires at exactly 30.0% drawdown; Kelly tolerates the boundary (his strict `> 30%` semantics or excluding very recent quarters).
- `next_goal()` formula uses `prior_goal × 1.09 + 0.5 × dividends`, which matches Kelly for normal sell quarters. Skip/throttle/base-reset quarters use catch-up rules (post-rebal actual × 1.09); engine sidesteps by re-seeding `prior_goal` from each new Kelly note.
- Spike-reset baseline is the **rebalance fill price** (not Q-1 close). Stored in `PlanState.last_rebalance_fill_price`.

**TQQQ split history** (Yahoo applies retroactively, breaks naive comparisons):
- 3:1 on 5/24/2018, 2:1 on ~1/13/2022, **2:1 in Q4 2025** (between 9/29/25 and 1/5/26 — discovered via share count doubling).

## Auto-refresh on Mac Mini
A copy of the project lives on the Mac Mini (`macmini:Projects/leveraged-etf-radar`, mirrored via rsync from MBP). The Mac Mini owns the publish cadence — the MBP is for development.

**Wrapper:** `scripts/refresh-dashboard.sh` — sets a sane PATH, sources `~/.zshenv` for API keys, runs `export_static.py --upload --no-summary`, logs to `logs/dashboard-refresh.log`.

**Cron:** weekdays only, ET market hours, every 30 min plus market-close print:
```
30 9 * * 1-5    /Users/newsc2/Projects/leveraged-etf-radar/scripts/refresh-dashboard.sh
0,30 10-15 * * 1-5  /Users/newsc2/Projects/leveraged-etf-radar/scripts/refresh-dashboard.sh
0 16 * * 1-5    /Users/newsc2/Projects/leveraged-etf-radar/scripts/refresh-dashboard.sh
```
That's 14 runs/day: 9:30, 10:00, 10:30, …, 15:30, 16:00 ET. The 9Sig timestamp in the panel header shows the actual ET run time.

**To re-sync MBP → Mac Mini after a code change:**
```bash
rsync -az --delete \
  --exclude=.venv --exclude=cache --exclude=dist --exclude=__pycache__ \
  --exclude=.mypy_cache --exclude=.pytest_cache --exclude=.ruff_cache \
  --exclude=.DS_Store --exclude=logs \
  ./ macmini:Projects/leveraged-etf-radar/
```

**Notes:**
- `--no-summary` keeps cron runs fast (~30s) and skips Gemini calls. Enable in cron if you want hourly LLM summaries — currently the summary updates only when you run `export_static.py --upload` manually from MBP.
- macOS cron runs in system TZ (`America/New_York`) so hours above are ET; DST handled by the OS.
- **NYSE holidays:** the wrapper hard-codes 2026 + 2027 full-day NYSE closings and exits early on those dates (logs a one-line skip notice). Refresh the list when 2028 dates are needed; missing entries just cause one wasted refresh, never breakage.

## How we coordinate (Claude ↔ Codex)
- **`AGENTS.md`** (this file) is the shared brief. Both agents read & maintain it. Keep it terse.
- **`CLAUDE.md`** is Claude's private scratch. Codex doesn't touch it.
- **Commit when a logical chunk is done** — git is the sync layer between us.
- If you fix something the other agent got wrong, note the correction in the commit message.
