"""Lead: plans the research, fans out to parallel Researchers, checks for gaps,
and hands the findings to the Writer.

    run_turn(session_id, message) -> (reply_text, tool_calls)   # same as Project 1

One turn:
  1. plan     - clarify a vague question, decline an out-of-scope one, or split it
                into 2-4 sub-questions
  2. dispatch - run one Researcher per sub-question, in parallel, fresh contexts
  3. review   - enough to answer? If not, dispatch follow-ups (up to MAX_ROUNDS total)
  4. write    - the Writer composes a cited answer; citations are checked in code
The Lead's own decisions are recorded in tool_calls (agent "lead") so the
front end can show the whole process.
"""

from __future__ import annotations

import logging
import contextvars
import os
import time
from concurrent.futures import ThreadPoolExecutor

from .. import events
from ..llm import ask_json, user_content
from ..sessions import add_turn, get_history
from . import researcher, writer
from .context import BACKGROUND, data_catalog

AGENT_NAME = "lead"
MAX_ROUNDS = int(os.environ.get("MAX_RESEARCH_ROUNDS", "2"))         # turn cap: 1 plan + 1 follow-up
MAX_PARALLEL = int(os.environ.get("MAX_PARALLEL_RESEARCHERS", "4"))
MAX_FOLLOW_UPS = 2

PLAN_PROMPT = f"""You are the Lead of a research team that answers questions about how the
Long Island Rail Road (LIRR) fare and ticketing changes of January 4, 2026 changed what
riders buy and what they pay. You plan; Researchers query the data; a Writer reports.

{BACKGROUND}

The data (the ONLY sources available):
{data_catalog()}

Decide what to do with the user's latest message (earlier messages are context: if you
asked a clarifying question before, the latest message may be the answer to it).

Return JSON with one of these shapes:
{{"action": "clarify", "question": "...", "options": ["...", "...", "..."]}}
  when the question is too vague to research well: it doesn't say what aspect it is about
  (what riders buy, what they pay, how many ride, how crowded trains are) or what to
  compare. Examples: "did things change?", "tell me about the LIRR", "is it worse now?".
  Ask ONE short question. options = 2-3 complete questions the user could send instead,
  each one specific and answerable (e.g. "What did riders buy instead of ten-trip tickets?").
{{"action": "out_of_scope", "reason": "...", "options": ["...", "..."]}}
  when the data cannot answer it at all (other transit systems, events outside 2025-2026,
  individual riders, anything not in the tables above). In 1-2 sentences say why, and give
  2-3 complete questions this data CAN answer as options.
Write the question, reason and options for a rider, in plain words: never mention table or
column names, SQL, or "datasets" by their technical names.
{{"action": "research", "sub_questions": ["...", "..."]}}
  otherwise. Give 2-{MAX_PARALLEL} specific sub-questions, each answerable with one or two
  SQL queries on the tables above, that together answer the question. Name the table,
  the comparison (e.g. Feb-Sep 2025 vs Feb-Sep 2026) and the measure.
  Prefer researching over clarifying when the question is reasonably clear.

Good plans cover DIFFERENT ANGLES, never the same measure for different months (one
Researcher can compare several months in one query). For a question about a ticket or
fare change, the usual angles are:
  1. the direct effect: that ticket's sales before vs after, by month or around 2026-01-04
  2. substitution: how the share of the OTHER ticket types (sold_as) changed over the
     same months, i.e. what riders bought instead
  3. how riders buy: the mix of device_class_type (mobile app, machines, onboard) before vs after
  4. context: LIRR ridership over the same months, to separate "fewer riders" from
     "riders switched tickets"
  5. price: what the same trip cost before vs after (lirr_fares has both periods), or an
     estimate of what riders spent: tickets sold x matching prices, before vs after
  6. who pays more: the cheapest way to ride for different riders (daily vs 1-3 days a week,
     peak vs off-peak) before vs after, with compare_ticket_costs; the ten-trip's loss and
     the new Day Pass hit them differently
Pick the angles that fit the question."""

REVIEW_PROMPT = f"""You are the Lead of a research team studying the LIRR fare and ticketing
changes of January 4, 2026. Researchers have answered sub-questions. Decide whether the
findings are enough for a well-supported answer to the user's question.

The data available:
{data_catalog()}

Return JSON:
{{"done": true, "reason": "..."}}  if the findings answer the question (or the data
  simply can't answer the rest, which the Writer will say), or
{{"done": false, "reason": "...", "follow_up_questions": ["..."]}}  if there is a gap that
  one more query could fill. Gaps to look for:
  - a failed or empty finding, or a claim with no numbers
  - the "so what" is missing: e.g. a ticket disappeared but nothing says what riders
    bought instead, or sales changed but nothing says whether ridership changed too
  - an unfair comparison: before and after periods that are different calendar months
    (e.g. Sep-Dec 2025 vs Jan-Apr 2026 mixes seasons). Ask for the same months in both years,
    e.g. Feb-Sep 2025 vs Feb-Sep 2026
  - a surprise nobody checked: e.g. sales of a discontinued ticket after the change
    date (check by DAY whether they fall before 2026-01-04), or a number that
    contradicts another finding
  At most {MAX_FOLLOW_UPS} follow-up questions, each specific and different from what
  was already asked.
Don't ask for data that doesn't exist (revenue, prices before August 2023)."""


