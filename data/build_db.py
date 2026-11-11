"""Load the downloaded CSVs and the hand-built fare table into one DuckDB file.

Usage (from the repo root, after data/fetch.py):
    python data/build_db.py
    python data/build_db.py --fares-only   # reload lirr_fares.csv and rebuild ticket_spend_est only

Creates:
    data/lirr.duckdb   one table per dataset + `lirr_fares` + `ticket_spend_est` + `sources` (git-ignored)
    data/SCHEMA.md     tables, columns, date ranges, common values (commit this:
                       you will paste it into the agent's system prompt later)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

DATA_DIR = Path(__file__).resolve().parent
RAW_DIR = DATA_DIR / "raw"
FARES_CSV = DATA_DIR / "fares" / "lirr_fares.csv"
DB_PATH = DATA_DIR / "lirr.duckdb"
SCHEMA_MD = DATA_DIR / "SCHEMA.md"

FARE_COLUMNS = [
    "fare_period", "effective_date", "origin_zone", "dest_zone",
    "ticket_type", "price_usd", "source_doc",
]
TABLE_KEYS = ["ticket_sales", "daily_ridership", "monthly_ridership", "stop_occupancy"]


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def load_dataset(con: duckdb.DuckDBPyConnection, key: str) -> bool:
    csv_path = RAW_DIR / f"{key}.csv"
    meta_path = RAW_DIR / f"{key}_meta.json"
    if not csv_path.exists():
        print(f"  SKIP {key}: {csv_path} not found (run data/fetch.py)")
        return False

    con.execute(
        f"""CREATE OR REPLACE TABLE {key} AS
            SELECT * FROM read_csv(?, header=true, normalize_names=true,
                                   sample_size=-1, auto_detect=true)""",
        [str(csv_path)],
    )
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    n = con.execute(f"SELECT count(*) FROM {key}").fetchone()[0]
    con.execute(
        "INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?, ?)",
        [key, meta.get("dataset_id"), meta.get("title"), meta.get("url"),
         meta.get("row_filter"), meta.get("fetched_at"), n],
    )
    print(f"  {key}: {n:,} rows")
    return True


def load_fares(con: duckdb.DuckDBPyConnection) -> bool:
    if not FARES_CSV.exists():
        print(f"  SKIP lirr_fares: {FARES_CSV} not found")
        return False
    con.execute(
        """CREATE OR REPLACE TABLE lirr_fares AS
           SELECT * FROM read_csv(?, header=true, all_varchar=true)""",
        [str(FARES_CSV)],
    )
    cols = [r[0] for r in con.execute("DESCRIBE lirr_fares").fetchall()]
    if cols != FARE_COLUMNS:
        sys.exit(f"lirr_fares.csv header must be exactly: {','.join(FARE_COLUMNS)}\n got: {','.join(cols)}")

    n = con.execute("SELECT count(*) FROM lirr_fares").fetchone()[0]
    if n == 0:
        print("  WARNING lirr_fares: header only, no rows yet (fill it in from the MTA fare PDFs)")
    else:
        bad = con.execute(
            """SELECT count(*) FROM lirr_fares
               WHERE fare_period NOT IN ('pre_2026', 'post_2026')
                  OR try_cast(effective_date AS DATE) IS NULL
                  OR try_cast(price_usd AS DECIMAL(8,2)) IS NULL
                  OR coalesce(source_doc, '') = ''"""
        ).fetchone()[0]
        if bad:
            sys.exit(f"lirr_fares.csv has {bad} invalid row(s): check fare_period, "
                     "effective_date (YYYY-MM-DD), price_usd (number) and source_doc")
    # Re-type the columns now that they are validated.
    con.execute(
        """CREATE OR REPLACE TABLE lirr_fares AS SELECT
             fare_period,
             CAST(effective_date AS DATE) AS effective_date,
             origin_zone, dest_zone, ticket_type,
             CAST(price_usd AS DECIMAL(8,2)) AS price_usd,
             source_doc
           FROM lirr_fares"""
    )
    con.execute(
        "DELETE FROM sources WHERE table_name = 'lirr_fares'"
    )
    con.execute(
        "INSERT INTO sources VALUES ('lirr_fares', NULL, 'LIRR fare tables (hand-entered from MTA PDFs)', "
        "NULL, NULL, ?, ?)",
        [datetime.now(timezone.utc).isoformat(timespec="seconds"), n],
    )
    print(f"  lirr_fares: {n:,} rows")
    return True


# How each ticket sale maps to a fare (see SPEND_NOTES in lirr_agent/tools/schema.py).
# Found by inspecting the data: every ten_trip count is a multiple of 10 (the dataset counts
# RIDES, ten per ten-trip ticket), and round trips are mostly counted per leg.
SPEND_SQL = """
CREATE OR REPLACE TABLE ticket_spend_est AS
WITH sales AS (
    SELECT CAST(date AS DATE) AS date, sold_as, fare_type, ticket_type AS sales_ticket_type,
           CAST(origin_station_zone AS VARCHAR) AS origin_zone,
           CAST(destination_station_zone AS VARCHAR) AS dest_zone,
           sum(number_of_tickets) AS tickets
    FROM ticket_sales WHERE railroad = 'LIRR'
    GROUP BY ALL
), mapped AS (
    SELECT *,
        CASE WHEN date < DATE '2026-01-04' THEN 'pre_2026' ELSE 'post_2026' END AS fare_period,
        CASE
            WHEN sold_as IN ('monthly', 'monthly_bundled') THEN 'monthly'
            WHEN sold_as = 'weekly' THEN 'weekly'
            WHEN sold_as = 'ten_trip' THEN
                CASE fare_type WHEN 'peak' THEN 'ten_trip_peak' WHEN 'off_peak' THEN 'ten_trip_offpeak'
                               WHEN 'half' THEN 'ten_trip_senior_disabled' END
            WHEN sold_as = 'day_pass' THEN
                CASE WHEN sales_ticket_type ILIKE '%weekend%' THEN 'day_pass_weekend'
                     WHEN sales_ticket_type ILIKE '%weekday%' THEN 'day_pass_weekday'
                     WHEN dayofweek(date) IN (0, 6) THEN 'day_pass_weekend'
                     ELSE 'day_pass_weekday' END
            WHEN sold_as IN ('one_way', 'one_way_bundled', 'round_trip', 'round_trip_bundled') THEN
                CASE fare_type WHEN 'peak' THEN 'one_way_peak' WHEN 'off_peak' THEN 'one_way_offpeak'
                               WHEN 'half' THEN 'one_way_senior_disabled' END
        END AS price_ticket_type,
        CASE WHEN sold_as = 'ten_trip' THEN tickets / 10.0 ELSE tickets END AS units_priced
    FROM sales
)
SELECT m.date, m.fare_period, m.sold_as, m.fare_type, m.origin_zone, m.dest_zone,
       m.price_ticket_type, m.tickets, m.units_priced, f.price_usd,
       CAST(m.units_priced * f.price_usd AS DOUBLE) AS est_spend_usd
