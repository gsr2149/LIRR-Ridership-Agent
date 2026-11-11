FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app.py ./
COPY lirr_agent ./lirr_agent

# The database is not in git (~600 MB). At startup the app downloads it from
# GCS when LIRR_DB_GCS_URI is set (see lirr_agent/tools/db.py).
ENV LIRR_DB_PATH=/app/data/lirr.duckdb

# Cloud Run sets $PORT (default 8080).
CMD ["sh", "-c", "exec /app/.venv/bin/uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}"]
