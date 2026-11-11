"""Live progress events, so the front end can watch the research as it happens.

/chat/stream sets an emitter for its request; the agents call emit() as they work.
Researchers run in a thread pool, so dispatch() copies the context into each thread
(see lead.dispatch). When no emitter is set (plain /chat, tests), emit() does nothing.
"""

from __future__ import annotations

import contextvars
from typing import Callable

_emitter: contextvars.ContextVar[Callable[[dict], None] | None] = contextvars.ContextVar("emitter", default=None)


def set_emitter(fn: Callable[[dict], None] | None) -> contextvars.Token:
    return _emitter.set(fn)


def emit(event: dict) -> None:
    fn = _emitter.get()
    if fn is not None:
        try:
            fn(event)
        except Exception:  # a broken stream must never break the research
            pass


def step(entry: dict) -> None:
    """One trace entry ({agent, name, args, result}) as it happens."""
    emit({"type": "step", "entry": entry})


def phase(name: str, **info) -> None:
    """planning / researching / reviewing / writing, with any extra detail."""
    emit({"type": "phase", "phase": name, **info})
