"""MCP domain server: transactions (Clubcard, T-1 feature tables).

Blocks 1-5 of the Diagnostic Pack plus audience sizing. Every output is an
aggregate; cells below the minimum cell size are suppressed.
"""

from __future__ import annotations

from collections import defaultdict

from ...data.taxonomy import BRANDS, LIFESTAGES, LOYALTY_TIERS, VALUE_SEGMENTS
from ._common import (
    SkillContext,
    SkillError,
    change_pct,
    diag_period,
    pct,
    percentile,
    require_brand,
    suppress,
)

ACTIVE_TIERS = ("light", "medium", "heavy")
COHORTS = ("lapsed", "light", "medium", "heavy", "non_buyers")


def _active(rows):
    return [r for r in rows if r["trips_6w"] > 0]


def _category_shoppers(ctx: SkillContext, category_id: str) -> set[int]:
    return {r["hh_id"] for r in ctx.store.rows_for_category(category_id) if r["trips_6w"] > 0}


def brand_shoppers(ctx: SkillContext, brand_id: str, cut: str | None = None) -> dict:
    spec = require_brand(brand_id)
    rows = ctx.store.rows_for_brand(brand_id)
    active = _active(rows)
    shoppers = len(active)
    prior = sum(1 for r in rows if r["trips_prior_6w"] > 0)
    lapsed = [r for r in rows if r["tier"] == "lapsed"]
    cat = _category_shoppers(ctx, spec["category"])
    brand_hh = {r["hh_id"] for r in active}
    ly = ctx.store.brand_summary[brand_id]["shoppers_6w_ly"]
    out = {
        "brand_id": brand_id,
        "period": diag_period(ctx.store),
        "shoppers_6w": shoppers,
        "shoppers_prior_6w": prior,
        "shoppers_6w_ly": ly,
        "change_vs_prior_pct": change_pct(shoppers, prior),
        "yoy_pct": change_pct(shoppers, ly),
        "category_shoppers_6w": len(cat),
        "penetration_pct": pct(shoppers, len(cat)),
        "lapsed_pool": len(lapsed),
        "headroom_households": len(cat - brand_hh),
        "sales_6w_brand_week": round(sum(ctx.store.feat_brand_week[brand_id][-6:]), 2),
    }
    if cut == "lapsed_recency":
        bands = {"7-12 wks": 0, "13-18 wks": 0, "19-26 wks": 0}
        for r in lapsed:
            w = r["weeks_since_last"]
            bands["7-12 wks" if w <= 12 else "13-18 wks" if w <= 18 else "19-26 wks"] += 1
        out["lapsed_by_recency"] = {k: (None if suppress(v, ctx) else v) for k, v in bands.items()}
        out["lapsed_prior_spend_6w"] = round(sum(r["spend_prior_6w"] for r in lapsed), 2)
    elif cut is not None:
        raise SkillError(f"unknown cut {cut!r}; supported: lapsed_recency")
    return out


