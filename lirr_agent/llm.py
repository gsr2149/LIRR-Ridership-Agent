"""Gemini calls shared by every agent (same approach as Project 1: automatic
function calling is off, so we run each tool ourselves and record it).

    result = run_agent(
        agent="researcher-1",
        system="You are ...",
        contents=[user_content("How did day pass sales change?")],
        tool_names=["run_sql", "describe_table"],
    )
    result.text        # the model's final answer
    result.tool_calls  # [{"agent", "name", "args", "result"}, ...]

    plan = ask_json(system="...", contents=[...])   # a dict parsed from JSON
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field

from google import genai
from google.genai import types

from . import events
from .tools import run_tool, tool_specs

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
DEFAULT_MAX_ROUNDS = 8
RETRY_WAITS = [5, 15, 30]  # seconds to wait after a 429/503 before trying again
# Gemini "thinks" before answering, which adds seconds to every call. Our agents
# get structured prompts and data, so thinking is off by default (0). Set
# GEMINI_THINKING_BUDGET to e.g. 512 to turn some back on, or "" to use Gemini's default.
THINKING_BUDGET = os.environ.get("GEMINI_THINKING_BUDGET", "0")
# Gemini 3 models take a level instead of a budget: minimal, low, medium or high.
THINKING_LEVEL = os.environ.get("GEMINI_THINKING_LEVEL", "minimal")
REQUEST_TIMEOUT_MS = int(os.environ.get("GEMINI_TIMEOUT_MS", "90000"))  # one hung call can't eat the whole answer

log = logging.getLogger("uvicorn.error")  # shows up in the uvicorn terminal

_client: genai.Client | None = None


def client() -> genai.Client:
    global _client
    if _client is None:
        if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true"):
            # Cloud Run: Gemini through Vertex AI, billed to the GCP project and
            # authenticated as the service account (no API key). Reads
            # GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION (use "global").
            credentials = None
            if os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN"):  # local dev: `gcloud auth print-access-token`
                from google.oauth2.credentials import Credentials

                credentials = Credentials(os.environ["GOOGLE_OAUTH_ACCESS_TOKEN"])
            _client = genai.Client(credentials=credentials,
                                   http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))
        else:
            # Gemini API key (GEMINI_API_KEY). Tool calling needs the v1beta API;
            # newer SDK versions default to v1, which rejects tools.
            _client = genai.Client(
                http_options=types.HttpOptions(
                    api_version=os.environ.get("GEMINI_API_VERSION", "v1beta"), timeout=REQUEST_TIMEOUT_MS
                )
            )
    return _client


def set_client(fake) -> None:
    """Tests use this to swap in a fake Gemini client."""
    global _client
    _client = fake


def user_content(text: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part.from_text(text=text)])


def model_content(text: str) -> types.Content:
    return types.Content(role="model", parts=[types.Part.from_text(text=text)])


def gemini_tools(tool_names: list[str] | None) -> list[types.Tool]:
    """Turn our JSON-Schema tool specs into Gemini function declarations."""
    if not tool_names:
        return []
    decls = []
    for spec in tool_specs():
        if spec["name"] not in tool_names:
            continue
        kwargs = {"name": spec["name"], "description": spec["description"]}
        if spec["input_schema"].get("properties"):
            kwargs["parameters_json_schema"] = spec["input_schema"]
        decls.append(types.FunctionDeclaration(**kwargs))
    return [types.Tool(function_declarations=decls)]


def _is_retryable(e: Exception) -> bool:
    msg = str(e)
    return any(s in msg for s in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "504", "DEADLINE_EXCEEDED"))


def _generate(model: str, contents, config):
    """generate_content with a few retries when Gemini says 'slow down'.

    Several Researchers run at once, so the free tier's per-minute limit is
    easy to hit; waiting a bit usually fixes it. A daily limit will still fail
    after the retries, and /chat turns that into a 429 for the user.
    """
    for wait in RETRY_WAITS + [None]:
        try:
            return client().models.generate_content(model=model, contents=contents, config=config)
        except Exception as e:
            if wait is None or not _is_retryable(e):
                raise
            log.warning("Gemini rate limit/busy (%s); waiting %ss before retrying", str(e)[:80], wait)
            time.sleep(wait)


def _thinking_config(model: str):
    if "gemini-3" in model:
        level = THINKING_LEVEL.strip()
        return types.ThinkingConfig(thinking_level=level) if level else None
    if THINKING_BUDGET.strip() == "" or "2.5" not in model:
        return None
    budget = int(THINKING_BUDGET)
    if "pro" in model:
        budget = max(budget, 128)  # 2.5 Pro can't turn thinking off completely
    return types.ThinkingConfig(thinking_budget=budget)


@dataclass
class AgentResult:
    text: str
    tool_calls: list[dict] = field(default_factory=list)
    contents: list[types.Content] = field(default_factory=list)


def run_agent(
    agent: str,
    system: str,
    contents: list[types.Content],
    tool_names: list[str] | None = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    response_json: bool = False,
    model: str | None = None,
) -> AgentResult:
    """Call Gemini, run any tools it asks for, repeat until it answers in text.

    Every tool call is recorded with the agent's name so /chat can show who
    did what. After max_rounds, tools are switched off and the model must answer
    with what it has.
    """
    contents = list(contents)
    tool_calls: list[dict] = []
    tools = gemini_tools(tool_names)
    model = model or MODEL

    def config(allow_tools: bool) -> types.GenerateContentConfig:
        kwargs = {
            "system_instruction": system,
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
        }
        if tools:
            kwargs["tools"] = tools
            if not allow_tools:
                kwargs["tool_config"] = types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(mode="NONE")
                )
        if response_json:
            kwargs["response_mime_type"] = "application/json"
        thinking = _thinking_config(model)
        if thinking:
            kwargs["thinking_config"] = thinking
        return types.GenerateContentConfig(**kwargs)

    for round_no in range(max_rounds + 1):
        allow_tools = round_no < max_rounds
        response = _generate(model, contents, config(allow_tools))
        calls = (response.function_calls or []) if allow_tools else []
        if not calls:
            return AgentResult(text=_text(response), tool_calls=tool_calls, contents=contents)

        contents.append(response.candidates[0].content)
        parts = []
        for call in calls:
            args = dict(call.args or {})
            result = run_tool(call.name, args)
            entry = {"agent": agent, "name": call.name, "args": args, "result": result}
            tool_calls.append(entry)
            events.step(entry)
            parts.append(types.Part.from_function_response(name=call.name, response=result))
        contents.append(types.Content(role="user", parts=parts))

    return AgentResult(text="", tool_calls=tool_calls, contents=contents)  # not reached


def ask_json(system: str, contents: list[types.Content], model: str | None = None) -> dict:
    """One Gemini call (no tools) that must answer with a JSON object."""
    result = run_agent("json", system, contents, tool_names=None, response_json=True, model=model)
    return parse_json(result.text)


def parse_json(text: str) -> dict:
    """Parse a JSON object from model output, tolerating ```json fences or extra prose."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return {}
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _text(response) -> str:
    """Concatenate the text parts (avoids the SDK warning when other parts exist)."""
    try:
        parts = response.candidates[0].content.parts or []
    except (AttributeError, IndexError, TypeError):
        return ""
    return "".join(
        p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False)
    ).strip()
