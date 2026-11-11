"""Shared text for the agents' prompts, kept in one place so they agree."""

from ..tools.schema import TABLE_NOTES

FARE_CITATION = "fare chart 2026-01-04"
BACKGROUND_CITATION = "MTA announcement"

BACKGROUND = f"""Background (from MTA announcements, NOT from the datasets; cite as [{BACKGROUND_CITATION}]):
On 2026-01-04 LIRR fares rose (monthly and weekly tickets up to about 4.5%, most other
tickets up to about 8%), the ten-trip ticket was discontinued, round-trip tickets were
replaced by a Day Pass (weekday and weekend prices), and buying onboard costs more."""

DATA_RULES = f"""Data rules:
- Filter to the LIRR: ticket_sales railroad = 'LIRR'; daily_ridership mode = 'LIRR';
  monthly_ridership agency = 'LIRR'. stop_occupancy and lirr_fares are LIRR only.
- ticket_sales has ticket COUNTS (number_of_tickets), not revenue. lirr_fares has only
  2026 prices. So "how much more does a ticket cost than before" cannot be computed;
  estimates of spending must multiply 2026 counts by 2026 prices and say so.
- Compare like with like: the same calendar months before and after (e.g. Feb-Sep 2025
  vs Feb-Sep 2026), leaving out the transition weeks around 2026-01-04. Give shares
  (percent of LIRR tickets) as well as counts.
- Ticket sales and occupancy cover 2025-01-01 to 2026-09-30 in this database.
- Fares from lookup_fare are cited as [{FARE_CITATION}]."""


def data_catalog() -> str:
    """Short description of every table, for the Lead's planning prompt."""
    lines = []
    for table, notes in TABLE_NOTES.items():
        if table == "sources":
            continue
        lines.append(f"- {table}: " + " ".join(notes[:3]))
    return "\n".join(lines)


SCHEMA_TABLES = ["ticket_sales", "daily_ridership", "monthly_ridership", "stop_occupancy", "lirr_fares"]
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