def brand_spc(ctx: SkillContext, brand_id: str, cohort: str | None = None) -> dict:
    require_brand(brand_id)
    rows = ctx.store.rows_for_brand(brand_id)
    if cohort in (None, "all"):
        sel, label = _active(rows), "all"
    elif cohort in ACTIVE_TIERS:
        sel, label = [r for r in rows if r["tier"] == cohort], cohort
    elif cohort == "lapsed":
        lapsed = [r for r in rows if r["tier"] == "lapsed"]
        n = len(lapsed)
        if suppress(n, ctx):
            return {"brand_id": brand_id, "cohort": "lapsed", "suppressed": True}
        sales = sum(r["spend_prior_6w"] for r in lapsed)
        trips = sum(r["trips_prior_6w"] for r in lapsed)
        return {
            "brand_id": brand_id, "cohort": "lapsed", "shoppers": n, "suppressed": False,
            "spc": 0.0, "spc_prior_6w": round(sales / n, 2),
            "frequency_prior_6w": round(trips / n, 2),
            "note": "Lapsed households have no brand spend in the window; prior-6-week SPC shown.",
        }
    else:
        raise SkillError(f"unknown cohort {cohort!r}")
    n = len(sel)
    if suppress(n, ctx):
        return {"brand_id": brand_id, "cohort": label, "suppressed": True}
    sales = sum(r["spend_6w"] for r in sel)
    trips = sum(r["trips_6w"] for r in sel)
    hh = ctx.store.households
    abv = sum(hh[r["hh_id"]]["basket_value"] * r["trips_6w"] for r in sel) / trips
    return {
        "brand_id": brand_id,
        "cohort": label,
        "suppressed": False,
        "period": diag_period(ctx.store),
        "shoppers": n,
        "trips": trips,
        "sales_6w": round(sales, 2),
        "spc": round(sales / n, 2),
        "frequency": round(trips / n, 2),
        "spend_per_trip": round(sales / trips, 2),
        "avg_basket_value": round(abv, 2),
    }


def _split(ctx, rows, key_fn, levels):
    counts, sales = defaultdict(int), defaultdict(float)
    for r in rows:
        k = key_fn(r)
        counts[k] += 1
        sales[k] += r["spend_6w"]
    n, total = sum(counts.values()), sum(sales.values())
    out = []
    for lvl in levels:
        c = counts.get(lvl, 0)
        if suppress(c, ctx):
            out.append({"cohort": lvl, "households": None, "shoppers_pct": None,
                        "sales_pct": None, "suppressed": True})
        else:
            out.append({"cohort": lvl, "households": c, "shoppers_pct": pct(c, n, 2),
                        "sales_pct": pct(sales.get(lvl, 0.0), total, 2), "suppressed": False})
    return out


def cohort_split(ctx: SkillContext, brand_id: str) -> dict:
    require_brand(brand_id)
    rows = ctx.store.rows_for_brand(brand_id)
    active = _active(rows)
    hh = ctx.store.households
    return {
        "brand_id": brand_id,
        "period": diag_period(ctx.store),
        # Loyalty tier is over the brand base (active + lapsed) so the four tiers sum to 100%.
        "loyalty_tier": _split(ctx, rows, lambda r: r["tier"], LOYALTY_TIERS),
        "lifestage": _split(ctx, active, lambda r: hh[r["hh_id"]]["lifestage"], LIFESTAGES),
        "value_segment": _split(ctx, active, lambda r: hh[r["hh_id"]]["value_segment"], VALUE_SEGMENTS),
        "clubcard_plus": _split(ctx, active, lambda r: "Plus" if hh[r["hh_id"]]["clubcard_plus"] else "Standard",
                                ["Plus", "Standard"]),
    }


def spend_distribution(ctx: SkillContext, brand_id: str, cohort: str | None = None) -> dict:
    require_brand(brand_id)
    rows = ctx.store.rows_for_brand(brand_id)
    tiers = [cohort] if cohort else ["all", *ACTIVE_TIERS]
    out = {"brand_id": brand_id, "period": diag_period(ctx.store), "by_cohort": {}}
    for tier in tiers:
        if tier == "lapsed":
            sel = [r for r in rows if r["tier"] == "lapsed"]
            spends = [r["spend_prior_6w"] for r in sel]
            basis = "prior_6w"
        elif tier in ("all", *ACTIVE_TIERS):
            sel = _active(rows) if tier == "all" else [r for r in rows if r["tier"] == tier]
            spends = [r["spend_6w"] for r in sel]
            basis = "6w"
        else:
            raise SkillError(f"unknown cohort {tier!r}")
        if suppress(len(sel), ctx):
            out["by_cohort"][tier] = {"suppressed": True}
            continue
        intervals = [42.0 / r["trips_6w"] for r in sel if r["trips_6w"] >= 2]
        out["by_cohort"][tier] = {
            "suppressed": False,
            "households": len(sel),
            "basis": basis,
            "spend_p10": percentile(spends, 0.10),
            "spend_p25": percentile(spends, 0.25),
            "spend_p50": percentile(spends, 0.50),
            "spend_p75": percentile(spends, 0.75),
            "spend_p90": percentile(spends, 0.90),
            "median_interpurchase_days": percentile(intervals, 0.5),
        }
    return out


