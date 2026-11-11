"""Tools that tell the agent what data exists: list_tables and describe_table."""

from __future__ import annotations

import copy
import threading

from .db import connection, db_path, to_json_value

# The data doesn't change while the app runs, so each answer is computed once
# per database file (describe_table on the 17M-row ticket table takes seconds).
_cache: dict[tuple, dict] = {}
_cache_lock = threading.Lock()


def _cached(key: tuple, compute) -> dict:
    full_key = (str(db_path()),) + key
    with _cache_lock:
        hit = _cache.get(full_key)
    if hit is None:
        hit = compute()
        if "error" not in hit:
            with _cache_lock:
                _cache[full_key] = hit
    return copy.deepcopy(hit)

# Facts about each table that the column names alone don't tell the model.
# Keep these short and factual; the agent reads them before writing SQL.
TABLE_NOTES: dict[str, list[str]] = {
    "ticket_sales": [
        "Daily ticket sales for BOTH railroads: always filter railroad = 'LIRR'.",
        "number_of_tickets is a count of tickets sold. There is NO revenue/price column.",
        "sold_as = what kind of ticket (one_way, round_trip, day_pass, monthly, weekly, ten_trip, *_bundled).",
        "fare_type = peak / off_peak / half (senior, disabled, Medicare) / intermediate / family.",
        "device_class_type = where it was bought: mobile (TrainTime app), TVM (ticket machine), "
        "TOM (ticket office); OBTIM/TIM/STIM are likely conductor/onboard devices, but the dataset "
        "does not document them, so say so if a claim depends on it.",
        "origin_station_zone / destination_station_zone are LIRR fare zones (1, 3, 4, 7, 9, 10, 12, 14); "
        "join to lirr_fares on CAST(zone AS VARCHAR).",
        "Covers 2025-01-01 to 2026-09-30 in this database. The fare change took effect 2026-01-04.",
        "Counting quirk: ten_trip rows count RIDES (10 per ten-trip ticket; every count is a multiple "
        "of 10) and round trips are mostly counted per leg, so counts are closer to rides than purchases.",
        "For day passes, ticket_type says 'Day Pass Weekday' or 'Day Pass Weekend'.",
        "17M+ rows: aggregate (GROUP BY) instead of selecting raw rows.",
    ],
    "daily_ridership": [
        "Daily estimated ridership for every MTA mode: filter mode = 'LIRR'.",
        "count = riders that day (for mode = 'LIRR').",
    ],
    "monthly_ridership": [
        "Monthly ridership per agency since 2008: filter agency = 'LIRR'.",
        "month is the first day of the month.",
    ],
    "stop_occupancy": [
        "LIRR only. One row per stop per trip, M7 and M9 trains only (not diesel or other fleets).",
        "total_passengers = people on board when leaving that stop; train_occupancy = total_passengers / train_capacity.",
        "Rows with missing_load_data > 0 have incomplete counts; consider excluding them.",
        "Covers 2025-01-01 to 2026-09-30 in this database. ~5M rows: aggregate.",
    ],
    "lirr_fares": [
        "LIRR station fares in two periods: fare_period = 'pre_2026' (effective 2023-08-20, in force until "
        "2026-01-03) and 'post_2026' (effective 2026-01-04). Join the two on origin_zone, dest_zone, "
        "ticket_type to compute price changes.",
        "post_2026 covers every zone pair. pre_2026 covers every pair to/from zone 1 (84% of 2025 LIRR "
        "tickets) plus 3<->3, 3<->4, 3<->7, 4<->7, 3<->9, 4<->9, 7<->9, 3<->10, 4<->10, 7<->10.",
        "Zone pairs are symmetric (1->7 costs the same as 7->1). Zones are stored as text.",
        "ticket_type values: monthly, weekly, one_way_peak, one_way_offpeak, one_way_senior_disabled; "
        "post_2026 only: day_pass_weekday, day_pass_weekend, onboard_one_way_peak, onboard_one_way_offpeak; "
        "pre_2026 only: ten_trip_peak, ten_trip_offpeak, ten_trip_senior_disabled.",
        "Within New York City (zones 1/3 to zone 1) pre_2026 one-way prices are CityTicket fares.",
        "To price ticket_sales: zones join as CAST(origin_station_zone AS VARCHAR) = origin_zone; map "
        "sold_as/fare_type to ticket_type: monthly->monthly, weekly->weekly, one_way+peak->one_way_peak, "
        "one_way+off_peak->one_way_offpeak, one_way+half->one_way_senior_disabled, ten_trip+peak->"
        "ten_trip_peak, ten_trip+off_peak->ten_trip_offpeak, ten_trip+half->ten_trip_senior_disabled. "
        "No chart lists round-trip or day-pass-by-fare_type prices directly; state any assumption.",
    ],
    "ticket_spend_est": [
        "ESTIMATED spending per day, ticket kind and zone pair: LIRR ticket_sales priced with lirr_fares "
        "(built offline by data/build_db.py). Use it for 'what riders pay/spend' questions.",
        "est_spend_usd = units_priced x price_usd, at the fares in force that day (fare_period: pre_2026 "
        "before 2026-01-04, post_2026 from then). NULL when no price matches; always report the share of "
        "tickets priced, e.g. sum(tickets) FILTER (WHERE price_usd IS NOT NULL) / sum(tickets).",
        "Rules: ten_trip units_priced = tickets / 10 (the data counts rides) at the ten-trip price; "
        "round trips are priced as one one-way fare per counted leg; day passes use the weekday or "
        "weekend price; family and intermediate fares and zone pairs missing from the 2023 chart are unpriced.",
        "Feb-Sep spending: 2025 about 94% of tickets priced, 2026 about 97%. Not actual MTA revenue "
        "(no discounts, refunds or unused tickets).",
    ],
    "sources": [
        "Where each table came from (data.ny.gov dataset id and URL) and when it was downloaded. Use for citations.",
    ],
}

