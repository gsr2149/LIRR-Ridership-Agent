"""Writer: turns the Researchers' findings into one cited answer.

The Writer has no data tools; it can only use what the Researchers found.
After it writes, code (not the model) checks every citation:
  [q_xxxxxxxx]           must be a query that actually ran and succeeded
  [fare chart 2026-01-04] / [fare chart 2023-08-20]  only if a Researcher looked up a fare
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
from .context import BACKGROUND, BACKGROUND_CITATION, FARE_CITATION, FARE_CITATION_BEFORE, FARE_CITATIONS
from .charts import charts_for_answer
from .grounding import check_numbers
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
  [{FARE_CITATION}] for 2026 prices, [{FARE_CITATION_BEFORE}] for pre-2026 prices from
  fare lookups, or [{BACKGROUND_CITATION}] for the background facts above.
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
            + (f"\nFare lookups (cite before-prices [{FARE_CITATION_BEFORE}], after-prices [{FARE_CITATION}]):\n"
               f"{json.dumps(fares, default=str)}" if fares else "")
        )
    return "\n\n".join(blocks)


FARE_ALIAS_RE = re.compile(r"^(lookup_fare|fare lookups?|fare chart|fare table|lirr fares?)$", re.I)


def normalize_fare_citations(text: str) -> str:
    """The model sometimes cites the tool ([lookup_fare]) instead of the chart: cite both charts."""
    def fix(m: re.Match) -> str:
        tokens = [t.strip() for t in m.group(1).split(",")]
        if not any(FARE_ALIAS_RE.match(t) for t in tokens):
            return m.group(0)
        out = []
        for t in tokens:
            for new in ([FARE_CITATION_BEFORE, FARE_CITATION] if FARE_ALIAS_RE.match(t) else [t]):
                if new not in out:
                    out.append(new)
        return f"[{', '.join(out)}]"
    return CITE_RE.sub(fix, text or "")


def check_citations(text: str, valid_ids: set[str], fares_used: bool) -> dict:
    """Which citations in the text are valid and which aren't."""
    cited, invalid = set(), set()
    for inner in CITE_RE.findall(text):
        for token in [t.strip() for t in inner.split(",")]:
            if QID_RE.fullmatch(token):
                (cited if token in valid_ids else invalid).add(token)
            elif token.lower() in (c.lower() for c in FARE_CITATIONS):
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
        if not any(QID_RE.fullmatch(t) or t.lower() in [c.lower() for c in (*FARE_CITATIONS, BACKGROUND_CITATION)]
                   or t.startswith("q_") for t in tokens):
            return m.group(0)  # not a citation, e.g. [optional]
        kept = [t for t in tokens if t not in invalid]
        return f"[{', '.join(kept)}]" if kept else "[unverified]"
    return CITE_RE.sub(fix, text)


def sources_section(cited: list[str], findings: list[Finding], fares_cited: set[str], background_cited: bool) -> str:
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
    if fares_cited and FARE_CITATION_BEFORE.lower() in fares_cited:
        lines.append(f"- [{FARE_CITATION_BEFORE}] MTA LIRR fares effective August 20, 2023, from the 2023 "
                     "Table of Proposed Fares as adopted (data/fares)")
    if fares_cited and FARE_CITATION.lower() in fares_cited:
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
    draft = normalize_fare_citations(result.text)
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
                         + (f", or [{FARE_CITATION}] / [{FARE_CITATION_BEFORE}]" if fares_used else "")
                         + ". Drop any claim you can't support."),
        ]
        draft = normalize_fare_citations(run_agent(AGENT_NAME, SYSTEM_PROMPT, retry).text) or draft
        check = check_citations(draft, valid_ids, fares_used)
        revised = True

    # Every number next to a citation must match the cited data (or be one step from it).
    numbers = check_numbers(draft, findings)
    number_retry = False
    if numbers["unsupported"]:
        listing = "\n".join(f"- {u['number']} (cited {', '.join(u['cited'])}): \"{u['sentence']}\""
                            for u in numbers["unsupported"][:10])
        retry = contents + [
            model_content(draft),
            user_content("A code check could not find these numbers in the query results they cite:\n"
                         f"{listing}\nRewrite the whole answer. For each one: cite the query whose rows "
                         "contain it, correct it to the value in the rows, or remove the claim. "
                         "Keep everything else, including the citations that are already right."),
        ]
        new_draft = normalize_fare_citations(run_agent(AGENT_NAME, SYSTEM_PROMPT, retry).text)
        if new_draft:
            new_check = check_citations(new_draft, valid_ids, fares_used)
            if not (valid_ids and new_check["has_numbers"] and not new_check["cited"]):
                draft, check, number_retry = new_draft, new_check, True
                numbers = check_numbers(draft, findings)

    final = _remove_invalid(draft, set(check["invalid"])) if check["invalid"] else draft
    final_check = check_citations(final, valid_ids, fares_used)
    sources = sources_section(
        final_check["cited"], findings,
        fares_cited={c.lower() for c in FARE_CITATIONS if c.lower() in final.lower()},
        background_cited=BACKGROUND_CITATION.lower() in final.lower(),
    )
    entry = {
        "agent": AGENT_NAME,
        "name": "verify_citations",
        "args": {"valid_query_ids": sorted(valid_ids)},
        "result": {"cited": final_check["cited"], "invalid_removed": check["invalid"],
                   "revised": revised},
    }
    if numbers["unsupported"] and sources:
        shown = ", ".join(u["number"] for u in numbers["unsupported"][:6])
        sources += (f"\n\n_Number check: {numbers['grounded']} of {numbers['checked']} cited numbers match "
                    f"the query results; not matched: {shown}._")
    number_entry = {
        "agent": AGENT_NAME,
        "name": "verify_numbers",
        "args": {"rule": "each number in a cited sentence must appear in (or be one step from) the cited rows"},
        "result": {**numbers, "rewrote_answer": number_retry},
    }
    chart_entries = [{"agent": AGENT_NAME, "name": "chart", "args": {"query_id": c["query_id"]}, "result": c}
                     for c in charts_for_answer(final, findings)]
    return final + sources, [entry, number_entry, *chart_entries]