def psg_score(ctx: SkillContext, brand_id: str, hero_bias: dict | None = None) -> dict:
    require_brand(brand_id)
    hero_bias = hero_bias or {}
    active = _active(ctx.store.rows_for_brand(brand_id))
    n = len(active)
    buyers = defaultdict(int)
    for r in active:
        for psg_id in r["psg_spend"]:
            buyers[psg_id] += 1
    psgs = [p for p in ctx.store.psgs.values() if p["brand_id"] == brand_id]
    items = []
    for p in psgs:
        series = ctx.store.feat_psg_week[p["psg_id"]]
        now, prior, ly = sum(series[-6:]), sum(series[-12:-6]), sum(series[-58:-52])
        items.append({
            "psg_id": p["psg_id"],
            "name": p["name"],
            "reach_pct": pct(buyers[p["psg_id"]], n),
            "sales_6w": round(now, 2),
            "momentum_6w_pct": change_pct(now, prior),
            "yoy_pct": change_pct(now, ly),
        })
    max_reach = max((i["reach_pct"] or 0) for i in items) or 1
    moms = [i["momentum_6w_pct"] or 0 for i in items]
    lo, hi = min(moms), max(moms)
    for i in items:
        mom_norm = ((i["momentum_6w_pct"] or 0) - lo) / (hi - lo) if hi > lo else 0.5
        i["hero_score"] = round(0.6 * (i["reach_pct"] or 0) / max_reach + 0.4 * mom_norm
                                + float(hero_bias.get(i["psg_id"], 0.0)), 3)
    items.sort(key=lambda i: -i["hero_score"])
    return {"brand_id": brand_id, "period": diag_period(ctx.store), "psgs": items,
            "default_basket": "whole brand"}


def cohort_size(ctx: SkillContext, brand_id: str, cohort: str, by: str | None = None) -> dict:
    spec = require_brand(brand_id)
    if cohort not in COHORTS:
        raise SkillError(f"unknown cohort {cohort!r}; one of {COHORTS}")
    rows = ctx.store.rows_for_brand(brand_id)
    if cohort == "non_buyers":
        cat = _category_shoppers(ctx, spec["category"])
        hh_ids = sorted(cat - {r["hh_id"] for r in _active(rows)})
    else:
        hh_ids = [r["hh_id"] for r in rows if r["tier"] == cohort]
    size = len(hh_ids)
    out = {"brand_id": brand_id, "cohort": cohort, "size": None if suppress(size, ctx) else size,
           "suppressed": suppress(size, ctx)}
    if by == "lifestage":
        counts = defaultdict(int)
        for h in hh_ids:
            counts[ctx.store.households[h]["lifestage"]] += 1
        out["by_lifestage"] = {k: (None if suppress(counts[k], ctx) else counts[k]) for k in LIFESTAGES}
    elif by is not None:
        raise SkillError("by must be 'lifestage' or omitted")
    return out


def brand_sales_6w(ctx: SkillContext, brand_id: str) -> float:
    """Internal helper used by other servers (not a registered skill)."""
    return sum(r["spend_6w"] for r in ctx.store.rows_for_brand(brand_id))


def headroom_pct(ctx: SkillContext, brand_id: str) -> float:
    spec = BRANDS[brand_id]
    cat = _category_shoppers(ctx, spec["category"])
    brand_hh = {r["hh_id"] for r in _active(ctx.store.rows_for_brand(brand_id))}
    return pct(len(cat - brand_hh), len(cat)) or 0.0
