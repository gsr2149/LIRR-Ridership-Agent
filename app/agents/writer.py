"""Writer: turns the Researchers' findings into one cited answer.

The Writer has no data tools; it can only use what the Researchers found.
After it writes, code (not the model) checks every citation:
  [q_xxxxxxxx]           must be a query that actually ran and succeeded
  [fare chart 2026-01-04] only if a Researcher actually looked up a fare
  [MTA announcement]     background facts given in the prompt
If any citation is invalid, the Writer gets one chance to fix it; anything
still invalid is removed and flagged. The Sources list is built by code from
the cited query ids, so it can't be made up.
"""

from __future__ import annotations

import json
import re

from ..llm import model_content, run_agent, user_content
from ..tools.db import connection
from .context import BACKGROUND, BACKGROUND_CITATION, FARE_CITATION
from .researcher import Finding

AGENT_NAME = "writer"
MAX_ROWS_SHOWN = 40  # rows per query passed to the Writer

SYSTEM_PROMPT = f"""You are the Writer on a research team studying how the Long Island Rail
Road (LIRR) fare and ticketing changes of January 4, 2026 changed what riders buy and
what they pay. Researchers have already queried the data. Write the final answer to the
user's question using ONLY their findings and query results.

{BACKGROUND}

Rules:
- Lead with a 1-2 sentence direct answer, then the supporting evidence (bullets are fine),
  then a short "What the data can't tell us" part if anything is missing.
- Every number must come from the query results or findings given to you, and must be
  followed by its citation in square brackets: a query id like [q_1a2b3c4d], or
  [{FARE_CITATION}] for fares, or [{BACKGROUND_CITATION}] for the background facts above.
- Only cite query ids that appear in the material below. Never invent numbers or ids.
- If findings conflict or are thin, say so instead of smoothing it over.
- Don't add a sources list; it is added automatically. Keep it under about 300 words."""

CITE_RE = re.compile(r"\[([^\[\]]+)\]")
QID_RE = re.compile(r"\bq_[0-9a-f]{8}\b")


def _material(question: str, findings: list[Finding]) -> str:
    blocks = [f"User question: {question}"]
    for f in findings:
        queries = []
        for q in f.queries:
            queries.append({
                "query_id": q["query_id"], "purpose": q.get("purpose", ""),
                "columns": q["columns"], "rows": q["rows"][:MAX_ROWS_SHOWN],
                "truncated": q.get("truncated") or len(q["rows"]) > MAX_ROWS_SHOWN,
            })
        fares = [c["result"] for c in f.tool_calls if c["name"] == "lookup_fare" and "error" not in c["result"]]
        blocks.append(
            f"## Finding {f.id} ({f.agent})\nSub-question: {f.question}\n{f.summary}\n"
            f"Query results:\n{json.dumps(queries, default=str)}"
            + (f"\nFare lookups [{FARE_CITATION}]:\n{json.dumps(fares, default=str)}" if fares else "")
        )
    return "\n\n".join(blocks)


def check_citations(text: str, valid_ids: set[str], fares_used: bool) -> dict:
    """Which citations in the text are valid and which aren't."""
    cited, invalid = set(), set()
    for inner in CITE_RE.findall(text):
        for token in [t.strip() for t in inner.split(",")]:
            if QID_RE.fullmatch(token):
                (cited if token in valid_ids else invalid).add(token)
            elif token.lower() == FARE_CITATION.lower():
                if not fares_used:
                    invalid.add(token)
            elif token.lower() == BACKGROUND_CITATION.lower():
                pass
            elif token.startswith("q_"):
                invalid.add(token)
    return {"cited": sorted(cited), "invalid": sorted(invalid),
            "has_numbers": bool(re.search(r"\d", CITE_RE.sub("", text)))}


def _remove_invalid(text: str, invalid: set[str]) -> str:
    def fix(m: re.Match) -> str:
        tokens = [t.strip() for t in m.group(1).split(",")]
        if not any(QID_RE.fullmatch(t) or t.lower() in (FARE_CITATION.lower(), BACKGROUND_CITATION.lower())
                   or t.startswith("q_") for t in tokens):
            return m.group(0)  # not a citation, e.g. [optional]
        kept = [t for t in tokens if t not in invalid]
        return f"[{', '.join(kept)}]" if kept else "[unverified]"
    return CITE_RE.sub(fix, text)


def sources_section(cited: list[str], findings: list[Finding], fares_cited: bool, background_cited: bool) -> str:
    by_id = {q["query_id"]: q for f in findings for q in f.queries}
    try:
        datasets = {r[0]: (r[1], r[2]) for r in
                    connection().cursor().execute("SELECT table_name, title, url FROM sources").fetchall()}
    except Exception:
        datasets = {}
    lines = ["", "---", "**Sources**"]
    for qid in cited:
        q = by_id[qid]
        tables = ", ".join(
            f"{t} ({datasets[t][0]}{', ' + datasets[t][1] if datasets[t][1] else ''})" if t in datasets else t
            for t in q.get("tables", [])
        )
        lines.append(f"- [{qid}] {q.get('purpose') or 'query'} — {tables}")
    if fares_cited:
        lines.append(f"- [{FARE_CITATION}] MTA/LIRR Station Fares effective January 4, 2026 (data/fares)")
    if background_cited:
        lines.append(f"- [{BACKGROUND_CITATION}] MTA fare and ticketing changes effective January 4, 2026 "
                     "(background, not from the datasets)")
    return "\n".join(lines) if len(lines) > 3 else ""


def write(question: str, findings: list[Finding]) -> tuple[str, list[dict]]:
    """Return (final answer with Sources, tool_calls entries for the Writer's work)."""
    valid_ids = {q["query_id"] for f in findings for q in f.queries}
    fares_used = any(f.used_fares for f in findings)
    contents = [user_content(_material(question, findings))]

    result = run_agent(AGENT_NAME, SYSTEM_PROMPT, contents)
    draft = result.text
    check = check_citations(draft, valid_ids, fares_used)
    revised = False

    needs_fix = check["invalid"] or (valid_ids and check["has_numbers"] and not check["cited"])
    if needs_fix:
        problem = (f"These citations are not valid: {', '.join(check['invalid'])}. "
                   if check["invalid"] else "Your answer has numbers but cites no query ids. ")
        retry = contents + [
            model_content(draft or "(empty)"),
            user_content(problem + "Rewrite the answer so every number cites one of these query ids: "
                         f"{', '.join(sorted(valid_ids)) or '(none)'}"
                         + (f", or [{FARE_CITATION}]" if fares_used else "")
                         + ". Drop any claim you can't support."),
        ]
        draft = run_agent(AGENT_NAME, SYSTEM_PROMPT, retry).text or draft
        check = check_citations(draft, valid_ids, fares_used)
        revised = True

    final = _remove_invalid(draft, set(check["invalid"])) if check["invalid"] else draft
    final_check = check_citations(final, valid_ids, fares_used)
    sources = sources_section(
        final_check["cited"], findings,
        fares_cited=FARE_CITATION.lower() in final.lower(),
        background_cited=BACKGROUND_CITATION.lower() in final.lower(),
    )
    entry = {
        "agent": AGENT_NAME,
        "name": "verify_citations",
        "args": {"valid_query_ids": sorted(valid_ids)},
        "result": {"cited": final_check["cited"], "invalid_removed": check["invalid"],
                   "revised": revised},
    }
    return final + sources, [entry]