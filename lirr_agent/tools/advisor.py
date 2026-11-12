"""Which ticket should I buy? The cheapest way to ride a trip, before and after the change.

The ten-trip ticket is gone, so riders who don't commute every day have to work out
their best option again. For one trip (two stations or zones) and a riding pattern
(days a week, peak or off-peak), this prices every way to ride for a month at the fares
in force before (2023-08-20) and after (2026-01-04) the change, and picks the cheapest.

Assumptions, stated to the user too: one round trip per riding day, on weekdays;
a month is 52/12 weeks; weekly and monthly tickets are unlimited; the weekday Day Pass
covers peak trains; in the TrainTime app every 11th one-way bought within 14 days is
free (new in 2026). Fares only: no employer benefits or other discounts.
"""

from __future__ import annotations

from .db import connection, to_json_value
from .fares import display_name, resolve_zone

WEEKS_PER_MONTH = 52 / 12
TIMES = ("peak", "offpeak")
APP_BONUS_NOTE = "TrainTime app: buy 10 one-ways within 14 days and the 11th is free"


def _prices(o_zone: str, d_zone: str) -> dict[str, dict[str, float]]:
    rows = connection().cursor().execute(
        "SELECT fare_period, ticket_type, price_usd FROM lirr_fares WHERE origin_zone = ? AND dest_zone = ?",
        [o_zone, d_zone],
    ).fetchall()
    out: dict[str, dict[str, float]] = {"pre_2026": {}, "post_2026": {}}
    for period, t, p in rows:
        out[period][t] = to_json_value(p)
    return out


def _app_one_ways(price: float, rides_per_week: float) -> float:
    """Monthly cost of one-ways bought in the app, where every 11th within 14 days is free."""
    rides_14 = round(rides_per_week * 2)
    paid_14 = rides_14 - rides_14 // 11
    return paid_14 * price * WEEKS_PER_MONTH / 2


def options_for(prices: dict[str, float], days: int, time: str, period: str) -> list[dict]:
    """Every way to ride for a month at one period's prices, cheapest first."""
    rides = 2 * days  # one round trip per riding day
    ow = prices.get(f"one_way_{time}")
    opts = []
    if ow is not None:
        opts.append(("One-way tickets", rides * WEEKS_PER_MONTH * ow, f"{rides} rides a week at ${ow:.2f}"))
    if period == "post_2026" and ow is not None:
        opts.append(("One-ways in the TrainTime app", _app_one_ways(ow, rides), APP_BONUS_NOTE))
    tt = prices.get(f"ten_trip_{time}")
    if period == "pre_2026" and tt is not None:
        opts.append(("Ten-trip tickets", rides * WEEKS_PER_MONTH * tt / 10, f"${tt:.2f} for 10 rides"))
    dp = prices.get("day_pass_weekday")
    if period == "post_2026" and dp is not None:
        opts.append(("Weekday Day Pass", days * WEEKS_PER_MONTH * dp, f"${dp:.2f} a day, unlimited rides"))
    if prices.get("weekly") is not None:
        opts.append(("Weekly ticket", WEEKS_PER_MONTH * prices["weekly"], f"${prices['weekly']:.2f} a week"))
    if prices.get("monthly") is not None:
        opts.append(("Monthly ticket", prices["monthly"], f"${prices['monthly']:.2f} a month"))
    opts.sort(key=lambda o: o[1])
    per_month = rides * WEEKS_PER_MONTH
    return [{"option": name, "monthly_cost_usd": round(cost, 2), "per_ride_usd": round(cost / per_month, 2), "how": how}
            for name, cost, how in opts]


def _compare(prices: dict, days: int, time: str) -> dict:
    before = options_for(prices["pre_2026"], days, time, "pre_2026")
    after = options_for(prices["post_2026"], days, time, "post_2026")
    row = {"days_per_week": days, "time": time,
           "round_trips_per_month": round(days * WEEKS_PER_MONTH, 1),
           "rides_per_month": round(2 * days * WEEKS_PER_MONTH, 1),
           "best_before": before[0] if before else None, "best_after": after[0] if after else None}
    if before and after:
        b, a = before[0]["monthly_cost_usd"], after[0]["monthly_cost_usd"]
        row["change_usd"] = round(a - b, 2)
        row["change_pct"] = round((a - b) / b * 100, 1)
    return row


def compare_ticket_costs(origin: str, destination: str, days_per_week: int = 5, time: str = "peak") -> dict:
    """Cheapest monthly cost of a riding pattern before vs after, plus every option, plus a
    grid of how the change hit each pattern (1-5 days a week x peak/off-peak) on this trip."""
    o_zone, o_how = resolve_zone(origin)
    d_zone, d_how = resolve_zone(destination)
    if o_zone is None or d_zone is None:
        return {"error": " ".join(m for z, m in [(o_zone, o_how), (d_zone, d_how)] if z is None)}
    time = "offpeak" if str(time).lower().replace("-", "").replace("_", "").replace(" ", "") == "offpeak" else "peak"
    days = max(1, min(5, int(days_per_week)))

    prices = _prices(o_zone, d_zone)
    pattern = _compare(prices, days, time)
    result = {
        "origin_name": display_name(origin, o_zone), "destination_name": display_name(destination, d_zone),
        "origin_zone": o_zone, "destination_zone": d_zone,
        "pattern": pattern,
        "options_before": options_for(prices["pre_2026"], days, time, "pre_2026"),
        "options_after": options_for(prices["post_2026"], days, time, "post_2026"),
        "grid": [_compare(prices, d, t) for t in TIMES for d in range(1, 6)],
        "assumptions": [
            "One round trip per riding day, on weekdays; a month is 52/12 weeks.",
            "Weekly and monthly tickets are unlimited; the weekday Day Pass covers peak trains.",
            APP_BONUS_NOTE + " (new in 2026).",
            "Fares only, from the charts effective 2023-08-20 and 2026-01-04: no employer benefits or other discounts.",
        ],
    }
    if not result["options_before"]:
        result["assumptions"].append("The 2023 fare table has no prices for this zone pair, so there is no 'before'.")
    return result
