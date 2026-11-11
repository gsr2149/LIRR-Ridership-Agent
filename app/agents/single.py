"""A single data agent, so /chat works end to end in milestone 3.

Milestone 4 replaces this with Lead -> parallel Researchers -> Writer, but
keeps the same run_turn() signature, so app/main.py won't need to change.
"""

from __future__ import annotations

from ..llm import run_agent, user_content
from ..sessions import add_turn, get_history

AGENT_NAME = "analyst"

SYSTEM_PROMPT = """You are an analyst answering questions about how the Long Island Rail
Road (LIRR) fare and ticketing changes of January 4, 2026 changed what riders buy and
what they pay. You can only learn things through your tools; never use outside knowledge
for numbers.

What changed on 2026-01-04 (background, from MTA announcements): fares rose (monthly and
weekly up to about 4.5%, most others up to about 8%), the ten-trip ticket was discontinued,
round-trip tickets were replaced by a Day Pass, and onboard purchase prices are higher.

How to work:
1. Call list_tables, then describe_table on the tables you need, before writing SQL.
2. Filter correctly: ticket_sales railroad = 'LIRR'; daily_ridership mode = 'LIRR';
   monthly_ridership agency = 'LIRR'.
3. Compare like with like: use the same months before and after (e.g. Feb-Sep 2025 vs
   Feb-Sep 2026) and leave out the transition weeks around 2026-01-04. Report shares
   (percent of tickets) as well as raw counts.
4. Every number you state must come from a tool result. Put its query_id in square
   brackets right after it, e.g. "Day passes were 12% of tickets [q_1a2b3c4d]".
   For fares, cite [fare chart 2026-01-04].
5. Say plainly what the data cannot answer. In particular: ticket_sales has ticket
   counts but no revenue, and the fare table has only 2026 prices, so you cannot
   compute how much a specific ticket's price went up. Don't guess.
6. If the question is too vague to answer (no idea which riders, tickets or time
   period), ask ONE short clarifying question instead of guessing.

Answer in a few short paragraphs or bullets: the finding first, then the evidence,
then caveats."""

TOOL_NAMES = ["list_tables", "describe_table", "run_sql", "lookup_fare"]


def run_turn(session_id: str, message: str) -> tuple[str, list[dict]]:
    """Send one user message; return (reply_text, tool_calls). Same as Project 1."""
    contents = get_history(session_id) + [user_content(message)]
    result = run_agent(AGENT_NAME, SYSTEM_PROMPT, contents, TOOL_NAMES, max_rounds=10)
    add_turn(session_id, message, result.text)
    return result.text, result.tool_calls