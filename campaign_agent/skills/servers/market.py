"""MCP domain server: market (Kantar / Worldpanel, sentiment, Brand Scout).

Kantar rules from the design:
1. cache per release period - no call per request when the period is unchanged;
2. Kantar's hierarchy is mapped to PSGs in the taxonomy layer, not here;
3. every Kantar-derived number carries its period and licence scope.
"""

from __future__ import annotations

from statistics import mean, pstdev

from ...data.taxonomy import BRANDS, brands_in_category
from ._common import SkillContext, require_brand
from .commercial import margin_after_funding
from .ops import seasonality_index
from .transactions import headroom_pct

_KANTAR_CACHE: dict[tuple, dict] = {}


def _kantar(ctx: SkillContext, brand_id: str, measure: str) -> tuple[dict, bool]:
    period = ctx.store.kantar_release["period"]
    key = (id(ctx.store), brand_id, measure, period)
    if key in _KANTAR_CACHE:
        return _KANTAR_CACHE[key], True
    periods = ctx.store.kantar[brand_id]
    latest = periods[0]
    last3 = mean(p[measure] for p in periods[:3])
    prev3 = mean(p[measure] for p in periods[3:6])
    out = {
        "brand_id": brand_id,
        "period": latest["period"],
        "period_end": latest["period_end"],
        "licence_scope": latest["licence_scope"],
        measure: latest[measure],
        f"{measure}_trend_pts": round(last3 - prev3, 2),
        "history": [{"period": p["period"], measure: p[measure]} for p in periods[:6]],
        # Raw panel detail is outside the licence scope for chat display; the
        # guardrail layer strips it before anything reaches a human.
        "panel_sample_households": latest["panel_sample_households"],
    }
    _KANTAR_CACHE[key] = out
    return out, False


def kantar_share(ctx: SkillContext, brand_id: str) -> dict:
    spec = require_brand(brand_id)
    out, hit = _kantar(ctx, brand_id, "value_share")
    others = [b for b in brands_in_category(spec["category"])]
    cat_trend = mean(_kantar(ctx, b, "value_share")[0]["value_share_trend_pts"] for b in others)
    return {**out, "cache_hit": hit,
            "share_trend_vs_category_pts": round(out["value_share_trend_pts"] - cat_trend, 2)}


def kantar_penetration(ctx: SkillContext, brand_id: str) -> dict:
    require_brand(brand_id)
    out, hit = _kantar(ctx, brand_id, "penetration")
    return {**out, "cache_hit": hit}


def sentiment_scan(ctx: SkillContext, brand_id: str) -> dict:
    require_brand(brand_id)
    days = ctx.store.sentiment_daily[brand_id]
    last, prior = days[-30:], days[-60:-30]
    now, before = mean(d["net_sentiment"] for d in last), mean(d["net_sentiment"] for d in prior)
    return {
        "brand_id": brand_id,
        "window_days": 30,
        "net_sentiment_30d": round(now, 1),
        "net_sentiment_prior_30d": round(before, 1),
        "sentiment_trend": round(now - before, 1),
        "mentions_30d": sum(d["mentions"] for d in last),
        "sources": ["reviews", "social", "search_trends"],
    }


def category_brands(ctx: SkillContext, brand_id: str) -> dict:
    spec = require_brand(brand_id)
    return {
        "brand_id": brand_id,
        "category_id": spec["category"],
        "brands": [{"brand_id": b, "name": BRANDS[b]["name"]}
                   for b in brands_in_category(spec["category"]) if b != brand_id],
    }


SCOUT_WEIGHTS = {"seasonal_fit": 0.25, "market_momentum": 0.25, "sentiment": 0.2,
                 "headroom": 0.15, "margin": 0.15}


def brand_scout_score(ctx: SkillContext, brand_id: str, window_start: str, window_weeks: int,
                      switch_bias: float = 0.0) -> dict:
    spec = require_brand(brand_id)
    brand_ids = brands_in_category(spec["category"])
    drivers = {}
    for b in brand_ids:
        drivers[b] = {
            "seasonal_fit": seasonality_index(ctx, b, window_start, window_weeks)["seasonality_index"],
            "market_momentum": _kantar(ctx, b, "value_share")[0]["value_share_trend_pts"],
            "sentiment": sentiment_scan(ctx, b)["sentiment_trend"],
            "headroom": headroom_pct(ctx, b),
            "margin": margin_after_funding(ctx, b)["brand_margin_after_funding_pct"],
        }
    # z-score each driver within the category, then weight.
    scores = {b: 0.0 for b in brand_ids}
    for d, w in SCOUT_WEIGHTS.items():
        vals = [drivers[b][d] for b in brand_ids]
        mu, sd = mean(vals), pstdev(vals) or 1.0
        for b in brand_ids:
            scores[b] += w * (drivers[b][d] - mu) / sd
    req = drivers[brand_id]
    need = ctx.settings.scout_beat_margin + float(switch_bias)
    alts = []
    for b in brand_ids:
        if b == brand_id:
            continue
        d = drivers[b]
        beats = (scores[b] > scores[brand_id] + need and
                 (d["seasonal_fit"] >= req["seasonal_fit"] or d["market_momentum"] > req["market_momentum"]))
        alts.append({"brand_id": b, "name": BRANDS[b]["name"], "score": round(scores[b], 3),
                     "drivers": d, "beats_requested": beats})
    alts.sort(key=lambda a: -a["score"])
    return {
        "brand_id": brand_id,
        "window": {"start": window_start, "weeks": window_weeks},
        "requested": {"brand_id": brand_id, "name": spec["name"],
                      "score": round(scores[brand_id], 3), "drivers": req},
        "alternatives": alts[: ctx.settings.scout_max_alternatives],
        "weights": SCOUT_WEIGHTS,
        "kantar_period": ctx.store.kantar_release["period"],
        "licence_scope": ctx.store.kantar_release["licence_scope"],
    }