FROM mapped m
LEFT JOIN lirr_fares f
  ON f.fare_period = m.fare_period AND f.origin_zone = m.origin_zone
 AND f.dest_zone = m.dest_zone AND f.ticket_type = m.price_ticket_type
"""


def build_spend(con: duckdb.DuckDBPyConnection) -> bool:
    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if not {"ticket_sales", "lirr_fares"} <= tables:
        print("  SKIP ticket_spend_est: needs ticket_sales and lirr_fares")
        return False
    con.execute(SPEND_SQL)
    n, share = con.execute(
        "SELECT count(*), sum(tickets) FILTER (WHERE price_usd IS NOT NULL) / sum(tickets) FROM ticket_spend_est"
    ).fetchone()
    con.execute("DELETE FROM sources WHERE table_name = 'ticket_spend_est'")
    con.execute(
        "INSERT INTO sources VALUES ('ticket_spend_est', NULL, 'Estimated spending: LIRR ticket_sales x lirr_fares "
        "(built by data/build_db.py)', NULL, NULL, ?, ?)",
        [datetime.now(timezone.utc).isoformat(timespec="seconds"), n],
    )
    print(f"  ticket_spend_est: {n:,} rows, {share:.1%} of tickets priced")
    return True


def describe_table(con: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    """Markdown section: columns, types, date range, and values of small categorical columns."""
    n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    out = [f"## `{table}` ({n:,} rows)", ""]
    src = con.execute("SELECT title, url, row_filter FROM sources WHERE table_name = ?", [table]).fetchone()
    if src and src[0]:
        out.append(f"Source: {src[0]}" + (f" — {src[1]}" if src[1] else ""))
        if src[2]:
            out.append(f"Downloaded rows where: `{src[2]}`")
        out.append("")

    out += ["| column | type | notes |", "|---|---|---|"]
    for name, dtype, *_ in con.execute(f"DESCRIBE {table}").fetchall():
        notes = ""
        if dtype.startswith(("DATE", "TIMESTAMP")):
            lo, hi = con.execute(f"SELECT min({q(name)}), max({q(name)}) FROM {table}").fetchone()
            notes = f"{lo} → {hi}"
        elif dtype == "VARCHAR":
            k = con.execute(f"SELECT count(DISTINCT {q(name)}) FROM {table}").fetchone()[0]
            if k <= 25:
                vals = [r[0] for r in con.execute(
                    f"SELECT {q(name)} FROM {table} WHERE {q(name)} IS NOT NULL "
                    f"GROUP BY 1 ORDER BY count(*) DESC").fetchall()]
                notes = "values: " + ", ".join(f"`{v}`" for v in vals)
            else:
                notes = f"{k:,} distinct"
        out.append(f"| `{name}` | {dtype} | {notes.replace('|', '/')} |")
    out.append("")
    return out


def main() -> int:
    if "--fares-only" in sys.argv:
        con = duckdb.connect(str(DB_PATH))
        print("Reloading lirr_fares in", DB_PATH.name)
        load_fares(con)
        build_spend(con)
        tables = {r[0] for r in con.execute("SELECT table_name FROM sources").fetchall()}
        loaded = [k for k in TABLE_KEYS + ["lirr_fares", "ticket_spend_est"] if k in tables]
    else:
        DB_PATH.unlink(missing_ok=True)
        con = duckdb.connect(str(DB_PATH))
        con.execute(
            """CREATE TABLE sources (table_name VARCHAR, dataset_id VARCHAR, title VARCHAR,
               url VARCHAR, row_filter VARCHAR, fetched_at VARCHAR, row_count BIGINT)"""
        )
        print("Loading tables into", DB_PATH.name)
        loaded = [k for k in TABLE_KEYS if load_dataset(con, k)]
        if load_fares(con):
            loaded.append("lirr_fares")
        if build_spend(con):
            loaded.append("ticket_spend_est")

    lines = [
        "# Database schema",
        "",
        f"Generated by `data/build_db.py` on {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. "
        "Do not edit by hand; rerun the script.",
        "",
    ]
    for t in loaded:
        lines += describe_table(con, t)
    SCHEMA_MD.write_text("\n".join(lines))
    con.close()
    print(f"\nWrote {SCHEMA_MD.name}. Open it and check the columns look right.")
    return 0


if __name__ == "__main__":
    sys.exit(main())