"""FastAPI app: POST /chat with the same request/response shape as Project 1.

    request:  {"message": "...", "session_id": "optional"}
    response: {"response": "...", "session_id": "...",
               "tool_calls": [{"agent": "...", "name": "...", "args": {...}, "result": {...}}]}

Run locally:  uvicorn app.main:app --reload
"""

import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # load .env before the agent reads env vars

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from . import agents  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="LIRR Fare Change Research Agent")
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


@app.post("/chat")
def chat(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message is empty")
    session_id = req.session_id or str(uuid.uuid4())
    try:
        reply, tool_calls = agents.run_turn(session_id, req.message)
    except Exception as e:
        if "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e):
            raise HTTPException(
                status_code=429,
                detail="The research agent has hit its Gemini usage limit. Please try again later.",
            )
        raise HTTPException(status_code=500, detail=str(e))
    return {"response": reply, "session_id": session_id, "tool_calls": tool_calls}