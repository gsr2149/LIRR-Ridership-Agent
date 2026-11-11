"""Tests for the Lead -> parallel Researchers -> Writer pipeline, using a fake
Gemini that answers differently depending on which agent is calling.

    python -m pytest -q
"""

import json
import threading
import time

import pytest
from google.genai import types

from lirr_agent import llm, sessions
from lirr_agent.agents import lead, writer
from lirr_agent.tools.sql import query_id
from test_tools import fake_db  # noqa: F401  (reuses the tiny fake database)

SQL_MIX = ("SELECT sold_as, sum(number_of_tickets) AS n FROM ticket_sales "
           "WHERE railroad = 'LIRR' GROUP BY 1 ORDER BY n DESC")
SQL_RIDERS = "SELECT sum(count) AS riders FROM daily_ridership WHERE mode = 'LIRR'"
Q_MIX, Q_RIDERS = query_id(SQL_MIX), query_id(SQL_RIDERS)


def text(t):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part.from_text(text=t)]))])


def call(name, args):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part(
            function_call=types.FunctionCall(name=name, args=args))]))])


def first_text(contents):
    return "".join(p.text or "" for p in contents[0].parts)


def last_text(contents):
    return "".join(p.text or "" for p in contents[-1].parts if p.text)


class FakeTeam:
    """Plays every agent. Picks the role from the system prompt."""

    def __init__(self, plan, reviews=None, writer_texts=None):
        self.plan = plan
        self.reviews = list(reviews or [{"done": True, "reason": "enough"}])
        self.writer_texts = list(writer_texts or [f"Ten-trip led with 40 tickets [{Q_MIX}]."])
        self.models = self
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.researcher_prompts = []
        self.writer_inputs = []

    def generate_content(self, model, contents, config):
        system = config.system_instruction
        if system.startswith("You are the Lead of a research team that answers"):
            return text(json.dumps(self.plan))
        if system.startswith("You are the Lead of a research team studying"):
            return text(json.dumps(self.reviews.pop(0)))
        if system.startswith("You are the Writer"):
            self.writer_inputs.append(contents)
            return text(self.writer_texts.pop(0))
        if system.startswith("You are a Researcher"):
            return self._researcher(contents)
        raise AssertionError("unknown agent")

    def _researcher(self, contents):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.2)  # long enough to overlap if they really run in parallel
            asked = first_text(contents)
            if len(contents) == 1:  # first round: run a query
                with self.lock:
                    self.researcher_prompts.append(list(contents))
                sql = SQL_RIDERS if "ridership" in asked else SQL_MIX
                return call("run_sql", {"sql": sql, "purpose": "test"})
            qid = Q_RIDERS if "ridership" in asked else Q_MIX
            return text(f"FINDING: answer with a number 40 [{qid}].\nLIMITS: none")
        finally:
            with self.lock:
                self.active -= 1


@pytest.fixture(autouse=True)
def clean():
    sessions.clear()
    yield
    sessions.clear()
    llm.set_client(None)


def names(tool_calls):
    return [(c["agent"], c["name"]) for c in tool_calls]


def test_lead_fans_out_in_parallel_and_writer_cites():
    team = FakeTeam({"action": "research", "sub_questions": [
        "How did the ticket mix change?", "How did daily ridership change?", "Which tickets sold most?"]})
    llm.set_client(team)

    reply, calls = lead.run_turn("s1", "How did the January 2026 changes affect what riders buy?")

    assert team.max_active >= 2, "researchers did not overlap"
    agents = {c["agent"] for c in calls}
    assert {"lead", "researcher-1", "researcher-2", "researcher-3", "writer"} <= agents
    # Every Researcher started fresh: one message, only its own sub-question, no history.
    assert len(team.researcher_prompts) == 3
    for contents in team.researcher_prompts:
        assert len(contents) == 1 and "Sub-question:" in first_text(contents)
    # Each Researcher's SQL is in tool_calls under its own name.
    sql_calls = [c for c in calls if c["name"] == "run_sql"]
    assert {c["agent"] for c in sql_calls} == {"researcher-1", "researcher-2", "researcher-3"}
    # Writer's citation is valid and a Sources list was added from real queries.
    assert f"[{Q_MIX}]" in reply and "**Sources**" in reply and "ticket_sales" in reply
    verify = next(c for c in calls if c["name"] == "verify_citations")
    assert verify["result"] == {"cited": [Q_MIX], "invalid_removed": [], "revised": False}


