"""FastAPI app: POST /chat with the same request/response shape as Project 1.

    request:  {"message": "...", "session_id": "optional"}
    response: {"response": "...", "session_id": "...",
               "tool_calls": [{"agent": "...", "name": "...", "args": {...}, "result": {...}}]}

Run locally:  uvicorn app:app --reload
"""

import json
import logging
import queue
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # load .env before the agent reads env vars

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from . import agents, events  # noqa: E402
from .tools.fares import lookup_fare, station_list  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"

@asynccontextmanager
async def lifespan(_app):
    # Fetch the database from GCS if needed (Cloud Run), then describe every
    # table once, so the first question isn't slow.
    try:
        from .agents.context import schema_brief
        from .tools.db import ensure_database

        ensure_database()
        schema_brief()
    except Exception as e:  # e.g. database not built yet: the app still starts
        logging.getLogger("uvicorn.error").warning("Schema warm-up skipped: %s", e)
    yield


app = FastAPI(title="LIRR Fare Change Research Agent", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


def _friendly_error(e: Exception) -> tuple[int, str]:
    if "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e):
        return 429, "The research agent has hit its Gemini usage limit. Please try again in a minute."
    return 500, f"{type(e).__name__}: {e}"


@app.post("/chat/stream")
def chat_stream(req: ChatRequest):
    """Same answer as /chat, streamed as NDJSON so the page can show the research live.

    Lines: {"type": "phase"|"researcher_start"|"step"|"ping", ...} while working, then
    {"type": "done", "response", "session_id", "tool_calls"} (the /chat body) or
    {"type": "error", "status", "detail"}.
    """
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message is empty")
    session_id = req.session_id or str(uuid.uuid4())
    q: queue.Queue = queue.Queue()

    def work():
        events.set_emitter(q.put)  # this thread's context; dispatch copies it to Researchers
        try:
            reply, tool_calls = agents.run_turn(session_id, req.message)
            q.put({"type": "done", "response": reply, "session_id": session_id, "tool_calls": tool_calls})
        except Exception as e:
            status, detail = _friendly_error(e)
            q.put({"type": "error", "status": status, "detail": detail})
        finally:
            q.put(None)

    threading.Thread(target=work, daemon=True).start()

    def lines():
        yield json.dumps({"type": "start", "session_id": session_id}) + "\n"
        while True:
            try:
                item = q.get(timeout=15)
            except queue.Empty:
                yield json.dumps({"type": "ping"}) + "\n"  # keeps proxies from closing the stream
                continue
            if item is None:
                return
            yield json.dumps(item, default=str) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/stations")
def stations():
    """LIRR stations and fare zones (no model call)."""
    return {"stations": station_list()}


@app.get("/api/fare")
def fare(origin: str, destination: str):
    """Before/after fares for one trip, straight from the fare charts (no model call)."""
    return lookup_fare(origin, destination)


@app.post("/chat")
def chat(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message is empty")
    session_id = req.session_id or str(uuid.uuid4())
    try:
        reply, tool_calls = agents.run_turn(session_id, req.message)
    except Exception as e:
        status, detail = _friendly_error(e)
        raise HTTPException(status_code=status, detail=detail)
    return {"response": reply, "session_id": session_id, "tool_calls": tool_calls}
