"""Number grounding: does every number the Writer cites actually come from the data?

The citation check (writer.check_citations) proves each [q_xxxxxxxx] is a query that
ran. This goes one step further: for every sentence that cites a query, each number in
that sentence must match a value in the cited query's result rows, or be one simple
step away from them (difference, ratio, percent change, share). Numbers cited to a fare
chart must match a fare lookup. Code does the checking, not the model.

    check_numbers(text, findings) -> {"checked": 12, "grounded": 11, "unsupported": [...]}
"""

from __future__ import annotations

import itertools
import re

from .context import FARE_CITATIONS
from .researcher import Finding

# A number as written: $1,234.56 / 12.5% / 3.8 million / 640K
NUM_RE = re.compile(
    r"(?<![\w.])(?P<cur>\$)?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s?(?P<unit>%|percent|percentage points?|pp|million|M\b|billion|B\b|thousand|[Kk]\b)?"
)
QID_RE = re.compile(r"\bq_[0-9a-f]{8}\b")
CITE_RE = re.compile(r"\[([^\[\]]+)\]")
FARE_LOWER = {c.lower() for c in FARE_CITATIONS}
SEGMENT_RE = re.compile(r"[^\n]+")          # one line (a bullet or paragraph line)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z*])")
SCALE = {"million": 1e6, "m": 1e6, "billion": 1e9, "b": 1e9, "thousand": 1e3, "k": 1e3}
MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|december|"
          "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")
SKIP_BEFORE = re.compile(rf"(zone|zones|q_|round|top|{MONTHS})\s*$", re.I)
MAX_VALUES_FOR_PAIRS = 120   # keep the derived-number search small


def _parse(m: re.Match) -> tuple[float, int, str]:
    raw = m.group("num").replace(",", "")
    decimals = len(raw.split(".")[1]) if "." in raw else 0
    unit = (m.group("unit") or "").lower()
    value = float(raw) * SCALE.get(unit, 1)
    return value, decimals, unit


def _is_label(text: str, m: re.Match, value: float, unit: str) -> bool:
    """Years, dates, zone numbers, list markers: not claims about the data."""
    if unit or m.group("cur"):
        return False
    before = text[:m.start()]
    if SKIP_BEFORE.search(before[-12:]):
        return True
    if value.is_integer() and 1990 <= value <= 2035:
        return True                                  # a year
    if re.match(r"\s*[-–/]\s*\d", text[m.end():m.end() + 3]) and value <= 31:
        return True                                  # a date part like 2026-01-04 or 1/4
    if re.match(r"[-–]\s*(day|week|month|year|trip|zone)", text[m.end():m.end() + 8], re.I):
        return True                                  # "ten-trip", "30-day"
    if value.is_integer() and value <= 12 and not re.search(r"\d", text[m.end():m.end() + 2]):
        return True                                  # small counts like "3 researchers", list items
    return False


def _close(a: float, b: float, decimals: int, scaled: bool) -> bool:
    """Is data value b written as a (allowing for rounding to the shown precision)?"""
    tol = 0.5 * 10 ** (-decimals) + 1e-9
    if scaled:                                       # "3.8 million" may round 3,812,004
        tol = max(tol * (abs(a) / max(round(abs(a)), 1)), abs(a) * 0.006)
    return abs(abs(a) - abs(b)) <= tol


def _values(rows: list) -> list[float]:
    out = []
    for row in rows:
        for v in (row if isinstance(row, (list, tuple)) else [row]):
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                out.append(float(v))
            elif isinstance(v, str):
                try:
                    out.append(float(v.replace(",", "")))
                except ValueError:
                    pass
    return out


def _derived(values: list[float]) -> list[float]:
    """Numbers one step from the data: differences, ratios, percent changes and shares."""
    vals = sorted(set(values), key=abs, reverse=True)[:MAX_VALUES_FOR_PAIRS]
    out = []
    for a, b in itertools.permutations(vals, 2):
        out.append(a - b)
        if b:
            out += [a / b, a / b * 100, (a - b) / b * 100]
    total = sum(v for v in vals if v > 0)
    if total:
        out += [v / total * 100 for v in vals]
    return out


def _evidence(findings: list[Finding]) -> tuple[dict[str, list[float]], list[float]]:
    by_qid = {q["query_id"]: _values(q.get("rows", [])) for f in findings for q in f.queries}
    fare_values = []
    for f in findings:
        for c in f.tool_calls:
            if c["name"] == "lookup_fare" and "error" not in c["result"]:
                for row in c["result"].get("fares", []):
                    fare_values += [float(v) for k, v in row.items()
                                    if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return by_qid, fare_values


def check_numbers(text: str, findings: list[Finding]) -> dict:
    by_qid, fare_values = _evidence(findings)
    derived_cache: dict[tuple, list[float]] = {}
    checked, grounded, unsupported = 0, 0, []

    sentences = [s for line in SEGMENT_RE.findall(text) for s in SENTENCE_RE.split(line)]
    for sentence in sentences:
        if sentence.strip().startswith(("- [q_", "- [fare chart", "- [MTA", "**Sources")):
            continue                                 # the generated Sources list
        # Each number belongs to the citation that follows it (or the last one in the sentence).
        cites = list(CITE_RE.finditer(sentence))
        if not cites:
            continue
        start = 0
        for i, cite in enumerate(cites):
            end = cite.start() if i < len(cites) - 1 else len(sentence)
            chunk_text = sentence[start:end]
            start = cite.end()
            tokens = [t.strip() for t in cite.group(1).split(",")]
            qids = [t for t in tokens if t in by_qid]
            cites_fares = any(t.lower() in FARE_LOWER for t in tokens)
            if not qids and not cites_fares:
                continue                             # background or invalid citation: not checked here
            pool = [v for q in qids for v in by_qid[q]] + (fare_values if cites_fares else [])
            key = tuple(sorted(qids)) + (("fares",) if cites_fares else ())
            plain = CITE_RE.sub(lambda m: " " * len(m.group(0)), chunk_text)  # hide citations
            for m in NUM_RE.finditer(plain):
                value, decimals, unit = _parse(m)
                if _is_label(plain, m, value, unit):
                    continue
                checked += 1
                scaled = unit in SCALE
                if any(_close(value, v, decimals, scaled) for v in pool):
                    grounded += 1
                    continue
                if key not in derived_cache:
                    derived_cache[key] = _derived(pool)
                if any(_close(value, v, decimals, scaled) for v in derived_cache[key]):
                    grounded += 1
                    continue
                unsupported.append({"number": m.group(0).strip(), "cited": list(key),
                                    "sentence": sentence.strip()[:200]})
    return {"checked": checked, "grounded": grounded, "unsupported": unsupported}
