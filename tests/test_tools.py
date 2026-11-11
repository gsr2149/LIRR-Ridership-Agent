"""Tests for lirr_agent/tools. They build a tiny fake database with the same columns
as the real one, so they run in a second and don't need the 17M-row file.

    python -m pytest -q
"""

import duckdb
import pytest

from lirr_agent import tools
from lirr_agent.tools import db, fares, run_tool, tool_specs
from lirr_agent.tools.sql import query_id, validate


@pytest.fixture(autouse=True)
def fake_db(tmp_path, monkeypatch):
    path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(path))
    con.execute("""
        CREATE TABLE ticket_sales AS SELECT * FROM (VALUES
          (TIMESTAMP '2025-12-01', 'LIRR', 'ten_trip', 'peak',     'Penn Station', 1, 'Hicksville', 7, 'TVM',    40),
          (TIMESTAMP '2025-12-01', 'LIRR', 'monthly',  'peak',     'Penn Station', 1, 'Hicksville', 7, 'mobile', 10),
          (TIMESTAMP '2026-01-10', 'LIRR', 'day_pass', 'off_peak', 'Penn Station', 1, 'Hicksville', 7, 'mobile', 25),
          (TIMESTAMP '2026-01-10', 'MNR',  'one_way',  'peak',     'Grand Central', 1, 'Harlem-125 St', 1, 'TVM', 99)
        ) AS t(date, railroad, sold_as, fare_type, origin_station_name, origin_station_zone,
               destination_station_name, destination_station_zone, device_class_type, number_of_tickets)
    """)
    con.execute("CREATE TABLE daily_ridership AS SELECT TIMESTAMP '2026-01-05' AS date, 'LIRR' AS mode, 250000 AS count")
    con.execute("""CREATE TABLE lirr_fares AS SELECT * FROM (VALUES
          ('post_2026', DATE '2026-01-04', '1', '7', 'monthly', 299.75::DECIMAL(8,2), 'chart.pdf'),
          ('post_2026', DATE '2026-01-04', '1', '7', 'one_way_peak', 15.25::DECIMAL(8,2), 'chart.pdf'),
          ('post_2026', DATE '2026-01-04', '7', '1', 'monthly', 299.75::DECIMAL(8,2), 'chart.pdf'),
          ('pre_2026', DATE '2023-08-20', '1', '7', 'monthly', 287.00::DECIMAL(8,2), 'old.pdf'),
          ('pre_2026', DATE '2023-08-20', '1', '7', 'ten_trip_peak', 145.00::DECIMAL(8,2), 'old.pdf')
        ) AS t(fare_period, effective_date, origin_zone, dest_zone, ticket_type, price_usd, source_doc)""")
    con.execute("""CREATE TABLE sources AS SELECT 'ticket_sales' AS table_name, '5ypk-fceg' AS dataset_id,
          'MTA Railroad Ticket Sales' AS title, 'https://data.ny.gov/d/5ypk-fceg' AS url,
          NULL AS row_filter, 'now' AS fetched_at, 4 AS row_count""")
    con.close()

    monkeypatch.setenv("LIRR_DB_PATH", str(path))
    db.reset_connection()
    fares.station_zones.cache_clear()
    yield
    db.reset_connection()
    fares.station_zones.cache_clear()


# ---- registry ---------------------------------------------------------------

def test_every_tool_has_a_spec():
    names = {t["name"] for t in tool_specs()}
    assert names == {"list_tables", "describe_table", "run_sql", "lookup_fare"}
    for spec in tool_specs():
        assert spec["description"] and spec["input_schema"]["type"] == "object"
        assert "fn" not in spec


def test_unknown_tool_and_bad_args_return_errors_not_exceptions():
    assert "error" in run_tool("nope", {})
    assert "error" in run_tool("describe_table", {})  # missing 'table'


# ---- schema tools -----------------------------------------------------------

def test_list_tables_includes_sources_and_notes():
    result = run_tool("list_tables")
    by_name = {t["table"]: t for t in result["tables"]}
    assert by_name["ticket_sales"]["row_count"] == 4
    assert by_name["ticket_sales"]["source"]["url"].endswith("5ypk-fceg")
    assert any("railroad = 'LIRR'" in n for n in by_name["ticket_sales"]["notes"])


def test_describe_table_lists_categorical_values():
    result = run_tool("describe_table", {"table": "ticket_sales"})
    cols = {c["column"]: c for c in result["columns"]}
    assert set(cols["sold_as"]["values"]) == {"ten_trip", "monthly", "day_pass", "one_way"}
    assert cols["date"]["min"].startswith("2025-12-01")
    assert "error" in run_tool("describe_table", {"table": "not_a_table"})


# ---- run_sql ----------------------------------------------------------------