def plan(history, message: str) -> dict:
    decision = ask_json(PLAN_PROMPT, list(history) + [user_content(message)])
    action = decision.get("action")
    options = [str(o).strip() for o in decision.get("options") or [] if str(o).strip()][:4]
    if action == "clarify" and decision.get("question"):
        return {"action": "clarify", "question": str(decision["question"]), "options": options}
    if action == "out_of_scope" and decision.get("reason"):
        return {"action": "out_of_scope", "reason": str(decision["reason"]), "options": options}
    subs = [str(q) for q in decision.get("sub_questions") or [] if str(q).strip()]
    # Fallback: if the plan is unusable, research the question as asked.
    return {"action": "research", "sub_questions": subs[:MAX_PARALLEL] or [message]}


def review(message: str, findings: list[researcher.Finding]) -> dict:
    summaries = "\n\n".join(f"{f.id} ({f.agent}) - {f.question}\n{f.summary}" for f in findings)
    decision = ask_json(REVIEW_PROMPT, [user_content(f"User question: {message}\n\nFindings:\n{summaries}")])
    follow = [str(q) for q in decision.get("follow_up_questions") or [] if str(q).strip()]
    done = bool(decision.get("done", True)) or not follow
    return {"done": done, "reason": str(decision.get("reason", "")), "follow_up_questions": follow[:MAX_FOLLOW_UPS]}


def dispatch(sub_questions: list[str], message: str, start: int) -> list[researcher.Finding]:
    """Run one Researcher per sub-question at the same time."""
    jobs = [(f"R{start + i}", f"researcher-{start + i}", q) for i, q in enumerate(sub_questions)]
    def run_one(fid: str, agent: str, q: str) -> researcher.Finding:
        events.emit({"type": "researcher_start", "agent": agent, "sub_question": q})
        finding = researcher.research(fid, agent, q, message)
        events.step({"agent": agent, "name": "report_finding", "args": {"sub_question": q},
                     "result": {"summary": finding.summary}})
        return finding

    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(jobs))) as pool:
        # copy_context: each thread sees this request's event stream (see events.py)
        futures = [pool.submit(contextvars.copy_context().run, run_one, fid, agent, q) for fid, agent, q in jobs]
        return [f.result() for f in futures]  # keep plan order


log = logging.getLogger("uvicorn.error")


def _lead_entry(name: str, args: dict, result: dict) -> dict:
    entry = {"agent": AGENT_NAME, "name": name, "args": args, "result": result}
    events.step(entry)
    return entry


def run_turn(session_id: str, message: str) -> tuple[str, list[dict]]:
    history = get_history(session_id)
    tool_calls: list[dict] = []
    started = time.perf_counter()

    def mark(step: str) -> None:
        log.info("research %-28s %5.1fs", step, time.perf_counter() - started)

    events.phase("planning")
    decision = plan(history, message)
    tool_calls.append(_lead_entry("plan", {"message": message}, decision))
    mark(f"plan -> {decision['action']}")

    if decision["action"] in ("clarify", "out_of_scope"):
        reply = decision["question"] if decision["action"] == "clarify" else decision["reason"]
        if decision["options"]:
            reply += "\n\nYou could ask:\n" + "\n".join(f"- {o}" for o in decision["options"])
    else:
        findings: list[researcher.Finding] = []
        questions = decision["sub_questions"]
        for round_no in range(1, MAX_ROUNDS + 1):
            tool_calls.append(_lead_entry(
                "dispatch_researchers",
                {"round": round_no, "sub_questions": questions},
                {"researchers": [f"researcher-{len(findings) + 1 + i}" for i in range(len(questions))]},
            ))
            events.phase("researching", round=round_no)
            new = dispatch(questions, message, start=len(findings) + 1)
            mark(f"round {round_no}: {len(new)} researchers")
            findings.extend(new)
            for f in new:
                tool_calls.extend(f.tool_calls)
                tool_calls.append({"agent": f.agent, "name": "report_finding",
                                   "args": {"sub_question": f.question}, "result": {"summary": f.summary}})
            if round_no == MAX_ROUNDS:
                tool_calls.append(_lead_entry("review", {"round": round_no},
                                              {"done": True, "reason": f"Reached the {MAX_ROUNDS}-round cap."}))
                break
            events.phase("reviewing", round=round_no)
            verdict = review(message, findings)
            tool_calls.append(_lead_entry("review", {"round": round_no}, verdict))
            mark(f"review {round_no} -> {'done' if verdict['done'] else 'follow up'}")
            if verdict["done"]:
                break
            questions = verdict["follow_up_questions"]

        events.phase("writing")
        reply, writer_calls = writer.write(message, findings)
        tool_calls.extend(writer_calls)
        for entry in writer_calls:
            events.step(entry)
        mark("writer")

    add_turn(session_id, message, reply)
    return reply, tool_calls
