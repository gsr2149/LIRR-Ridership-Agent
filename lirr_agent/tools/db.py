"""Shared read-only connection to the DuckDB file built by data/build_db.py."""

from __future__ import annotations

import datetime as dt
import decimal
import logging
import os
import threading
import time
from pathlib import Path

import duckdb

log = logging.getLogger("uvicorn.error")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "data" / "lirr.duckdb"

_lock = threading.Lock()
_conn: duckdb.DuckDBPyConnection | None = None


def db_path() -> Path:
    return Path(os.environ.get("LIRR_DB_PATH", DEFAULT_DB))


def ensure_database() -> Path:
    """Make sure the DuckDB file is on local disk.

    The database (~600 MB) is too big for GitHub, so in Cloud Run it is pulled
    from GCS at startup: set LIRR_DB_GCS_URI=gs://bucket/path/lirr.duckdb.
    Locally, a file already at LIRR_DB_PATH (default data/lirr.duckdb) is used as is.
    """
    path = db_path()
    uri = os.environ.get("LIRR_DB_GCS_URI", "").strip()
    if path.exists() or not uri:
        return path
    if not uri.startswith("gs://") or "/" not in uri[5:]:
        raise ValueError(f"LIRR_DB_GCS_URI must look like gs://bucket/object, got {uri!r}")
    from google.cloud import storage  # imported here so local runs don't need GCP auth

    bucket_name, blob_name = uri[5:].split("/", 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    t0 = time.monotonic()
    blob = storage.Client().bucket(bucket_name).blob(blob_name)
    blob.download_to_filename(str(tmp), raw_download=True)
    tmp.replace(path)  # atomic: never leave a half-written database at `path`
    log.info("Downloaded %s (%.0f MB) in %.1fs", uri, path.stat().st_size / 1e6, time.monotonic() - t0)
    return path


def connection() -> duckdb.DuckDBPyConnection:
    """One read-only connection per process, opened on first use.

    Each caller should use .cursor() on it: cursors are safe to use from
    several threads at once (the Researchers will run in parallel).
    """
    global _conn
    with _lock:
        if _conn is None:
            path = ensure_database()
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