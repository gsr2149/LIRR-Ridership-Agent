"""Researcher: answers ONE sub-question with the data tools, in a fresh context.

Each Researcher sees only its sub-question (no chat history, no other
Researchers' work), so several can run in parallel without stepping on
each other. What comes back is a Finding: the Researcher's write-up plus the
exact queries it ran, collected from its tool calls (not from its prose).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..llm import run_agent, user_content
from .context import BACKGROUND, DATA_RULES, schema_brief

RESEARCHER_MODEL = os.environ.get("RESEARCHER_MODEL") or None  # None = same as GEMINI_MODEL
MAX_TOOL_ROUNDS = 8
TOOL_NAMES = ["describe_table", "run_sql", "lookup_fare"]

SYSTEM_PROMPT = f"""You are a Researcher on a team studying the Long Island Rail Road (LIRR).
You get ONE sub-question. Answer it with evidence from your tools only, then stop.

{BACKGROUND}

{DATA_RULES}

Work efficiently: the tables and their columns are listed at the end of these
instructions, so go straight to run_sql (use describe_table only if something is
unclear). Run a few focused aggregate queries (usually 1-3), ideally comparing several
months in ONE query. Don't explore beyond your sub-question.

Finish with exactly this format:
FINDING: 2-5 sentences that answer the sub-question with specific numbers. Put the
query_id in square brackets after every number, e.g. "monthly tickets fell 3% [q_1a2b3c4d]".
LIMITS: what you could not establish from the data, or "none"."""


@dataclass
class Finding:
    id: str                    # "R1", "R2", ...
    agent: str                 # "researcher-1", ...
    question: str
    summary: str
    tool_calls: list[dict] = field(default_factory=list)

    @property
    def queries(self) -> list[dict]:
        """Successful run_sql results this Researcher produced."""
        return [c["result"] for c in self.tool_calls
                if c["name"] == "run_sql" and "error" not in c["result"]]

    @property
    def used_fares(self) -> bool:
        return any(c["name"] == "lookup_fare" and "error" not in c["result"] for c in self.tool_calls)


def research(finding_id: str, agent: str, sub_question: str, user_question: str) -> Finding:
    prompt = (
        f"Sub-question: {sub_question}\n\n"
        f"(For context only, the user's overall question was: {user_question})"
    )
    try:
        system = SYSTEM_PROMPT + "\n\nTables and columns:\n" + schema_brief()
        result = run_agent(agent, system, [user_content(prompt)], TOOL_NAMES,
                           max_rounds=MAX_TOOL_ROUNDS, model=RESEARCHER_MODEL)
        summary = result.text or "FINDING: (no answer)\nLIMITS: the researcher returned nothing."
        calls = result.tool_calls
    except Exception as e:  # one failed Researcher shouldn't sink the whole answer
        summary = f"FINDING: (research failed)\nLIMITS: {type(e).__name__}: {e}"
        calls = []
    return Finding(finding_id, agent, sub_question, summary, calls)
