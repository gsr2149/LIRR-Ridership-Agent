"""The ticket advisor, on a tiny fare table with known answers."""

import duckdb
import pytest

from lirr_agent.tools import advisor, db, fares


@pytest.fixture
def fare_db(tmp_path, monkeypatch):
    path = tmp_path / "f.duckdb"
    con = duckdb.connect(str(path))
    con.execute("""CREATE TABLE lirr_fares AS SELECT * FROM (VALUES
      ('pre_2026', '1', '7', 'monthly', 287.00), ('pre_2026', '1', '7', 'weekly', 102.00),
      ('pre_2026', '1', '7', 'one_way_peak', 14.50), ('pre_2026', '1', '7', 'one_way_offpeak', 10.75),
      ('pre_2026', '1', '7', 'ten_trip_peak', 145.00), ('pre_2026', '1', '7', 'ten_trip_offpeak', 91.50),
      ('post_2026', '1', '7', 'monthly', 299.75), ('post_2026', '1', '7', 'weekly', 106.50),
      ('post_2026', '1', '7', 'one_way_peak', 15.25), ('post_2026', '1', '7', 'one_way_offpeak', 11.25),
      ('post_2026', '1', '7', 'day_pass_weekday', 27.50)
    ) AS t(fare_period, origin_zone, dest_zone, ticket_type, price_usd)""")
    con.execute("CREATE TABLE ticket_sales (railroad VARCHAR, origin_station_name VARCHAR, origin_station_zone INT,"
                " destination_station_name VARCHAR, destination_station_zone INT, number_of_tickets INT)")
    con.close()
    monkeypatch.setenv("LIRR_DB_PATH", str(path))
    db.reset_connection(); fares.station_zones.cache_clear(); fares.station_list.cache_clear()
    yield
    db.reset_connection(); fares.station_zones.cache_clear(); fares.station_list.cache_clear()


def test_daily_peak_commuter_pays_the_monthly_increase(fare_db):
    p = advisor.compare_ticket_costs("1", "7", 5, "peak")["pattern"]
    assert p["best_before"]["option"] == p["best_after"]["option"] == "Monthly ticket"
    assert p["change_pct"] == 4.4


def test_two_day_peak_rider_saves_with_the_day_pass(fare_db):
    p = advisor.compare_ticket_costs("1", "7", 2, "peak")["pattern"]
    assert p["best_before"]["option"] == "One-way tickets"
    assert p["best_after"]["option"] == "Weekday Day Pass" and p["change_pct"] < 0


def test_off_peak_occasional_rider_loses_the_ten_trip(fare_db):
    r = advisor.compare_ticket_costs("1", "7", 1, "off-peak")
    assert r["pattern"]["best_before"]["option"] == "Ten-trip tickets"
    assert r["pattern"]["change_pct"] == 23.0
    assert len(r["grid"]) == 10 and r["origin_name"] == "Zone 1"


def test_app_bonus_counts_every_eleventh_ride_free():
    # 12 rides in 14 days -> pay for 11
    assert round(advisor._app_one_ways(10.0, 6), 2) == round(11 * 10.0 * advisor.WEEKS_PER_MONTH / 2, 2)
