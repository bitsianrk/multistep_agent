"""MCP domain server: taxonomy (NER + product knowledge graph)."""

from __future__ import annotations

import re
from datetime import date, timedelta
from difflib import SequenceMatcher

from ...data.taxonomy import BRANDS, CATEGORIES, psgs_for_brand
from ._common import SkillContext, SkillError, require_brand

OBJECTIVE_PATTERNS = [
    ("reactivate", r"\b(lapsed|win[\s-]?back|reactivat\w*|re-?engag\w*|bring back|lost shoppers)\b"),
    ("trial", r"\b(trial|new shoppers|new customers|acquir\w*|acquisition|recruit\w*|non-?buyers|penetration)\b"),
    ("retain", r"\b(retain|retention|loyal\w*|protect|defend)\b"),
    ("grow", r"\b(grow\w*|frequency|spend more|increase spend|basket|upsell)\b"),
]
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\- ]", " ", text.lower())).strip()


def _next_monday(d: date) -> date:
    return d + timedelta(days=(7 - d.weekday()) % 7)


def parse_objective(text: str) -> str | None:
    t = text.lower()
    for objective, pattern in OBJECTIVE_PATTERNS:
        if re.search(pattern, t):
            return objective
    return None


def parse_window(text: str, as_of: date, default_weeks: int, lead_days: int) -> dict:
    t = text.lower()
    weeks, start, assumed = None, None, []
    m = re.search(r"\b(\d{1,2})[\s-]*(?:weeks?|wks?)\b", t)
    if m:
        weeks = int(m.group(1))
    m = re.search(r"\b(20\d\d-\d\d-\d\d)\b", t)
    if m:
        start = date.fromisoformat(m.group(1))
    else:
        m = (re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", t)
             or re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?\b", t))
        if m:
            a, b = m.groups()
            day, mon = (int(a), MONTHS[b]) if a.isdigit() else (int(b), MONTHS[a])
            start = date(as_of.year, mon, day)
            if start < as_of:
                start = date(as_of.year + 1, mon, day)
        elif "christmas" in t or "xmas" in t or "festive" in t:
            start = _next_monday(date(as_of.year, 11, 30) if as_of < date(as_of.year, 11, 30)
                                 else date(as_of.year + 1, 11, 30))
        elif "new year" in t or "january" in t:
            start = _next_monday(date(as_of.year + 1, 1, 1))
        elif "next week" in t:
            start = _next_monday(as_of + timedelta(days=1))
    if start is None:
        start = _next_monday(as_of + timedelta(days=lead_days))
        assumed.append("start")
    else:
        start = start - timedelta(days=start.weekday())  # campaign weeks start on Monday
    if weeks is None:
        weeks = default_weeks
        assumed.append("weeks")
    return {"start": start.isoformat(), "weeks": weeks, "assumed": assumed}


def _brand_matches(text: str) -> list[dict]:
    t = f" {_norm(text)} "
    hits = []
    for brand_id, spec in BRANDS.items():
        for alias in sorted(spec["aliases"], key=len, reverse=True):
            if f" {alias} " in t:
                hits.append({"brand_id": brand_id, "name": spec["name"], "alias": alias,
                             "match": "exact", "score": 1.0 if alias == spec["name"].lower() else 0.8})
                break
    # A longer alias match ("golden harvest") suppresses brands matched only by
    # a shorter alias contained in it ("golden").
    longest = [h for h in hits if " " in h["alias"]]
    hits = [h for h in hits if h in longest or not any(h["alias"] in L["alias"] for L in longest)]
    if hits:
        return hits
    tokens = t.split()
    grams = tokens + [" ".join(tokens[i:i + 2]) for i in range(len(tokens) - 1)]
    for brand_id, spec in BRANDS.items():
        best = max((SequenceMatcher(None, g, a).ratio() for g in grams for a in spec["aliases"]), default=0)
        if best >= 0.85:
            hits.append({"brand_id": brand_id, "name": spec["name"], "alias": None,
                         "match": "fuzzy", "score": round(best * 0.8, 2)})
    return hits


def _category_matches(text: str) -> list[str]:
    t = f" {_norm(text)} "
    return [c for c, spec in CATEGORIES.items() if any(f" {a} " in t for a in spec["aliases"])]


def ner_extract(ctx: SkillContext, text: str) -> dict:
    brands = _brand_matches(text)
    cats = _category_matches(text)
    if cats and len(brands) > 1:
        narrowed = [b for b in brands if BRANDS[b["brand_id"]]["category"] in cats]
        brands = narrowed or brands
    return {
        "brand_mentions": brands,
        "category_mentions": cats,
        "objective": parse_objective(text),
        "window": parse_window(text, ctx.settings.as_of, ctx.settings.default_window_weeks,
                               ctx.settings.default_lead_days),
    }


def taxonomy_lookup(ctx: SkillContext, brand_id: str) -> dict:
    spec = require_brand(brand_id)
    return {
        "brand_id": brand_id,
        "name": spec["name"],
        "category_id": spec["category"],
        "category_name": CATEGORIES[spec["category"]]["name"],
        "supplier": spec["supplier"],
        "psgs": [{"psg_id": p["psg_id"], "name": p["name"]} for p in psgs_for_brand(brand_id)],
    }


def disambiguate(ctx: SkillContext, text: str, candidates: list[str]) -> dict:
    if not candidates:
        raise SkillError("candidates must not be empty")
    t = _norm(text)
    m = re.fullmatch(r"(?:option\s*)?(\d)", t)
    if m and 1 <= int(m.group(1)) <= len(candidates):
        return {"brand_id": candidates[int(m.group(1)) - 1], "confidence": 1.0, "method": "choice"}
    found = [h["brand_id"] for h in _brand_matches(text) if h["brand_id"] in candidates]
    if len(found) == 1:
        return {"brand_id": found[0], "confidence": 0.95, "method": "name"}
    cats = _category_matches(text)
    by_cat = [c for c in candidates if BRANDS[c]["category"] in cats]
    if len(by_cat) == 1:
        return {"brand_id": by_cat[0], "confidence": 0.9, "method": "category"}
    return {"brand_id": None, "confidence": 0.0, "method": "unresolved"}
