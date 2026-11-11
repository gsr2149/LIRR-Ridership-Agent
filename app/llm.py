"""Gemini tool-calling loop shared by every agent (same approach as Project 1:
automatic function calling is off, so we run each tool ourselves and record it).

    result = run_agent(
        agent="researcher-1",
        system="You are ...",
        contents=[user_content("How did day pass sales change?")],
        tool_names=["run_sql", "describe_table"],
    )
    result.text        # the model's final answer
    result.tool_calls  # [{"agent", "name", "args", "result"}, ...]
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from google import genai
from google.genai import types

from .tools import run_tool, tool_specs

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
DEFAULT_MAX_ROUNDS = 8

_client: genai.Client | None = None


def client() -> genai.Client:
    global _client
    if _client is None:
        # Reads GEMINI_API_KEY from the environment. Tool calling needs the
        # v1beta API; newer SDK versions default to v1, which rejects tools.
        _client = genai.Client(
            http_options=types.HttpOptions(api_version=os.environ.get("GEMINI_API_VERSION", "v1beta"))
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
) -> AgentResult:
    """Call Gemini, run any tools it asks for, repeat until it answers in text.

    Every tool call is recorded with the agent's name so /chat can show who
    did what. After max_rounds, tools are switched off and the model must answer
    with what it has.
    """
    contents = list(contents)
    tool_calls: list[dict] = []
    tools = gemini_tools(tool_names)

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
        return types.GenerateContentConfig(**kwargs)

    for round_no in range(max_rounds + 1):
        allow_tools = round_no < max_rounds
        response = client().models.generate_content(
            model=MODEL, contents=contents, config=config(allow_tools)
        )
        calls = (response.function_calls or []) if allow_tools else []
        if not calls:
            return AgentResult(text=_text(response), tool_calls=tool_calls, contents=contents)

        contents.append(response.candidates[0].content)
        parts = []
        for call in calls:
            args = dict(call.args or {})
            result = run_tool(call.name, args)
            tool_calls.append({"agent": agent, "name": call.name, "args": args, "result": result})
            parts.append(types.Part.from_function_response(name=call.name, response=result))
        contents.append(types.Content(role="user", parts=parts))

    return AgentResult(text="", tool_calls=tool_calls, contents=contents)  # not reached


def _text(response) -> str:
    """Concatenate the text parts (avoids the SDK warning when other parts exist)."""
    try:
        parts = response.candidates[0].content.parts or []
    except (AttributeError, IndexError, TypeError):
        return ""
    return "".join(
        p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False)
    ).strip()