MAX_LISTED_VALUES = 25


def list_tables() -> dict:
    """Every table with its row count, source dataset, and short notes."""
    return _cached(("list_tables",), _list_tables)


def _list_tables() -> dict:
    cur = connection().cursor()
    names = [r[0] for r in cur.execute("SHOW TABLES").fetchall()]
    src = {
        r[0]: {"title": r[1], "url": r[2], "row_filter": r[3], "fetched_at": r[4]}
        for r in cur.execute(
            "SELECT table_name, title, url, row_filter, fetched_at FROM sources"
        ).fetchall()
    }
    tables = []
    for name in names:
        n = cur.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
        tables.append({
            "table": name,
            "row_count": n,
            "source": src.get(name),
            "notes": TABLE_NOTES.get(name, []),
        })
    return {"tables": tables}


def describe_table(table: str) -> dict:
    """Columns, types, date ranges and the values of small categorical columns."""
    return _cached(("describe_table", table), lambda: _describe_table(table))


def _describe_table(table: str) -> dict:
    cur = connection().cursor()
    known = {r[0] for r in cur.execute("SHOW TABLES").fetchall()}
    if table not in known:
        return {"error": f"Unknown table '{table}'. Known tables: {sorted(known)}"}

    columns = []
    for name, dtype, *_ in cur.execute(f'DESCRIBE "{table}"').fetchall():
        col = {"column": name, "type": dtype}
        qn = '"' + name.replace('"', '""') + '"'
        if dtype.startswith(("DATE", "TIMESTAMP")):
            lo, hi = cur.execute(f'SELECT min({qn}), max({qn}) FROM "{table}"').fetchone()
            col["min"], col["max"] = to_json_value(lo), to_json_value(hi)
        elif dtype == "VARCHAR":
            k = cur.execute(f'SELECT approx_count_distinct({qn}) FROM "{table}"').fetchone()[0]
            if k <= MAX_LISTED_VALUES:
                col["values"] = [
                    r[0] for r in cur.execute(
                        f'SELECT {qn} FROM "{table}" WHERE {qn} IS NOT NULL '
                        f"GROUP BY 1 ORDER BY count(*) DESC LIMIT {MAX_LISTED_VALUES}"
                    ).fetchall()
                ]
            else:
                col["approx_distinct"] = k
        columns.append(col)
    return {"table": table, "columns": columns, "notes": TABLE_NOTES.get(table, [])}
