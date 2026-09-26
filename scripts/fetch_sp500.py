"""Download the full S&P 500 daily close history for the Phase 5 replication.

The replication needs the second market on the same footing as the first: a daily
price index, close only, long enough to cover 1996 onwards. Two sources are tried in
order, because neither is a contract:

  stooq.com   one CSV for the whole history of ^SPX, no key, no rate limit stated
  Yahoo       the chart JSON for ^GSPC, used only if stooq refuses

It writes two files, the same pair and the same shape as fetch_nifty50.py:

  data/sp500_raw.csv   every row the source returned, untouched
  data/sp500.csv       the analysis file: weekend sessions removed (D11)

Both are Date,Close with dates written as "03 Jul 1990", matching the Nifty files so
one adapter config reads either.

Usage:
    python3 scripts/fetch_sp500.py [--source stooq|yahoo] [--out-dir data]

Standard library only, so it needs no virtualenv and no installed dependencies.

Run it from an ordinary connection. Both sources refuse data centre addresses, so this
will not work from a sandbox or a VPN, which is why the data file is committed to the
repo rather than fetched by CI.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

STOOQ = "https://stooq.com/q/d/l/?s=%5Espx&i=d"
YAHOO = (
    "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC"
    "?period1=0&period2=9999999999&interval=1d&events=history"
)
DATE_FMT = "%d %b %Y"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
REPO = Path(__file__).resolve().parent.parent


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def from_stooq() -> list[tuple[dt.date, float]]:
    """Stooq serves Date,Open,High,Low,Close,Volume with ISO dates, oldest first."""
    text = _get(STOOQ).decode("utf-8", "replace")
    if "Date" not in text.splitlines()[0]:
        raise RuntimeError(f"stooq did not return a CSV header: {text[:120]!r}")
    out = []
    for row in csv.DictReader(text.splitlines()):
        close = row.get("Close")
        if not close or close in {"null", "N/A"}:
            continue
        out.append((dt.date.fromisoformat(row["Date"]), float(close)))
    return out


def from_yahoo() -> list[tuple[dt.date, float]]:
    """Yahoo's chart JSON: parallel arrays of unix timestamps and adjusted closes."""
    payload = json.loads(_get(YAHOO))
    result = payload["chart"]["result"][0]
    stamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    out = []
    for ts, close in zip(stamps, closes, strict=True):
        if close is None:
            continue
        out.append((dt.datetime.fromtimestamp(ts, tz=dt.UTC).date(), float(close)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", choices=("stooq", "yahoo", "auto"), default="auto")
    ap.add_argument("--out-dir", type=Path, default=REPO / "data")
    args = ap.parse_args()

    order = {"auto": ("stooq", "yahoo"), "stooq": ("stooq",), "yahoo": ("yahoo",)}[args.source]
    rows: list[tuple[dt.date, float]] = []
    for name in order:
        try:
            rows = {"stooq": from_stooq, "yahoo": from_yahoo}[name]()
        except (urllib.error.URLError, OSError, RuntimeError, KeyError, ValueError) as e:
            print(f"{name}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        if rows:
            print(f"{name}: {len(rows)} rows", file=sys.stderr)
            break
    if not rows:
        print("every source failed; nothing written", file=sys.stderr)
        return 1

    by_date = dict(sorted(rows))  # last value wins on a duplicate date
    ordered = sorted(by_date.items())
    args.out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = args.out_dir / "sp500_raw.csv"
    clean_path = args.out_dir / "sp500.csv"

    for path, keep_weekends in ((raw_path, True), (clean_path, False)):
        with path.open("w", newline="\n") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["Date", "Close"])
            for d, close in ordered:
                if keep_weekends or d.weekday() < 5:
                    w.writerow([d.strftime(DATE_FMT), close])

    dropped = sum(1 for d, _ in ordered if d.weekday() >= 5)
    print(
        f"{raw_path}: {len(ordered)} rows, {ordered[0][0]} to {ordered[-1][0]}\n"
        f"{clean_path}: {len(ordered) - dropped} rows ({dropped} weekend sessions dropped)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
