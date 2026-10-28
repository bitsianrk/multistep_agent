"""Shared helpers for skill bindings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ...config import Settings
from ...data import DataStore
from ...data.taxonomy import BRANDS


@dataclass
class SkillContext:
    """What a skill binding may touch: settings and the data layer. Agents
    never receive this object; only the registry passes it to bindings."""

    settings: Settings
    store: DataStore


class SkillError(ValueError):
    """Raised by a binding for bad input (unknown brand, bad window...)."""


def require_brand(brand_id: str) -> dict:
    if brand_id not in BRANDS:
        raise SkillError(f"unknown brand_id {brand_id!r}")
    return BRANDS[brand_id]


def pct(num: float, den: float, nd: int = 1) -> float | None:
    return round(100.0 * num / den, nd) if den else None


def change_pct(now: float, before: float, nd: int = 1) -> float | None:
    return round(100.0 * (now - before) / before, nd) if before else None


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    pos = (len(v) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (pos - lo), 2)


def window_weeks_list(window_start: str, window_weeks: int) -> list[date]:
    try:
        start = date.fromisoformat(window_start)
    except (TypeError, ValueError) as exc:
        raise SkillError(f"window_start must be ISO date, got {window_start!r}") from exc
    if not 1 <= int(window_weeks) <= 26:
        raise SkillError("window_weeks must be between 1 and 26")
    return [start + timedelta(weeks=i) for i in range(int(window_weeks))]


def window_end(window_start: str, window_weeks: int) -> date:
    return date.fromisoformat(window_start) + timedelta(weeks=int(window_weeks), days=-1)


def diag_period(store: DataStore) -> dict:
    start = store.weeks[-6]
    end = store.weeks[-1] + timedelta(days=6)
    return {"start": start.isoformat(), "end": end.isoformat(), "weeks": 6}


def suppress(n: int, ctx: SkillContext) -> bool:
    return n < ctx.settings.min_cell_size
