"""Entry point: the FastAPI app lives in lirr_agent/main.py.

    uv run uvicorn app:app --reload      # local dev
    uv run python app.py                 # same, without reload (uses $PORT, default 8080)
"""

import os

from lirr_agent.main import app

__all__ = ["app"]

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
