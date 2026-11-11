"""lookup_fare: January 2026 LIRR fares between two zones or stations."""

from __future__ import annotations

import difflib
from functools import lru_cache

from .db import connection, to_json_value

ZONES = ["1", "3", "4", "7", "9", "10", "12", "14"]
TICKET_TYPES = [
    "monthly", "weekly", "one_way_peak", "one_way_offpeak", "one_way_senior_disabled",
    "day_pass_weekday", "day_pass_weekend", "onboard_one_way_peak", "onboard_one_way_offpeak",
]
NOT_IN_TABLE = {
    "ten_trip": "Ten-trip tickets were discontinued in January 2026; the fare table has no ten-trip price.",
    "round_trip": "Round-trip tickets were replaced by the Day Pass in January 2026; see day_pass_weekday / day_pass_weekend.",
}


@lru_cache(maxsize=1)
def station_zones() -> dict[str, str]:
    """LIRR station name (lowercase) -> fare zone, taken from the ticket sales data."""
    cur = connection().cursor()
    rows = cur.execute(
        """SELECT name, zone FROM (
               SELECT origin_station_name AS name, origin_station_zone AS zone
               FROM ticket_sales WHERE railroad = 'LIRR'
               UNION ALL
               SELECT destination_station_name, destination_station_zone
               FROM ticket_sales WHERE railroad = 'LIRR'
           ) WHERE name IS NOT NULL AND zone IS NOT NULL
           GROUP BY name, zone"""
    ).fetchall()
    return {str(name).strip().lower(): str(zone) for name, zone in rows}


def resolve_zone(place: str | int) -> tuple[str | None, str]:
    """Accept a zone ('7', 7) or a station name ('Hicksville'). Returns (zone, how)."""
    text = str(place).strip()
    if text.lower().startswith("zone"):
        text = text[4:].strip()
    if text in ZONES:
        return text, f"zone {text}"
    if text.isdigit():
        return None, f"'{text}' is not an LIRR fare zone. Zones are {', '.join(ZONES)}."

    stations = station_zones()
    key = text.lower()
    if key in stations:
        return stations[key], f"{text} is in zone {stations[key]}"
    close = difflib.get_close_matches(key, stations.keys(), n=1, cutoff=0.8)
    if close:
        return stations[close[0]], f"'{text}' matched station '{close[0]}' (zone {stations[close[0]]})"
    suggestions = difflib.get_close_matches(key, stations.keys(), n=3, cutoff=0.5)
    hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
    return None, f"Unknown LIRR station or zone '{text}'.{hint}"


def lookup_fare(origin: str, destination: str, ticket_type: str | None = None) -> dict:
    """Fares effective 2026-01-04 between two zones/stations, for one or all ticket types."""
    o_zone, o_how = resolve_zone(origin)
    d_zone, d_how = resolve_zone(destination)
    if o_zone is None or d_zone is None:
        return {"error": " ".join(m for z, m in [(o_zone, o_how), (d_zone, d_how)] if z is None)}

    if ticket_type:
        ticket_type = ticket_type.strip().lower()
        for prefix, msg in NOT_IN_TABLE.items():
            if ticket_type.startswith(prefix):
                return {"error": msg, "available_ticket_types": TICKET_TYPES}
        if ticket_type not in TICKET_TYPES:
            return {"error": f"Unknown ticket_type '{ticket_type}'.", "available_ticket_types": TICKET_TYPES}

    sql = ("SELECT ticket_type, price_usd, effective_date, source_doc FROM lirr_fares "
           "WHERE origin_zone = ? AND dest_zone = ?")
    params: list = [o_zone, d_zone]
    if ticket_type:
        sql += " AND ticket_type = ?"
        params.append(ticket_type)
    sql += " ORDER BY ticket_type"

    rows = connection().cursor().execute(sql, params).fetchall()
    return {
        "origin": o_how,
        "destination": d_how,
        "origin_zone": o_zone,
        "destination_zone": d_zone,
        "fares": [
            {"ticket_type": t, "price_usd": to_json_value(p), "effective_date": to_json_value(e)}
            for t, p, e, _ in rows
        ],
        "source_doc": rows[0][3] if rows else None,
        "note": "Prices effective 2026-01-04. No pre-2026 prices are in this database.",
    }