def test_lead_goes_back_for_more_when_review_finds_a_gap():
    team = FakeTeam(
        {"action": "research", "sub_questions": ["How did the ticket mix change?"]},
        reviews=[{"done": False, "reason": "no ridership", "follow_up_questions": ["How did daily ridership change?"]},
                 {"done": True, "reason": "enough"}],
        writer_texts=[f"Mix [{Q_MIX}] and riders [{Q_RIDERS}]."],
    )
    llm.set_client(team)
    reply, calls = lead.run_turn("s2", "What changed?")

    dispatches = [c for c in calls if c["name"] == "dispatch_researchers"]
    assert [d["args"]["round"] for d in dispatches] == [1, 2]
    assert dispatches[1]["args"]["sub_questions"] == ["How did daily ridership change?"]
    assert ("researcher-2", "run_sql") in names(calls)   # follow-up got a new researcher
    assert Q_RIDERS in reply


def test_turn_cap_stops_follow_ups(monkeypatch):
    monkeypatch.setattr(lead, "MAX_ROUNDS", 2)
    gap = {"done": False, "reason": "more", "follow_up_questions": ["How did daily ridership change?"]}
    team = FakeTeam({"action": "research", "sub_questions": ["Ticket mix?"]}, reviews=[gap, gap, gap])
    llm.set_client(team)
    _, calls = lead.run_turn("s3", "What changed?")
    assert len([c for c in calls if c["name"] == "dispatch_researchers"]) == 2
    assert "cap" in [c for c in calls if c["name"] == "review"][-1]["result"]["reason"]


def test_vague_question_gets_a_clarifying_question_and_no_research():
    team = FakeTeam({"action": "clarify", "question": "Do you mean what riders buy, or what they pay?"})
    llm.set_client(team)
    reply, calls = lead.run_turn("s4", "Did things change?")
    assert reply.startswith("Do you mean")
    assert names(calls) == [("lead", "plan")]

    # The answer to the clarifying question is planned with the history.
    team.plan = {"action": "research", "sub_questions": ["How did the ticket mix change?"]}
    lead.run_turn("s4", "What they buy")
    assert len(sessions.get_history("s4")) == 4


def test_out_of_scope_says_what_it_cannot_answer():
    team = FakeTeam({"action": "out_of_scope", "reason": "The data covers the LIRR only, not the subway."})
    llm.set_client(team)
    reply, calls = lead.run_turn("s5", "How crowded is the 7 train?")
    assert "LIRR only" in reply and names(calls) == [("lead", "plan")]


def test_bad_plan_json_falls_back_to_researching_the_question():
    team = FakeTeam({"nonsense": True})
    llm.set_client(team)
    _, calls = lead.run_turn("s6", "How did the ticket mix change?")
    assert calls[0]["result"] == {"action": "research", "sub_questions": ["How did the ticket mix change?"]}


def test_writer_fixes_or_removes_made_up_citations():
    team = FakeTeam(
        {"action": "research", "sub_questions": ["How did the ticket mix change?"]},
        writer_texts=["Sales fell 50% [q_deadbeef].", "Still wrong [q_deadbeef] but this is real [%s]." % Q_MIX],
    )
    llm.set_client(team)
    reply, calls = lead.run_turn("s7", "How did the ticket mix change?")
    verify = next(c for c in calls if c["name"] == "verify_citations")["result"]
    assert verify["revised"] is True and verify["invalid_removed"] == ["q_deadbeef"]
    assert "q_deadbeef" not in reply and "[unverified]" in reply and Q_MIX in reply


def test_check_citations_rules():
    valid = {"q_11111111"}
    r = writer.check_citations("a 5 [q_11111111], b [q_22222222], fare [fare chart 2026-01-04], [MTA announcement]",
                               valid, fares_used=False)
    assert r["cited"] == ["q_11111111"]
    assert set(r["invalid"]) == {"q_22222222", "fare chart 2026-01-04"}  # no fare was looked up
    assert writer.check_citations("[fare chart 2026-01-04]", valid, fares_used=True)["invalid"] == []