"""Download the full Nifty 50 price index history from niftyindices.com.

The site's own form refuses ranges longer than one year, so this walks the history
one calendar year at a time and stitches the years together. It writes two files:

  data/nifty50_nse_raw.csv  every row the site returns, untouched
  data/nifty50.csv          the analysis file: weekend sessions removed (D11)

Both are Date,Close with dates as NSE serves them, e.g. "03 Jul 1990".

Usage:
    uv run python scripts/fetch_nifty50.py [--start-year 1990] [--end-year 2026]

The endpoint is undocumented and IP restricted: it refuses data centre and VPN
addresses, so run this from an ordinary Indian or consumer connection. If it
returns HTML instead of JSON, the request was rejected, not empty.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PAGE = "https://www.niftyindices.com/reports/historical-data"
ENDPOINT = "https://www.niftyindices.com/BackPage/getHistoricaldatatabletoString"
INDEX = "NIFTY 50"
DATE_FMT = "%d %b %Y"
REPO = Path(__file__).resolve().parent.parent


def _opener() -> urllib.request.OpenerDirector:
    """One opener with a cookie jar: the endpoint wants a session from the page."""
    import http.cookiejar

    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    op.addheaders = [
        ("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"),
        ("Accept", "application/json, text/javascript, */*; q=0.01"),
        ("Referer", PAGE),
        ("X-Requested-With", "XMLHttpRequest"),
    ]
    op.open(PAGE, timeout=30).read()
    return op


def fetch_year(op: urllib.request.OpenerDirector, year: int, attempts: int = 3) -> list[dict]:
    # The endpoint wants a single-quoted object inside a JSON string, not nested JSON.
    window = f"'startDate':'01-Jan-{year}','endDate':'31-Dec-{year}'"
    cinfo = "{" + f"'name':'{INDEX}',{window},'indexName':'{INDEX}'" + "}"
    body = json.dumps({"cinfo": cinfo}).encode()
    req = urllib.request.Request(
        ENDPOINT, data=body, headers={"Content-Type": "application/json; charset=UTF-8"}
    )
    for attempt in range(1, attempts + 1):
        try:
            text = op.open(req, timeout=60).read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == attempts:
                raise SystemExit(f"{year}: request failed after {attempts} tries ({e})") from e
            time.sleep(2 * attempt)
            continue
        stripped = text.lstrip()
        if stripped.startswith("<"):
            if attempt == attempts:
                raise SystemExit(
                    f"{year}: the endpoint returned HTML, so the request was rejected. "
                    f"Run from an ordinary consumer connection, not a data centre or VPN."
                )
            time.sleep(2 * attempt)
            continue
        return json.loads(stripped)
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start-year", type=int, default=1990)
    ap.add_argument("--end-year", type=int, default=dt.date.today().year)
    ap.add_argument("--out-dir", type=Path, default=REPO / "data")
    ap.add_argument("--sleep", type=float, default=0.5, help="pause between years, seconds")
    args = ap.parse_args()

    op = _opener()
    by_date: dict[dt.date, str] = {}
    for year in range(args.start_year, args.end_year + 1):
        rows = fetch_year(op, year)
        for row in rows:
            d = dt.datetime.strptime(row["HistoricalDate"], DATE_FMT).date()
            by_date[d] = row["CLOSE"]
        print(f"{year}: {len(rows):>4} rows (running total {len(by_date)})", file=sys.stderr)
        time.sleep(args.sleep)

    if not by_date:
        raise SystemExit("no rows returned; nothing written")

    ordered = sorted(by_date.items())
    args.out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = args.out_dir / "nifty50_nse_raw.csv"
    clean_path = args.out_dir / "nifty50.csv"

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
