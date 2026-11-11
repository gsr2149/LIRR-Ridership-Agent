"""lookup_fare: LIRR fares between two zones or stations, before and after the
January 4, 2026 fare change, with the percent change for each ticket type."""

from __future__ import annotations

import difflib
from functools import lru_cache

from .db import connection, to_json_value

ZONES = ["1", "3", "4", "7", "9", "10", "12", "14"]
TICKET_TYPES = [
    "monthly", "weekly", "one_way_peak", "one_way_offpeak", "one_way_senior_disabled",
    "day_pass_weekday", "day_pass_weekend", "onboard_one_way_peak", "onboard_one_way_offpeak",
    "ten_trip_peak", "ten_trip_offpeak", "ten_trip_senior_disabled",
]
DISCONTINUED = {"ten_trip_peak", "ten_trip_offpeak", "ten_trip_senior_disabled"}
NEW_IN_2026 = {"day_pass_weekday", "day_pass_weekend"}
NOT_IN_TABLE = {
    "round_trip": "Round-trip tickets were replaced by the Day Pass on 2026-01-04 and neither fare chart "
                  "lists a round-trip price; see day_pass_weekday / day_pass_weekend.",
}
PERIODS = {"pre_2026": "2023-08-20", "post_2026": "2026-01-04"}


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
    """Fares between two zones/stations: before (effective 2023-08-20) and after
    (effective 2026-01-04) the fare change, for one or all ticket types."""
    o_zone, o_how = resolve_zone(origin)
    d_zone, d_how = resolve_zone(destination)
    if o_zone is None or d_zone is None:
        return {"error": " ".join(m for z, m in [(o_zone, o_how), (d_zone, d_how)] if z is None)}

    if ticket_type:
        ticket_type = ticket_type.strip().lower()
        if ticket_type == "ten_trip":
            ticket_type = "ten_trip_peak"
        for prefix, msg in NOT_IN_TABLE.items():
            if ticket_type.startswith(prefix):
                return {"error": msg, "available_ticket_types": TICKET_TYPES}
        if ticket_type not in TICKET_TYPES:
            return {"error": f"Unknown ticket_type '{ticket_type}'.", "available_ticket_types": TICKET_TYPES}

    sql = ("SELECT fare_period, ticket_type, price_usd FROM lirr_fares "
           "WHERE origin_zone = ? AND dest_zone = ?")
    params: list = [o_zone, d_zone]
    if ticket_type:
        sql += " AND ticket_type = ?"
        params.append(ticket_type)
    prices: dict[str, dict[str, float]] = {}
    for period, t, p in connection().cursor().execute(sql, params).fetchall():
        prices.setdefault(t, {})[period] = to_json_value(p)

    fares = []
    for t in [t for t in TICKET_TYPES if t in prices]:
        before, after = prices[t].get("pre_2026"), prices[t].get("post_2026")
        row = {"ticket_type": t, "price_before_usd": before, "price_after_usd": after}
        if before is not None and after is not None:
            row["change_pct"] = round((after - before) / before * 100, 1)
        elif t in DISCONTINUED:
            row["status"] = "discontinued 2026-01-04 (last price shown)"
        elif t in NEW_IN_2026:
            row["status"] = "new on 2026-01-04 (replaced the round-trip ticket)"
        else:
            row["status"] = "no pre-2026 price for this zone pair in the 2023 fare table"
        fares.append(row)

    notes = ["Before = fares effective 2023-08-20 (in force until 2026-01-03). "
             "After = fares effective 2026-01-04."]
    if o_zone in ("1", "3") and d_zone in ("1", "3"):
        notes.append("Trips within New York City (zones 1 and 3 to zone 1): before-2026 one-way prices are "
                     "the CityTicket fares ($7 peak, $5 off-peak).")
    if not any(f["price_before_usd"] for f in fares):
        notes.append("The 2023 fare table only covers trips to/from zone 1 and some trips to/from zones 3, 4 "
                     "and 7, so this zone pair has no pre-2026 prices.")
    return {
        "origin": o_how,
        "destination": d_how,
        "origin_zone": o_zone,
        "destination_zone": d_zone,
        "fares": fares,
        "notes": notes,
    }
