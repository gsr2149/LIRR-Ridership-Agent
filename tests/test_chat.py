"""Tests for the Gemini loop and the /chat endpoint, with a fake Gemini client
(no API key or network needed).

    python -m pytest -q
"""

import pytest
from fastapi.testclient import TestClient
from google.genai import types

from app import agents, llm, sessions
from app.main import app
from test_tools import fake_db  # noqa: F401  (reuses the tiny fake database)


def text_response(text):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part.from_text(text=text)]))])


def call_response(name, args):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part(
            function_call=types.FunctionCall(name=name, args=args))]))])


class FakeGemini:
    """Returns scripted responses in order and remembers what it was sent."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.requests.append({"contents": list(contents), "config": config})
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def clean_sessions():
    sessions.clear()
    yield
    sessions.clear()
    llm.set_client(None)


SQL = "SELECT sold_as, sum(number_of_tickets) AS n FROM ticket_sales WHERE railroad = 'LIRR' GROUP BY 1 ORDER BY n DESC"


def test_run_agent_runs_tools_and_records_agent_name():
    fake = FakeGemini([
        call_response("run_sql", {"sql": SQL, "purpose": "ticket mix"}),
        text_response("Ten-trip led with 40 tickets [q_x]."),
    ])
    llm.set_client(fake)
    result = llm.run_agent("researcher-1", "system", [llm.user_content("hi")], ["run_sql"])

    assert result.text == "Ten-trip led with 40 tickets [q_x]."
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call["agent"] == "researcher-1" and call["name"] == "run_sql"
    assert call["result"]["rows"][0] == ["ten_trip", 40]
    # The tool result was sent back to Gemini on the second request.
    sent = fake.requests[1]["contents"][-1].parts[0].function_response
    assert sent.name == "run_sql" and sent.response["rows"][0] == ["ten_trip", 40]


def test_run_agent_stops_calling_tools_after_max_rounds():
    fake = FakeGemini([call_response("list_tables", {})] * 2 + [text_response("done")])
    llm.set_client(fake)
    result = llm.run_agent("a", "s", [llm.user_content("hi")], ["list_tables"], max_rounds=2)
    assert result.text == "done" and len(result.tool_calls) == 2
    last_config = fake.requests[-1]["config"]
    assert last_config.tool_config.function_calling_config.mode == "NONE"


def test_tool_declarations_match_the_registry():
    decls = llm.gemini_tools(["run_sql", "list_tables"])[0].function_declarations
    assert {d.name for d in decls} == {"run_sql", "list_tables"}
    run_sql = next(d for d in decls if d.name == "run_sql")
    assert run_sql.parameters_json_schema["required"] == ["sql", "purpose"]


def test_chat_endpoint_keeps_project_1_shape_and_session_memory():
    fake = FakeGemini([
        call_response("lookup_fare", {"origin": "1", "destination": "7", "ticket_type": "monthly"}),
        text_response("A Zone 1-7 monthly is $299.75 [fare chart 2026-01-04]."),
        text_response("Second answer."),
    ])
    llm.set_client(fake)
    client = TestClient(app)

    r = client.post("/chat", json={"message": "What does a zone 7 monthly cost?"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"response", "session_id", "tool_calls"}
    assert body["response"].startswith("A Zone 1-7 monthly is $299.75")
    assert body["tool_calls"][0] == {
        "agent": agents.single.AGENT_NAME,
        "name": "lookup_fare",
        "args": {"origin": "1", "destination": "7", "ticket_type": "monthly"},
        "result": body["tool_calls"][0]["result"],
    }
    assert body["tool_calls"][0]["result"]["fares"][0]["price_usd"] == 299.75

    # Same session: the earlier question and answer are sent as history.
    r2 = client.post("/chat", json={"message": "And weekly?", "session_id": body["session_id"]})
    assert r2.json()["session_id"] == body["session_id"]
    history = fake.requests[-1]["contents"]
    assert [c.role for c in history] == ["user", "model", "user"]
    assert history[0].parts[0].text == "What does a zone 7 monthly cost?"


def test_chat_endpoint_errors():
    client = TestClient(app)
    assert client.post("/chat", json={"message": "  "}).status_code == 400

    class Exhausted:
        models = None

        def __init__(self):
            self.models = self

        def generate_content(self, **_):
            raise RuntimeError("429 RESOURCE_EXHAUSTED")

    llm.set_client(Exhausted())
    assert client.post("/chat", json={"message": "hi"}).status_code == 429


def test_home_page_and_health():
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}
    assert "LIRR" in client.get("/").text