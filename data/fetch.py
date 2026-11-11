"""Download the MTA datasets this project uses from data.ny.gov (Socrata API).

Usage (from the repo root):
    python data/fetch.py                      # all datasets, default --since
    python data/fetch.py --since 2025-01-01   # change the start date for big datasets
    python data/fetch.py --only ticket_sales  # just one dataset

Output: data/raw/<key>.csv plus data/raw/<key>_meta.json (column definitions,
dataset title, and when it was downloaded). data/raw/ is git-ignored.

Optional: set SOCRATA_APP_TOKEN in your environment (free at data.ny.gov ->
Developer Settings) to avoid throttling. It is never required.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://data.ny.gov"
RAW_DIR = Path(__file__).resolve().parent / "raw"
PAGE_SIZE = 50_000

# key -> dataset info. "filter_by_date" = only download rows on/after --since
# (used for the large datasets so the download stays manageable).
DATASETS: dict[str, dict] = {
    "ticket_sales": {
        "id": "5ypk-fceg",
        "title": "MTA Railroad Ticket Sales: Beginning 2023",
        "filter_by_date": True,
    },
    "daily_ridership": {
        "id": "sayj-mze2",
        "title": "MTA Daily Ridership and Traffic: Beginning 2020",
        "filter_by_date": False,
    },
    "monthly_ridership": {
        "id": "xfre-bxip",
        "title": "MTA Monthly Ridership / Traffic Data: Beginning January 2008",
        "filter_by_date": False,
    },
    "stop_occupancy": {
        "id": "hb2b-cimm",
        "title": "MTA LIRR Stop-Level Occupancy: Beginning January 2024",
        "filter_by_date": True,
    },
}

DATE_TYPES = {"calendar_date", "floating_timestamp", "date"}


def session() -> requests.Session:
    s = requests.Session()
    token = os.environ.get("SOCRATA_APP_TOKEN")
    if token:
        s.headers["X-App-Token"] = token
    return s


def get_with_retry(s: requests.Session, url: str, params: dict | None = None) -> requests.Response:
    """GET with retries and exponential backoff (2s, 4s, 8s, 16s)."""
    for attempt in range(5):
        try:
            r = s.get(url, params=params, timeout=120)
            if r.status_code == 200:
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
            if attempt == 4:
                raise
            wait = 2 ** (attempt + 1)
            print(f"    retry in {wait}s ({e})")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def fetch_metadata(s: requests.Session, dataset_id: str) -> dict:
    """Column names/types straight from the portal, so we never guess the schema."""
    return get_with_retry(s, f"{BASE}/api/views/{dataset_id}.json").json()


def find_date_column(meta: dict) -> str | None:
    for col in meta.get("columns", []):
        if col.get("dataTypeName") in DATE_TYPES and not col["fieldName"].startswith(":"):
            return col["fieldName"]
    return None


def fetch_dataset(s: requests.Session, key: str, info: dict, since: str) -> None:
    print(f"\n== {key}: {info['title']} ({info['id']})")
    meta = fetch_metadata(s, info["id"])

    where = None
    date_col = find_date_column(meta)
    if info["filter_by_date"]:
        if date_col:
            where = f"{date_col} >= '{since}T00:00:00'"
            print(f"   filtering: {where}")
        else:
            print("   WARNING: no date column found, downloading everything")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_csv = RAW_DIR / f"{key}.csv"
    tmp_csv = out_csv.with_suffix(".csv.part")

    total = 0
    offset = 0
    with open(tmp_csv, "w", encoding="utf-8", newline="") as f:
        while True:
            params = {"$limit": PAGE_SIZE, "$offset": offset, "$order": ":id"}
            if where:
                params["$where"] = where
            text = get_with_retry(s, f"{BASE}/resource/{info['id']}.csv", params).text
            lines = text.splitlines(keepends=True)
            if not lines:
                break
            header, rows = lines[0], lines[1:]
            if offset == 0:
                f.write(header)
            f.writelines(rows)
            # Rows can contain quoted newlines, so line count is approximate;
            # a short page still reliably means "last page".
            total += len(rows)
            print(f"   {total:,} rows...", end="\r")
            if len(rows) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
    tmp_csv.replace(out_csv)
    print(f"   saved {out_csv.relative_to(RAW_DIR.parent.parent)} (~{total:,} rows)")

    (RAW_DIR / f"{key}_meta.json").write_text(
        json.dumps(
            {
                "key": key,
                "dataset_id": info["id"],
                "title": meta.get("name", info["title"]),
                "url": f"{BASE}/d/{info['id']}",
                "description": meta.get("description", ""),
                "date_column": date_col,
                "row_filter": where,
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "columns": [
                    {
                        "field": c["fieldName"],
                        "name": c.get("name"),
                        "type": c.get("dataTypeName"),
                        "description": c.get("description", ""),
                    }
                    for c in meta.get("columns", [])
                ],
            },
            indent=2,
        )
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--since", default="2025-01-01", help="start date for large datasets (YYYY-MM-DD)")
    p.add_argument("--only", choices=list(DATASETS), action="append", help="fetch only these keys")
    args = p.parse_args()

    s = session()
    for key in args.only or DATASETS:
        fetch_dataset(s, key, DATASETS[key], args.since)
    print("\nDone. Next: python data/build_db.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())