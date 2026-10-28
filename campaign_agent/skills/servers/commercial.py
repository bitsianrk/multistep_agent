"""MCP domain server: commercial (margin, supplier funding, shelf price)."""

from __future__ import annotations

from ._common import SkillContext, require_brand


def _brand_psgs(ctx: SkillContext, brand_id: str) -> list[dict]:
    return [p for p in ctx.store.psgs.values() if p["brand_id"] == brand_id]


def margin_after_funding(ctx: SkillContext, brand_id: str) -> dict:
    require_brand(brand_id)
    floor = ctx.settings.margin_floor_pct
    items, w_sum, w_tot = [], 0.0, 0.0
    for p in _brand_psgs(ctx, brand_id):
        maf = round(p["margin_pct"] + p["supplier_funding_pct"], 1)
        sales = sum(ctx.store.feat_psg_week[p["psg_id"]][-6:])
        w_sum += maf * sales
        w_tot += sales
        items.append({
            "psg_id": p["psg_id"], "name": p["name"], "margin_pct": p["margin_pct"],
            "supplier_funding_pct": p["supplier_funding_pct"], "margin_after_funding_pct": maf,
            "shelf_price": p["shelf_price"], "below_floor": maf < floor,
        })
    return {
        "brand_id": brand_id,
        "margin_floor_pct": floor,
        "brand_margin_after_funding_pct": round(w_sum / w_tot, 1) if w_tot else None,
        "psgs": items,
    }


def supplier_funding_lookup(ctx: SkillContext, brand_id: str) -> dict:
    spec = require_brand(brand_id)
    psgs = _brand_psgs(ctx, brand_id)
    pool = sum(sum(ctx.store.feat_psg_week[p["psg_id"]][-6:]) * p["supplier_funding_pct"] / 100 for p in psgs)
    return {
        "brand_id": brand_id,
        "supplier": spec["supplier"],
        "funding_by_psg_pct": {p["psg_id"]: p["supplier_funding_pct"] for p in psgs},
        "est_funding_pool_6w_gbp": round(pool, 2),
        "funding_confirmed": False,  # confirmation happens in the external draft process
    }
