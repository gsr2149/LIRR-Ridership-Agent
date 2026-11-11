"""run_sql: read-only SQL over the LIRR DuckDB, with a row cap, a timeout,
and a stable query id so the Writer can cite exactly which query a number
came from."""

from __future__ import annotations

import hashlib
import re
import threading
import time

from .db import connection, to_json_value

MAX_ROWS = 200
TIMEOUT_SECONDS = 30

KNOWN_TABLES = ["ticket_sales", "daily_ridership", "monthly_ridership",
                "stop_occupancy", "lirr_fares", "sources", "ticket_spend_est"]

# Anything that could change data, touch files or the network is refused.
# (The connection is read-only too; this gives the model a clear error.)
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|create|alter|attach|detach|copy|export|import|"
    r"pragma|install|load|set|reset|call|checkpoint|vacuum|grant|revoke|truncate|"
    r"read_csv|read_csv_auto|read_parquet|read_json|read_json_auto|read_text|glob)\b",
    re.IGNORECASE,
)


def query_id(sql: str) -> str:
    """Same SQL -> same id, so citations stay stable across turns."""
    normalized = " ".join(sql.split()).lower()
    return "q_" + hashlib.sha1(normalized.encode()).hexdigest()[:8]


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", " ", sql)
    return re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)


def validate(sql: str) -> str | None:
    """Return an error message, or None if the query is allowed."""
    body = _strip_comments(sql).strip().rstrip(";").strip()
    if not body:
        return "Empty query."
    if ";" in body:
        return "Only one statement per call."
    if not re.match(r"^(select|with)\b", body, re.IGNORECASE):
        return "Only SELECT (or WITH ... SELECT) queries are allowed."
    # Ignore words inside string literals, e.g. WHERE sold_as = 'day_pass'.
    without_strings = re.sub(r"'(?:[^']|'')*'", "''", body)
    m = FORBIDDEN.search(without_strings)
    if m:
        return f"'{m.group(1)}' is not allowed. This tool is read-only."
    return None


def tables_used(sql: str) -> list[str]:
    return [t for t in KNOWN_TABLES if re.search(rf"\b{t}\b", sql, re.IGNORECASE)]


def run_sql(sql: str, purpose: str = "") -> dict:
    """Run one read-only query. Returns columns, up to MAX_ROWS rows, and metadata."""
    qid = query_id(sql)
    base = {"query_id": qid, "sql": sql.strip(), "purpose": purpose,
            "tables": tables_used(sql)}

    error = validate(sql)
    if error:
        return {**base, "error": error}

    body = _strip_comments(sql).strip().rstrip(";")

    cur = connection().cursor()
    timer = threading.Timer(TIMEOUT_SECONDS, cur.interrupt)
    started = time.perf_counter()
    timer.start()
    try:
        result = cur.execute(body)
        columns = [d[0] for d in result.description]
        rows = result.fetchmany(MAX_ROWS + 1)  # keeps the query's own ORDER BY
    except Exception as e:  # DuckDB raises many error types; give the model the message
        msg = str(e)
        if "INTERRUPT" in msg.upper():
            msg = f"Query took longer than {TIMEOUT_SECONDS}s. Aggregate more or filter by date."
        return {**base, "error": msg.splitlines()[0][:500]}
    finally:
        timer.cancel()
        cur.close()

    truncated = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    return {
        **base,
        "columns": columns,
        "rows": [[to_json_value(v) for v in r] for r in rows],
        "row_count": len(rows),
        "truncated": truncated,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }