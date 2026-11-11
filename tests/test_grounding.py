"""The number check: numbers next to a citation must come from the cited rows."""

from lirr_agent.agents.grounding import check_numbers
from lirr_agent.agents.researcher import Finding


def _finding(rows, qid="q_aaaaaaaa", fares=None):
    calls = [{"agent": "researcher-1", "name": "run_sql", "args": {},
              "result": {"query_id": qid, "columns": ["period", "sold_as", "n"], "rows": rows}}]
    if fares:
        calls.append({"agent": "researcher-1", "name": "lookup_fare", "args": {}, "result": {"fares": fares}})
    return Finding("R1", "researcher-1", "q", "FINDING: x", calls)


ROWS = [["2025", "ten_trip", 1242110], ["2026", "ten_trip", 0],
        ["2025", "one_way", 12210458], ["2026", "one_way", 13862886]]


def test_exact_rounded_and_scaled_numbers_are_grounded():
    text = ("Ten-trip sales fell from 1,242,110 to 0 in January 2026 [q_aaaaaaaa]. "
            "One-way tickets rose to 13.9 million [q_aaaaaaaa].")
    r = check_numbers(text, [_finding(ROWS)])
    assert r["unsupported"] == [] and r["grounded"] == 2  # "0" is skipped like other small counts


def test_derived_numbers_are_grounded():
    # 13,862,886 / 12,210,458 - 1 = 13.5% ; difference 1,652,428
    r = check_numbers("One-way sales grew 13.5% (1,652,428 more tickets) [q_aaaaaaaa].", [_finding(ROWS)])
    assert r["unsupported"] == []


def test_made_up_number_is_flagged_and_labels_are_skipped():
    text = "In zone 7 on 2026-01-04, one-way sales grew 42% to 15,000,000 [q_aaaaaaaa]."
    r = check_numbers(text, [_finding(ROWS)])
    assert {u["number"] for u in r["unsupported"]} == {"42%", "15,000,000"}
    assert r["checked"] == 2  # zone 7 and the date are labels, not claims


def test_fare_citations_check_against_fare_lookups():
    fares = [{"ticket_type": "monthly", "price_before_usd": 287.0, "price_after_usd": 299.75, "change_pct": 4.4}]
    text = ("A Zone 1 to 7 monthly went from $287.00 to $299.75, up 4.4% "
            "[fare chart 2023-08-20, fare chart 2026-01-04]. It is $310 now [fare chart 2026-01-04].")
    r = check_numbers(text, [_finding(ROWS, fares=fares)])
    assert [u["number"] for u in r["unsupported"]] == ["$310"]


def test_uncited_sentences_are_not_checked():
    assert check_numbers("Fares rose about 8% [MTA announcement]. Riders: 99.", [_finding(ROWS)])["checked"] == 0


def test_numbers_belong_to_the_citation_that_follows_them():
    text = ("Fares rose up to 4.5% [MTA announcement] while one-way sales reached 13,862,886 [q_aaaaaaaa], "
            "versus 77% claimed [q_aaaaaaaa].")
    r = check_numbers(text, [_finding(ROWS)])
    assert [u["number"] for u in r["unsupported"]] == ["77%"]


def test_tool_name_citations_become_fare_chart_citations():
    from lirr_agent.agents.writer import normalize_fare_citations
    assert normalize_fare_citations("up 4.4% [lookup_fare]. [q_aaaaaaaa]") == \
        "up 4.4% [fare chart 2023-08-20, fare chart 2026-01-04]. [q_aaaaaaaa]"


def test_clock_times_are_labels_and_fractions_match_percents():
    rows = [["peak", 0.2315472682174085], ["off_peak", 0.1821]]
    text = "Between 6:00 AM and 9:59 AM average occupancy was 23.2% [q_aaaaaaaa]."
    r = check_numbers(text, [_finding(rows)])
    assert r["unsupported"] == [] and r["checked"] == 1


def test_finding_references_are_dropped():
    from lirr_agent.agents.writer import normalize_fare_citations
    assert normalize_fare_citations("Peak is 6-10 AM [Finding R1]. Up 3% [q_aaaaaaaa, R2].") == \
        "Peak is 6-10 AM. Up 3% [q_aaaaaaaa]."
