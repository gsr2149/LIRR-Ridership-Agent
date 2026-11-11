from lirr_agent.agents.charts import chart_from_query, charts_for_answer
from lirr_agent.agents.researcher import Finding


def q(cols, rows, qid="q_aaaaaaaa"):
    return {"query_id": qid, "purpose": "test", "columns": cols, "rows": rows}


def test_long_format_becomes_one_series_per_category():
    spec = chart_from_query(q(["month", "sold_as", "tickets"], [
        ["2025-12-01T00:00:00", "ten_trip", 100], ["2025-12-01T00:00:00", "one_way", 900],
        ["2026-01-01T00:00:00", "one_way", 1000], ["2026-02-01T00:00:00", "one_way", 1100],
        ["2026-02-01T00:00:00", "day_pass", 50]]))
    assert spec["type"] == "line" and spec["labels"] == ["2025-12-01", "2026-01-01", "2026-02-01"]
    assert {s["name"]: s["values"] for s in spec["series"]}["ten_trip"] == [100, None, None]


def test_category_bar_chart_and_unchartable_results():
    spec = chart_from_query(q(["device_class_type", "share_pct", "days_count"],
                              [["mobile", 61.2, 90], ["TVM", 30.1, 90], ["TOM", 8.7, 90]]))
    assert spec["type"] == "bar" and spec["series"] == [{"name": "share_pct", "values": [61.2, 30.1, 8.7]}]
    assert chart_from_query(q(["period", "riders"], [["2025", 5], ["2026", 6]])) is None  # two bars, one series
    assert chart_from_query(q(["n"], [[5], [6]])) is None          # nothing to put on the x axis
    assert chart_from_query(q(["a", "n"], [["x", 1]])) is None     # a single point


def test_charts_follow_citation_order():
    calls = [{"name": "run_sql", "result": q(["k", "n"], [["a", 1], ["b", 2], ["c", 3]], qid)}
             for qid in ("q_11111111", "q_22222222", "q_33333333")]
    f = Finding("R1", "researcher-1", "q", "s", calls)
    specs = charts_for_answer("x [q_33333333] y [q_11111111] z [q_22222222]", [f])
    assert [s["query_id"] for s in specs] == ["q_33333333", "q_11111111"]


def test_wide_charts_skip_change_columns_and_mixed_units():
    spec = chart_from_query(q(["sold_as", "share_pre", "share_post", "pct_volume_change", "tickets_post"], [
        ["one_way", 77.2, 92.2, 16.1, 31258499], ["round_trip", 14.1, 0.01, -99.9, 3946],
        ["day_pass", 0.0, 5.9, None, 2000652]]))
    assert [s["name"] for s in spec["series"]] == ["share_pre", "share_post"]
