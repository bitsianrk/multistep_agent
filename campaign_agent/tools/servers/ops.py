"""MCP domain server: ops (stock, live promotions, seasonality, campaign mart).

Stock and promotions are the request-time inputs: in production these are
live API calls; here they read the dummy snapshot taken at T-1 09:00.
"""

from __future__ import annotations

from datetime import date, timedelta
from statistics import mean

from ._common import ToolContext, ToolError, require_brand, window_end, window_weeks_list


def _index_for(series: list[float], weeks: list[date], window: list[date]) -> float:
    """Average over the past years of (window-week sales / that year's 52-wk mean)."""
    target_weeks = {min(w.isocalendar()[1], 52) for w in window}
    ratios = []
    n = len(series)
    for year_end in range(n, 51, -52):  # each 52-week block, newest first
        block = series[year_end - 52:year_end]
        block_weeks = weeks[year_end - 52:year_end]
        base = mean(block)
        vals = [s for s, wk in zip(block, block_weeks) if min(wk.isocalendar()[1], 52) in target_weeks]
        if vals and base:
            ratios.append(mean(vals) / base)
    return round(mean(ratios), 3) if ratios else 1.0


def seasonality_index(ctx: ToolContext, brand_id: str, window_start: str, window_weeks: int,
                      level: str = "brand") -> dict:
    require_brand(brand_id)
    window = window_weeks_list(window_start, window_weeks)
    out = {
        "brand_id": brand_id,
        "window": {"start": window_start, "weeks": window_weeks},
        "history_weeks": len(ctx.store.weeks),
        "seasonality_index": _index_for(ctx.store.feat_brand_week[brand_id], ctx.store.weeks, window),
    }
    if level == "psg":
        out["by_psg"] = {
            p: _index_for(ctx.store.feat_psg_week[p], ctx.store.weeks, window)
            for p, spec in ctx.store.psgs.items() if spec["brand_id"] == brand_id
        }
    elif level != "brand":
        raise ToolError("level must be 'brand' or 'psg'")
    return out


def multistep_history(ctx: ToolContext, brand_id: str, cohort: str | None = None) -> dict:
    spec = require_brand(brand_id)
    camps = [c for c in ctx.store.campaign_mart if c["category_id"] == spec["category"]]
    out = {"brand_id": brand_id, "campaigns_on_brand": 0, "campaigns_in_category": len(camps),
           "by_cohort": {}}
    agg: dict[str, dict[str, list[float]]] = {}
    for c in camps:
        if c["brand_id"] == brand_id:
            out["campaigns_on_brand"] += 1
        for coh, res in c["by_cohort"].items():
            if cohort and coh != cohort:
                continue
            a = agg.setdefault(coh, {"participation_pct": [], "completion_pct": [],
                                     "incremental_spend_per_hh": [], "on_brand": []})
            for k in ("participation_pct", "completion_pct", "incremental_spend_per_hh"):
                a[k].append(res[k])
            a["on_brand"].append(1.0 if c["brand_id"] == brand_id else 0.0)
    for coh, a in agg.items():
        out["by_cohort"][coh] = {
            "campaigns": len(a["participation_pct"]),
            "participation_pct": round(mean(a["participation_pct"]), 1),
            "completion_pct": round(mean(a["completion_pct"]), 1),
            "incremental_spend_per_hh": round(mean(a["incremental_spend_per_hh"]), 2),
        }
    out["campaign_ids"] = [c["campaign_id"] for c in camps if c["brand_id"] == brand_id]
    return out


def stock_check(ctx: ToolContext, brand_id: str, window_start: str, window_weeks: int) -> dict:
    require_brand(brand_id)
    window_weeks_list(window_start, window_weeks)
    need = round(window_weeks * ctx.settings.stock_cover_min_ratio, 1)
    items = []
    for p in ctx.store.psgs.values():
        if p["brand_id"] != brand_id:
            continue
        items.append({"psg_id": p["psg_id"], "name": p["name"], "forecast_cover_weeks": p["stock_cover_weeks"],
                      "required_cover_weeks": need, "ok": p["stock_cover_weeks"] >= need})
    return {"brand_id": brand_id, "checked_at": ctx.store.source_as_of["stock_api"], "psgs": items}


def promo_clash_check(ctx: ToolContext, brand_id: str, window_start: str, window_weeks: int) -> dict:
    require_brand(brand_id)
    w_start = date.fromisoformat(window_start)
    w_end = window_end(window_start, window_weeks)
    clashes = []
    for promo in ctx.store.live_promos:
        p = ctx.store.psgs.get(promo["psg_id"])
        if not p or p["brand_id"] != brand_id:
            continue
        s, e = date.fromisoformat(promo["start"]), date.fromisoformat(promo["end"])
        if s <= w_end and e >= w_start:
            overlap = (min(e, w_end) - max(s, w_start) + timedelta(days=1)).days
            clashes.append({**promo, "name": p["name"], "overlap_days": overlap})
    return {"brand_id": brand_id, "checked_at": ctx.store.source_as_of["promotions_api"],
            "window": {"start": window_start, "end": w_end.isoformat()}, "clashes": clashes}
