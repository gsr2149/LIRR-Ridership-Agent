"""Try the tools by hand against your real database.

    python -m lirr_agent.tools list_tables
    python -m lirr_agent.tools describe_table ticket_sales
    python -m lirr_agent.tools run_sql "SELECT sold_as, sum(number_of_tickets) AS n FROM ticket_sales WHERE railroad = 'LIRR' GROUP BY 1 ORDER BY n DESC"
    python -m lirr_agent.tools lookup_fare "Penn Station" Hicksville monthly

You can also pass the arguments as JSON:
    python -m lirr_agent.tools lookup_fare '{"origin": "1", "destination": "7"}'
"""

import json
import sys

from . import run_tool, tool_specs

# Order of plain (non-JSON) arguments for each tool.
POSITIONAL = {
    "list_tables": [],
    "describe_table": ["table"],
    "run_sql": ["sql", "purpose"],
    "lookup_fare": ["origin", "destination", "ticket_type"],
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in POSITIONAL:
        print(__doc__)
        print("Tools:", ", ".join(t["name"] for t in tool_specs()))
        return 1
    name, rest = sys.argv[1], sys.argv[2:]
    if len(rest) == 1 and rest[0].lstrip().startswith("{"):
        args = json.loads(rest[0])
    else:
        args = dict(zip(POSITIONAL[name], rest))
        if name == "run_sql":
            args.setdefault("purpose", "manual test")
    print(json.dumps(run_tool(name, args), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())