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