def test_run_sql_returns_rows_and_stable_query_id():
    sql = ("SELECT sold_as, sum(number_of_tickets) AS n FROM ticket_sales "
           "WHERE railroad = 'LIRR' GROUP BY 1 ORDER BY n DESC")
    r1 = run_tool("run_sql", {"sql": sql, "purpose": "ticket mix"})
    assert r1["columns"] == ["sold_as", "n"]
    assert r1["rows"] == [["ten_trip", 40], ["day_pass", 25], ["monthly", 10]]
    assert r1["tables"] == ["ticket_sales"]
    r2 = run_tool("run_sql", {"sql": "  " + sql.replace(" ", "  "), "purpose": "again"})
    assert r1["query_id"] == r2["query_id"] == query_id(sql)


def test_run_sql_makes_dates_and_decimals_json_safe():
    r = run_tool("run_sql", {"sql": "SELECT effective_date, price_usd FROM lirr_fares LIMIT 1", "purpose": "t"})
    assert r["rows"] == [["2026-01-04", 299.75]]


@pytest.mark.parametrize("bad", [
    "DROP TABLE ticket_sales",
    "SELECT 1; DROP TABLE ticket_sales",
    "INSERT INTO sources VALUES (1)",
    "SELECT * FROM read_csv('/etc/passwd')",
    "ATTACH 'x.db'",
    "",
])
def test_run_sql_refuses_writes_and_file_access(bad):
    assert validate(bad) is not None
    assert "error" in run_tool("run_sql", {"sql": bad, "purpose": "attack"})


def test_run_sql_allows_forbidden_words_inside_strings_and_columns():
    assert validate("SELECT * FROM ticket_sales WHERE sold_as = 'update'") is None
    assert validate("WITH x AS (SELECT 1 AS a) SELECT a FROM x") is None


def test_run_sql_reports_sql_errors():
    r = run_tool("run_sql", {"sql": "SELECT no_such_column FROM ticket_sales", "purpose": "t"})
    assert "error" in r and "no_such_column" in r["error"]


def test_run_sql_caps_rows(monkeypatch):
    monkeypatch.setattr(tools.sql, "MAX_ROWS", 3)
    r = run_tool("run_sql", {"sql": "SELECT * FROM range(10)", "purpose": "t"})
    assert r["row_count"] == 3 and r["truncated"] is True


# ---- lookup_fare ------------------------------------------------------------

def test_lookup_fare_by_zone_and_by_station():
    by_zone = run_tool("lookup_fare", {"origin": "1", "destination": "7", "ticket_type": "monthly"})
    by_name = run_tool("lookup_fare", {"origin": "penn station", "destination": "Hicksvile"})  # typo on purpose
    assert by_zone["fares"] == [{"ticket_type": "monthly", "price_before_usd": 287.0,
                                 "price_after_usd": 299.75, "change_pct": 4.4}]
    assert by_name["destination_zone"] == "7"
    assert {f["ticket_type"] for f in by_name["fares"]} == {"monthly", "one_way_peak", "ten_trip_peak"}


def test_lookup_fare_marks_discontinued_and_missing_prices():
    fares_by_type = {f["ticket_type"]: f for f in run_tool("lookup_fare", {"origin": "1", "destination": "7"})["fares"]}
    assert fares_by_type["ten_trip_peak"]["price_after_usd"] is None
    assert "discontinued" in fares_by_type["ten_trip_peak"]["status"]
    assert "no pre-2026 price" in fares_by_type["one_way_peak"]["status"]
    ten = run_tool("lookup_fare", {"origin": "1", "destination": "7", "ticket_type": "ten_trip"})
    assert ten["fares"][0]["price_before_usd"] == 145.0


def test_lookup_fare_explains_what_it_cannot_answer():
    assert "Day Pass" in run_tool("lookup_fare", {"origin": "1", "destination": "7", "ticket_type": "round_trip"})["error"]
    assert "not an LIRR fare zone" in run_tool("lookup_fare", {"origin": "2", "destination": "7"})["error"]
    assert "Unknown LIRR station" in run_tool("lookup_fare", {"origin": "Narnia", "destination": "7"})["error"]

def test_ensure_database_downloads_from_gcs(tmp_path, monkeypatch):
    """With LIRR_DB_GCS_URI set and no local file, the database is fetched once."""
    import sys
    import types as pytypes

    calls = []

    class FakeBlob:
        def download_to_filename(self, filename, raw_download=False):
            calls.append(filename)
            with open(filename, "wb") as fh:
                fh.write(b"duckdb bytes")

    class FakeClient:
        def bucket(self, name):
            assert name == "my-bucket"
            return pytypes.SimpleNamespace(blob=lambda name: FakeBlob() if name == "dir/lirr.duckdb" else None)

    fake_storage = pytypes.SimpleNamespace(Client=FakeClient)
    monkeypatch.setitem(sys.modules, "google.cloud.storage", fake_storage)
    import google.cloud
    monkeypatch.setattr(google.cloud, "storage", fake_storage, raising=False)

    target = tmp_path / "sub" / "lirr.duckdb"
    monkeypatch.setenv("LIRR_DB_PATH", str(target))
    monkeypatch.setenv("LIRR_DB_GCS_URI", "gs://my-bucket/dir/lirr.duckdb")

    assert db.ensure_database() == target
    assert target.read_bytes() == b"duckdb bytes"
    assert db.ensure_database() == target  # already there: no second download
    assert len(calls) == 1
