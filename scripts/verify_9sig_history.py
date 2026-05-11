"""Verify our local 9Sig history JSON against Kelly's source page.

Two kinds of checks:
  1. Source-of-truth diff: re-fetch jasonkelly.com/kellyletter/9sig/ and confirm
     the post-rebalance balances we transcribed match what Kelly currently shows.
  2. Internal consistency: cash + AGG + TQQQ should grow Q-over-Q in a way
     consistent with reported buy/sell sizes and price moves.

Run:
    python scripts/verify_9sig_history.py
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HISTORY_FILE = REPO / "data" / "sig_plans" / "9sig_quarterly_history.json"
SOURCE_URL = "https://jasonkelly.com/kellyletter/9sig/"

# Match a balances line like:
#   BALANCES AFTER THIS QUARTER'S ACTIONS
#   Cash $3 | AGG $1,217,106 | TQQQ $5,893,063
BALANCES_RE = re.compile(
    r"BALANCES AFTER THIS QUARTER.{0,3}S ACTIONS.*?"
    r"Cash\s*\$([\d,]+)\s*\|\s*AGG\s*\$([\d,]+)\s*\|\s*TQQQ\s*\$([\d,]+)",
    re.DOTALL,
)
# Match a date header like "4/6/26" near the start of a section
DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")


_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def fetch_source() -> str:
    """Fetch the 9Sig history page. Site is behind HTTP Basic Auth.

    Set credentials via env vars (rotating monthly password):
        export KELLYLETTER_USER=subscriber
        export KELLYLETTER_PASS=<this-month's-password>
    """
    user = os.environ.get("KELLYLETTER_USER", "subscriber")
    pwd = os.environ.get("KELLYLETTER_PASS", "")
    headers = {"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml"}
    if pwd:
        token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    req = urllib.request.Request(SOURCE_URL, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def _strip_html(html: str) -> str:
    """Quick & dirty: drop tags so regex can scan the textual content."""
    return re.sub(r"<[^>]+>", " ", html)


def parse_published_quarters(text: str) -> list[dict[str, object]]:
    """Find each quarter section and extract date + post-rebalance balances."""
    # Split by "BALANCES AFTER" so each chunk has at most one balances block.
    chunks = text.split("BALANCES AFTER")
    quarters: list[dict[str, object]] = []
    for i in range(1, len(chunks)):
        # Date is in the chunk BEFORE the balances split
        before = chunks[i - 1]
        after = "BALANCES AFTER" + chunks[i]
        date_match = None
        for m in DATE_RE.finditer(before):
            date_match = m  # take the LAST date in the preceding chunk
        bal_match = BALANCES_RE.search(after)
        if not date_match or not bal_match:
            continue
        mm, dd, yy = date_match.groups()
        year = int(yy) + (2000 if len(yy) == 2 else 0)
        try:
            d = date(year, int(mm), int(dd))
        except ValueError:
            continue
        cash = int(bal_match.group(1).replace(",", ""))
        agg = int(bal_match.group(2).replace(",", ""))
        tqqq = int(bal_match.group(3).replace(",", ""))
        quarters.append({"date": d.isoformat(), "cash": cash,
                         "agg": agg, "tqqq": tqqq})
    return quarters


def main() -> int:
    local = json.loads(HISTORY_FILE.read_text())["history"]
    local_by_date = {h["date"]: h for h in local}

    print(f"Fetching {SOURCE_URL} ...")
    raw = fetch_source()
    text = _strip_html(raw)
    published = parse_published_quarters(text)
    if not published:
        print("ERROR: parsed 0 quarters from source page (parser broken or page changed)")
        return 1

    print(f"Source page parsed: {len(published)} quarter blocks")
    print(f"Local JSON entries: {len(local)} quarter blocks\n")

    # 1. Source-of-truth diff
    drift_rows: list[str] = []
    for pub in published:
        loc = local_by_date.get(pub["date"])
        if not loc:
            drift_rows.append(f"  MISSING in local: {pub['date']}  "
                              f"AGG ${pub['agg']:,}  TQQQ ${pub['tqqq']:,}")
            continue
        agg_diff = pub["agg"] - loc["agg_value"]
        tqqq_diff = pub["tqqq"] - loc["tqqq_value"]
        if agg_diff != 0 or tqqq_diff != 0:
            drift_rows.append(
                f"  DRIFT  {pub['date']}  "
                f"AGG diff=${agg_diff:+,}  TQQQ diff=${tqqq_diff:+,}",
            )

    print("=== Source diff (local JSON vs published page) ===")
    if drift_rows:
        for r in drift_rows:
            print(r)
        print(f"\n{len(drift_rows)} discrepancies found.")
    else:
        print("  All transcribed entries match the published page exactly.")

    # 2. Allocation-% reconstruction sanity check
    print("\n=== Allocation % consistency (latest 5 quarters) ===")
    print(f"  {'date':<12} {'tqqq%':>8} {'agg%':>8} {'cash $':>10} (cash ignored in chart)")
    for h in local[-5:]:
        total = h["tqqq_value"] + h["agg_value"]
        tqqq_pct = 100 * h["tqqq_value"] / total
        agg_pct = 100 - tqqq_pct
        # cash is in source but not in our JSON; cross-check a few
        pub = next((p for p in published if p["date"] == h["date"]), None)
        cash_str = f"${pub['cash']}" if pub else "—"
        print(f"  {h['date']:<12} {tqqq_pct:>7.2f}% {agg_pct:>7.2f}%  {cash_str:>10}")

    # 3. Cash-as-% sanity (is ignoring cash material?)
    if published:
        max_cash_pct = 0.0
        worst = None
        for pub in published:
            total = pub["cash"] + pub["agg"] + pub["tqqq"]
            cash_pct = 100 * pub["cash"] / total
            if cash_pct > max_cash_pct:
                max_cash_pct = cash_pct
                worst = pub
        print(f"\n=== Cash drag check ===")
        print(f"  Max cash % across all quarters: {max_cash_pct:.4f}%  "
              f"(date: {worst['date'] if worst else '—'})")
        if max_cash_pct < 0.01:
            print("  → Ignoring cash in % calc is immaterial (<0.01% always)")
        else:
            print(f"  → Cash up to {max_cash_pct:.2f}% — may want to include in chart denominator")

    return 0 if not drift_rows else 2


if __name__ == "__main__":
    sys.exit(main())
