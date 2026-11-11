"""Turn the query results an answer cites into small chart specs for the front end.

Code picks the chart, not the model, so a chart always shows real rows from a query
the answer cites. A spec is plain JSON the page can draw with any chart library:

    {"query_id": "q_1a2b3c4d", "type": "line" | "bar", "title": "...",
     "x": "month", "labels": ["2025-02", ...],
     "series": [{"name": "day_pass", "values": [...]}, ...], "y_label": "share_pct"}
"""

from __future__ import annotations

import re

from .researcher import Finding

MAX_CHARTS = 2
MAX_POINTS = 60
MAX_SERIES = 8
DATE_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?")
NOT_A_MEASURE = re.compile(r"(^|_)(days?|n_days|days_count|num_days|rank|zone|id|year|month)($|_)", re.I)
DELTA = re.compile(r"change|diff|delta|growth|increase|decrease|_pp$|^pp_", re.I)
PREFERRED = re.compile(r"share|pct|percent|rate|occupancy|spend|usd|riders|ridership|tickets|count|total", re.I)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _label(v) -> str:
    s = "—" if v is None else str(v)
    return s[:10] if DATE_RE.match(s) and s[10:].strip("T :0") == "" else s


def chart_from_query(q: dict) -> dict | None:
    cols, rows = q.get("columns") or [], q.get("rows") or []
    if len(cols) < 2 or not (2 <= len(rows) <= MAX_POINTS * MAX_SERIES):
        return None
    numeric = [i for i in range(len(cols)) if all(_is_num(r[i]) or r[i] is None for r in rows)
               and any(_is_num(r[i]) for r in rows)]
    text = [i for i in range(len(cols)) if i not in numeric]
    numeric = [i for i in numeric if not NOT_A_MEASURE.search(cols[i])]
    if not numeric or not text:
        return None
    # Prefer a meaningful measure (share, spend, tickets...) and the last such column.
    levels = [i for i in numeric if not DELTA.search(cols[i])]  # levels chart better than deltas
    numeric = levels or numeric
    preferred = [i for i in numeric if PREFERRED.search(cols[i])]
    shares = [i for i in preferred if re.search(r"share|pct|percent", cols[i], re.I)]
    y = (shares or preferred or numeric)[-1]
    x = text[0]
    is_time = bool(all(DATE_RE.match(str(r[x])) for r in rows if r[x] is not None)
                   or re.search(r"date|month|week|year", cols[x], re.I))

    if len(text) >= 2:  # long format, e.g. (month, sold_as, tickets): one series per category
        group = text[1]
        if not is_time and len({r[x] for r in rows}) < len({r[group] for r in rows}):
            x, group = group, x  # put the column with fewer distinct values in the legend
        labels = list(dict.fromkeys(_label(r[x]) for r in rows))
        groups = list(dict.fromkeys(_label(r[group]) for r in rows))
        if len(labels) > MAX_POINTS or len(groups) > MAX_SERIES:
            return None
        table = {(_label(r[x]), _label(r[group])): r[y] for r in rows}
        series = [{"name": g, "values": [table.get((lab, g)) for lab in labels]} for g in groups]
        y_label = cols[y]
    else:  # wide format: one series per numeric column of similar size
        labels = [_label(r[x]) for r in rows]
        if len(labels) > MAX_POINTS:
            return None
        top = max(abs(r[y]) for r in rows if _is_num(r[y])) or 1
        same_kind = [i for i in numeric if bool(re.search(r"share|pct|percent", cols[i], re.I))
                     == bool(re.search(r"share|pct|percent", cols[y], re.I))]
        ys = [i for i in same_kind
              if (m := max((abs(r[i]) for r in rows if _is_num(r[i])), default=0)) and 0.05 <= m / top <= 20]
        ys = ys[:4] or [y]
        series = [{"name": cols[i], "values": [r[i] for r in rows]} for i in ys]
        y_label = cols[y] if len(ys) == 1 else ""

    if len(labels) < 3 and len(series) < 2:
        return None  # two bars of one number say no more than the sentence does
    return {
        "query_id": q["query_id"],
        "type": "line" if is_time and len(labels) >= 3 else "bar",
        "title": (q.get("purpose") or "Query result")[:120],
        "x": cols[x],
        "labels": labels,
        "series": series,
        "y_label": y_label,
    }


def charts_for_answer(answer: str, findings: list[Finding]) -> list[dict]:
    """Charts for the cited queries, in the order the answer first cites them."""
    by_id = {q["query_id"]: q for f in findings for q in f.queries}
    order = list(dict.fromkeys(re.findall(r"\bq_[0-9a-f]{8}\b", answer)))
    charts = []
    for qid in order:
        if qid in by_id and (spec := chart_from_query(by_id[qid])):
            charts.append(spec)
        if len(charts) == MAX_CHARTS:
            break
    return charts
