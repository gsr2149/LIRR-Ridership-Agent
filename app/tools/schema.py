"""Tools that tell the agent what data exists: list_tables and describe_table."""

from __future__ import annotations

from .db import connection, to_json_value

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
        "LIRR station fares effective 2026-01-04 ONLY. There are no pre-2026 prices in this database, "
        "so price changes per ticket cannot be computed from it.",
        "Zone pairs are symmetric (1->7 costs the same as 7->1). Zones are stored as text.",
        "ticket_type values: monthly, weekly, one_way_peak, one_way_offpeak, one_way_senior_disabled, "
        "day_pass_weekday, day_pass_weekend, onboard_one_way_peak, onboard_one_way_offpeak.",
        "No ten-trip or round-trip prices (those tickets were discontinued in January 2026).",
    ],
    "sources": [
        "Where each table came from (data.ny.gov dataset id and URL) and when it was downloaded. Use for citations.",
    ],
}

MAX_LISTED_VALUES = 25


def list_tables() -> dict:
    """Every table with its row count, source dataset, and short notes."""
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