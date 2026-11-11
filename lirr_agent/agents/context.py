"""Shared text for the agents' prompts, kept in one place so they agree."""

from ..tools.schema import TABLE_NOTES

FARE_CITATION = "fare chart 2026-01-04"         # prices after the change
FARE_CITATION_BEFORE = "fare chart 2023-08-20"  # prices before the change
FARE_CITATIONS = (FARE_CITATION, FARE_CITATION_BEFORE)
BACKGROUND_CITATION = "MTA announcement"

BACKGROUND = f"""Background (from MTA announcements, NOT from the datasets; cite as [{BACKGROUND_CITATION}]):
On 2026-01-04 LIRR fares rose (monthly and weekly tickets up to about 4.5%, most other
tickets up to about 8%), the ten-trip ticket was discontinued, round-trip tickets were
replaced by a Day Pass (weekday and weekend prices), and buying onboard costs more."""

DATA_RULES = f"""Data rules:
- Filter to the LIRR: ticket_sales railroad = 'LIRR'; daily_ridership mode = 'LIRR';
  monthly_ridership agency = 'LIRR'. stop_occupancy and lirr_fares are LIRR only.
- ticket_sales has ticket COUNTS (number_of_tickets), not revenue. lirr_fares has prices
  before (fare_period 'pre_2026', effective 2023-08-20) and after ('post_2026', effective
  2026-01-04) the change, so price changes per ticket CAN be computed (lookup_fare does it
  for one zone pair; SQL can join the two periods). Spending is an ESTIMATE: tickets sold
  x the matching price. For spending, use the ticket_spend_est table (already priced,
  with the counting quirks handled) rather than joining tables yourself, and report the
  share of tickets it could price in each period.
- Counting quirks in ticket_sales: a ten_trip ticket is counted as 10 tickets (one per ride:
  every ten_trip count is a multiple of 10), and round trips are mostly counted per leg. So
  ticket counts are closer to RIDES than to purchases; say so when comparing ticket types.
- Compare like with like: the same calendar months before and after (e.g. Feb-Sep 2025
  vs Feb-Sep 2026), leaving out the transition weeks around 2026-01-04. Give shares
  (percent of LIRR tickets) as well as counts.
- Ticket sales and occupancy cover 2025-01-01 to 2026-09-30 in this database.
- Never assume which zone a station is in: use the station zone list below, or
  lookup_fare (it resolves station names). Huntington, for example, is zone 9, not 7.
- Prices from lookup_fare are cited by the chart they come from: [{FARE_CITATION}] for
  2026 prices, [{FARE_CITATION_BEFORE}] for pre-2026 prices (cite both for a change)."""


def data_catalog() -> str:
    """Short description of every table, for the Lead's planning prompt."""
    lines = []
    for table, notes in TABLE_NOTES.items():
        if table == "sources":
            continue
        lines.append(f"- {table}: " + " ".join(notes[:3]))
    return "\n".join(lines)


SCHEMA_TABLES = ["ticket_sales", "daily_ridership", "monthly_ridership", "stop_occupancy", "lirr_fares",
                 "ticket_spend_est"]
MAX_VALUES_SHOWN = 12


def schema_brief() -> str:
    """Columns (and allowed values) of every table, so Researchers can write SQL
    straight away instead of spending two Gemini calls discovering the schema.
    describe_table is cached, so this is fast after the first call."""
    from ..tools.schema import describe_table

    blocks = []
    for table in SCHEMA_TABLES:
        info = describe_table(table)
        if "error" in info:
            continue
        cols = []
        for c in info["columns"]:
            text = f"{c['column']} {c['type']}"
            if "values" in c:
                vals = c["values"][:MAX_VALUES_SHOWN]
                more = ", ..." if len(c["values"]) > MAX_VALUES_SHOWN else ""
                text += " (" + ", ".join(repr(v) for v in vals) + more + ")"
            elif "min" in c:
                text += f" ({str(c['min'])[:10]} to {str(c['max'])[:10]})"
            cols.append(text)
        blocks.append(f"{table}: " + "; ".join(cols))
    return "\n".join(blocks)


def station_zone_brief() -> str:
    """Every LIRR station's fare zone, grouped by zone, so no agent has to guess one."""
    from ..tools.fares import station_list

    try:
        stations = station_list()
    except Exception:
        return ""
    by_zone: dict[str, list[str]] = {}
    for s in stations:
        by_zone.setdefault(s["zone"], []).append(s["name"])
    zones = sorted(by_zone, key=int)
    return "\n".join(f"Zone {z}: " + ", ".join(by_zone[z]) for z in zones)
