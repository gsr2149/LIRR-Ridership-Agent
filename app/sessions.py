"""In-memory chat history per session_id (same idea as Project 1).

Only the user's messages and the final answers are kept, not the tool
traffic, so follow-up questions ("what about weekly tickets?") have context
without the history growing huge. Memory resets when the server restarts,
and each Cloud Run instance has its own memory.
"""

from __future__ import annotations

import threading

from google.genai import types

from .llm import model_content, user_content

MAX_TURNS = 10  # keep the last 10 question/answer pairs

_lock = threading.Lock()
_history: dict[str, list[types.Content]] = {}


def get_history(session_id: str) -> list[types.Content]:
    with _lock:
        return list(_history.get(session_id, []))


def add_turn(session_id: str, user_text: str, reply_text: str) -> None:
    with _lock:
        turns = _history.setdefault(session_id, [])
        turns.extend([user_content(user_text), model_content(reply_text or "(no answer)")])
        del turns[: max(0, len(turns) - 2 * MAX_TURNS)]


def clear() -> None:
    with _lock:
        _history.clear()