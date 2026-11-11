"""Shared read-only connection to the DuckDB file built by data/build_db.py."""

from __future__ import annotations

import datetime as dt
import decimal
import os
import threading
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "data" / "lirr.duckdb"

_lock = threading.Lock()
_conn: duckdb.DuckDBPyConnection | None = None


def db_path() -> Path:
    return Path(os.environ.get("LIRR_DB_PATH", DEFAULT_DB))


def connection() -> duckdb.DuckDBPyConnection:
    """One read-only connection per process, opened on first use.

    Each caller should use .cursor() on it: cursors are safe to use from
    several threads at once (the Researchers will run in parallel).
    """
    global _conn
    with _lock:
        if _conn is None:
            path = db_path()
            if not path.exists():
                raise FileNotFoundError(
                    f"Database not found at {path}. Run: python data/fetch.py && python data/build_db.py"
                )
            _conn = duckdb.connect(str(path), read_only=True)
        return _conn


def reset_connection() -> None:
    """Close the shared connection (used by tests that point at another file)."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None


def to_json_value(v):
    """Make DuckDB values JSON-safe (dates, decimals) for the /chat response."""
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    return v