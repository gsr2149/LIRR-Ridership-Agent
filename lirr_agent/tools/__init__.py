"""Tool registry: the one place the agents look up tools.

Each tool has a name, a description for the model, a JSON Schema for its
arguments, and the Python function that runs it. The schemas are plain JSON
Schema, so they work with any LLM provider's tool-calling format.

    from lirr_agent.tools import TOOLS, run_tool
    result = run_tool("run_sql", {"sql": "SELECT 1", "purpose": "test"})
"""

from __future__ import annotations

import time
from typing import Any, Callable

from .advisor import compare_ticket_costs
from .fares import TICKET_TYPES, lookup_fare
from .schema import describe_table, list_tables
from .sql import MAX_ROWS, run_sql

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_tables",
        "description": (
            "List every table in the LIRR database with row counts, the data.ny.gov "
            "source, and notes on how to filter it correctly. Call this first."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "fn": lambda **_: list_tables(),
    },
    {
        "name": "describe_table",
        "description": (
            "Show one table's columns, types, date ranges and the allowed values of "
            "categorical columns (e.g. ticket_sales.sold_as). Call before writing SQL on a table."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"table": {"type": "string", "description": "Table name from list_tables."}},
            "required": ["table"],
        },
        "fn": lambda table, **_: describe_table(table),
    },
    {
        "name": "run_sql",
        "description": (
            "Run ONE read-only DuckDB SELECT query and get the rows back "
            f"(max {MAX_ROWS} rows, so aggregate with GROUP BY). Every result has a "
            "query_id: cite it for any number you report. Remember railroad = 'LIRR' "
            "for ticket_sales, mode = 'LIRR' for daily_ridership, agency = 'LIRR' for monthly_ridership."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "A single SELECT or WITH ... SELECT statement."},
                "purpose": {"type": "string", "description": "One sentence: what this query is meant to show."},
            },
            "required": ["sql", "purpose"],
        },
        "fn": lambda sql, purpose="", **_: run_sql(sql, purpose),
    },
    {
        "name": "lookup_fare",
        "description": (
            "Look up LIRR fares between two places, BEFORE (effective 2023-08-20) and AFTER "
            "(effective 2026-01-04) the January 2026 fare change, with the percent change per "
            "ticket type. Each place can be a fare zone (1, 3, 4, 7, 9, 10, 12, 14) or a station "
            "name such as 'Hicksville'. Ten-trip prices exist only before 2026; Day Pass prices only after."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "Zone number or LIRR station name."},
                "destination": {"type": "string", "description": "Zone number or LIRR station name."},
                "ticket_type": {
                    "type": "string",
                    "enum": TICKET_TYPES,
                    "description": "Optional. Leave out to get every ticket type.",
                },
            },
            "required": ["origin", "destination"],
        },
        "fn": lambda origin, destination, ticket_type=None, **_: lookup_fare(origin, destination, ticket_type),
    },
    {
        "name": "compare_ticket_costs",
        "description": (
            "For one trip and a riding pattern, the cheapest way to ride for a month BEFORE and AFTER "
            "the January 2026 change, every option priced (one-ways, ten-trip, app one-ways with the "
            "11th free, Day Pass, weekly, monthly), and a grid of how the change hit each pattern "
            "(1-5 days a week x peak/off-peak) on that trip. Use it for 'what should I buy', 'who pays "
            "more' and hybrid/part-time commuter questions. Numbers come from the two fare charts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "Zone number or LIRR station name."},
                "destination": {"type": "string", "description": "Zone number or LIRR station name."},
                "days_per_week": {"type": "integer", "description": "Weekdays ridden, 1-5 (one round trip each)."},
                "time": {"type": "string", "enum": ["peak", "offpeak"]},
            },
            "required": ["origin", "destination"],
        },
        "fn": lambda origin, destination, days_per_week=5, time="peak", **_:
            compare_ticket_costs(origin, destination, days_per_week, time),
    },
]

_BY_NAME: dict[str, Callable[..., dict]] = {t["name"]: t["fn"] for t in TOOLS}


def tool_specs() -> list[dict[str, Any]]:
    """Name, description and input_schema for each tool (no Python functions)."""
    return [{k: t[k] for k in ("name", "description", "input_schema")} for t in TOOLS]


def run_tool(name: str, args: dict | None = None) -> dict:
    """Run a tool by name. Never raises: errors come back as {"error": ...}
    so the agent can read them and try again."""
    started = time.perf_counter()
    fn = _BY_NAME.get(name)
    if fn is None:
        result: dict = {"error": f"Unknown tool '{name}'. Available: {sorted(_BY_NAME)}"}
    else:
        try:
            result = fn(**(args or {}))
        except TypeError as e:
            result = {"error": f"Bad arguments for {name}: {e}"}
        except Exception as e:
            result = {"error": f"{type(e).__name__}: {e}"}
    result.setdefault("elapsed_ms", round((time.perf_counter() - started) * 1000))
    return result


__all__ = ["TOOLS", "tool_specs", "run_tool"]