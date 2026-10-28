"""Seeded synthetic data for every table and feed in the data-layer design.

Nothing here is real. The generator builds the four feature tables
(feat_customer_brand_6w, feat_brand_week, feat_psg_week, feat_multistep_outcome)
plus stand-ins for the live APIs (stock, promotions, price), the commercial
mart, the Kantar / Worldpanel cache and the sentiment feed. Household-level
rows are generated first and the weekly tables are calibrated to them, so the
Critic's reconciliation checks (shoppers x SPC ~= sales) hold on real arithmetic.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from .taxonomy import (
    BRANDS,
    CATEGORIES,
    CATEGORY_SEASONALITY,
    LIFESTAGES,
    VALUE_SEGMENTS,
    brands_in_category,
    psgs_for_brand,
)

HISTORY_WEEKS = 156
WINDOW_WEEKS = 6

# Per-brand PSG adjustments so brands in one category are not clones.
MARGIN_ADJ = {"BR001": 2.0, "BR002": 0.0, "BR003": -3.0, "BR004": 1.0, "BR005": 0.0,
              "BR006": 2.5, "BR007": 0.5, "BR008": -1.0, "BR009": -2.0}
LIVE_PROMOS = [
    # psg_id, mechanic, start, end
    ("PSG-BR001-02", "Clubcard Price", date(2026, 11, 2), date(2026, 11, 29)),
    ("PSG-BR003-01", "Clubcard Price", date(2026, 10, 12), date(2026, 11, 8)),
    ("PSG-BR004-03", "Coupon at till", date(2026, 11, 9), date(2026, 12, 6)),
    ("PSG-BR007-01", "Clubcard Price", date(2026, 10, 26), date(2026, 11, 22)),
]
# Past Multi-Step response by cohort: (participation %, completion %, incremental GBP / hh)
COHORT_RESPONSE = {
    "lapsed": (14.0, 31.0, 3.9),
    "light": (24.0, 38.0, 2.7),
    "medium": (33.0, 46.0, 1.9),
    "heavy": (47.0, 62.0, 0.6),
    "non_buyers": (6.0, 22.0, 2.2),
}


@dataclass
class DataStore:
    as_of: date
    weeks: list[date]                       # Monday of each week, oldest first
    households: dict[int, dict]
    customer_brand: list[dict]              # feat_customer_brand_6w
    feat_brand_week: dict[str, list[float]]
    feat_psg_week: dict[str, list[float]]
    brand_summary: dict[str, dict]
    psgs: dict[str, dict]
    kantar: dict[str, list[dict]]
    kantar_release: dict
    sentiment_daily: dict[str, list[dict]]
    live_promos: list[dict]
    campaign_mart: list[dict]               # feat_multistep_outcome, aggregated
    source_as_of: dict[str, str] = field(default_factory=dict)

    # Convenience indexes -------------------------------------------------
    def rows_for_brand(self, brand_id: str) -> list[dict]:
        return self._by_brand.get(brand_id, [])

    def rows_for_category(self, category_id: str) -> list[dict]:
        return self._by_category.get(category_id, [])

    def index(self) -> "DataStore":
        self._by_brand: dict[str, list[dict]] = {}
        self._by_category: dict[str, list[dict]] = {}
        for row in self.customer_brand:
            self._by_brand.setdefault(row["brand_id"], []).append(row)
            self._by_category.setdefault(row["category_id"], []).append(row)
        return self


def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _seasonal(category_id: str, iso_week: int, xmas_amp: float) -> float:
    for lo, hi, mult in CATEGORY_SEASONALITY[category_id]:
        if lo <= iso_week <= hi:
            if iso_week >= 44 and mult > 1:
                return 1 + (mult - 1) * xmas_amp
            return mult
    return 1.0


def generate(seed: int = 7, as_of: date = date(2026, 10, 27), n_households: int = 6000) -> DataStore:
    rng = random.Random(seed)

    # Weeks: Monday-start, the last one being the latest complete week (T-1).
    last_sunday = as_of - timedelta(days=(as_of.weekday() + 1) % 7)
    last_monday = last_sunday - timedelta(days=6)
    weeks = [last_monday - timedelta(weeks=HISTORY_WEEKS - 1 - i) for i in range(HISTORY_WEEKS)]

    psgs: dict[str, dict] = {}
    for brand_id in BRANDS:
        for p in psgs_for_brand(brand_id):
            p = dict(p)
            p["margin_pct"] = round(p.pop("base_margin_pct") + MARGIN_ADJ[brand_id] + rng.uniform(-1, 1), 1)
            p["supplier_funding_pct"] = round(p["supplier_funding_pct"] + rng.uniform(-0.5, 0.5), 1)
            p["stock_cover_weeks"] = round(max(0.5, p["stock_cover_weeks"] + rng.uniform(-0.8, 0.8)), 1)
            p["shelf_price"] = round(BRANDS[brand_id]["price"] * rng.uniform(0.6, 1.5), 2)
            psgs[p["psg_id"]] = p

    # Households ----------------------------------------------------------
    households = {}
    for hh in range(1, n_households + 1):
        households[hh] = dict(
            hh_id=hh,
            lifestage=rng.choices(LIFESTAGES, weights=[0.22, 0.34, 0.27, 0.17])[0],
            value_segment=rng.choices(VALUE_SEGMENTS, weights=[0.3, 0.5, 0.2])[0],
            clubcard_plus=rng.random() < 0.18,
            basket_value=round(rng.lognormvariate(math.log(38), 0.45), 2),
        )

    # feat_customer_brand_6w ---------------------------------------------
    customer_brand = []
    for hh in households.values():
        for cat_id in CATEGORIES:
            if rng.random() > 0.58:
                continue
            brand_ids = brands_in_category(cat_id)
            weights = [BRANDS[b]["size"] for b in brand_ids]
            n_brands = rng.choices([1, 2, 3], weights=[0.5, 0.35, 0.15])[0]
            chosen: set[str] = set()
            while len(chosen) < n_brands:
                chosen.add(rng.choices(brand_ids, weights=weights)[0])
            for brand_id in sorted(chosen):
                spec = BRANDS[brand_id]
                engagement = rng.lognormvariate(math.log(1.6), 0.7)
                trips_prior = _poisson(rng, engagement)
                if rng.random() < spec["lapse"]:
                    trips_now = 0
                    trips_prior = max(1, trips_prior)
                    weeks_since = rng.randint(7, 26)
                else:
                    trips_now = max(1, _poisson(rng, engagement * spec["momentum"]))
                    weeks_since = rng.randint(0, 5)
                spt = spec["price"] * rng.lognormvariate(0, 0.3)
                spend_now = round(trips_now * spt, 2)
                spend_prior = round(trips_prior * spt * rng.uniform(0.9, 1.1), 2)
                brand_psgs = [p for p in psgs.values() if p["brand_id"] == brand_id]
                psg_spend: dict[str, float] = {}
                if trips_now:
                    k = min(len(brand_psgs), 1 + trips_now // 2)
                    pool = list(brand_psgs)
                    picked = []
                    for _ in range(k):
                        w = [p["popularity"] * p["momentum_tilt"] for p in pool]
                        idx = rng.choices(range(len(pool)), weights=w)[0]
                        picked.append(pool.pop(idx))
                    split = [rng.random() + 0.2 for _ in picked]
                    total = sum(split)
                    for p, s in zip(picked, split):
                        psg_spend[p["psg_id"]] = round(spend_now * s / total, 2)
                customer_brand.append(dict(
                    hh_id=hh["hh_id"], brand_id=brand_id, category_id=cat_id,
                    trips_6w=trips_now, spend_6w=spend_now,
                    trips_prior_6w=trips_prior, spend_prior_6w=spend_prior,
                    weeks_since_last=weeks_since, psg_spend=psg_spend,
                ))

    # Loyalty tiers (segmentation snapshot): lapsed / light 50% / medium 30% / heavy 20%
    by_brand: dict[str, list[dict]] = {}
    for row in customer_brand:
        by_brand.setdefault(row["brand_id"], []).append(row)
    for rows in by_brand.values():
        active = sorted((r for r in rows if r["trips_6w"] > 0), key=lambda r: (r["spend_6w"], r["hh_id"]))
        n = len(active)
        for i, r in enumerate(active):
            r["tier"] = "light" if i < 0.5 * n else "medium" if i < 0.8 * n else "heavy"
        for r in rows:
            r.setdefault("tier", "lapsed")

    # feat_brand_week / feat_psg_week, calibrated to the household rows ----
    feat_brand_week: dict[str, list[float]] = {}
    feat_psg_week: dict[str, list[float]] = {}
    brand_summary: dict[str, dict] = {}
    n = HISTORY_WEEKS
    for brand_id, spec in BRANDS.items():
        raw = []
        for t, wk in enumerate(weeks):
            iso_week = min(wk.isocalendar()[1], 52)
            season = _seasonal(spec["category"], iso_week, spec["xmas_amp"])
            trend = 1.0 if t < n - 26 else spec["momentum"] ** ((t - (n - 26)) / 6)
            raw.append(season * trend * rng.uniform(0.96, 1.04))
        rows = by_brand.get(brand_id, [])
        actual_6w = sum(r["spend_6w"] for r in rows)
        scale = actual_6w / sum(raw[-WINDOW_WEEKS:]) * rng.uniform(0.997, 1.003)
        series = [round(x * scale, 2) for x in raw]
        feat_brand_week[brand_id] = series

        brand_psgs = [p for p in psgs.values() if p["brand_id"] == brand_id]
        for p in brand_psgs:
            pser = []
            for t in range(n):
                tilt = 1.0 if t < n - 26 else p["momentum_tilt"] ** ((t - (n - 26)) / 6)
                pser.append(p["popularity"] * tilt)
            feat_psg_week[p["psg_id"]] = pser
        for t in range(n):
            tot = sum(feat_psg_week[p["psg_id"]][t] for p in brand_psgs)
            for p in brand_psgs:
                feat_psg_week[p["psg_id"]][t] = feat_psg_week[p["psg_id"]][t] / tot * series[t]
        for p in brand_psgs:
            feat_psg_week[p["psg_id"]] = [round(x, 2) for x in feat_psg_week[p["psg_id"]]]

        shoppers = sum(1 for r in rows if r["trips_6w"] > 0)
        yoy = sum(series[-WINDOW_WEEKS:]) / sum(series[-52 - WINDOW_WEEKS:-52])
        brand_summary[brand_id] = dict(shoppers_6w_ly=round(shoppers / max(yoy, 0.5) ** 0.6))

    # Kantar / Worldpanel cache: 13 four-weekly periods ---------------------
    latest_end = date(2026, 10, 4)
    release = dict(period="2026 P10", period_end=latest_end.isoformat(),
                   released=date(2026, 10, 21).isoformat(), licence_scope="tesco-internal")
    kantar: dict[str, list[dict]] = {}
    for brand_id, spec in BRANDS.items():
        periods = []
        for k in range(13):
            end = latest_end - timedelta(weeks=4 * k)
            pnum = 10 - k
            year = 2026
            while pnum <= 0:
                pnum += 13
                year -= 1
            periods.append(dict(
                period=f"{year} P{pnum:02d}", period_end=end.isoformat(),
                value_share=round(spec["share"] - spec["share_trend"] * k / 3 + rng.uniform(-0.08, 0.08), 2),
                penetration=round(18 + 22 * spec["size"] - spec["share_trend"] * k / 2 + rng.uniform(-0.2, 0.2), 1),
                panel_sample_households=rng.randint(900, 1500),
                licence_scope="tesco-internal",
            ))
        kantar[brand_id] = periods  # latest first

    # Sentiment: 90 days of net sentiment ----------------------------------
    sentiment_daily: dict[str, list[dict]] = {}
    for brand_id, spec in BRANDS.items():
        days = []
        for d in range(90):
            day = as_of - timedelta(days=89 - d)
            net = spec["sentiment"] + spec["sent_trend"] * (d - 60) / 30 + rng.uniform(-6, 6)
            days.append(dict(date=day.isoformat(), net_sentiment=round(net, 1),
                             mentions=rng.randint(40, 400)))
        sentiment_daily[brand_id] = days

    live_promos = [dict(psg_id=p, mechanic=m, start=s.isoformat(), end=e.isoformat())
                   for p, m, s, e in LIVE_PROMOS]

    # Campaign mart: past Multi-Step campaigns with response by cohort -----
    campaign_mart = []
    cid = 100
    for brand_id in BRANDS:
        for months_ago in (5, 14):
            cid += 1
            start = as_of - timedelta(days=30 * months_ago)
            by_cohort = {}
            for cohort, (part, comp, inc) in COHORT_RESPONSE.items():
                if cohort == "non_buyers" and months_ago == 14:
                    continue  # older campaigns did not target non-buyers
                by_cohort[cohort] = dict(
                    targeted=rng.randint(800, 4000),
                    participation_pct=round(part * rng.uniform(0.8, 1.2), 1),
                    completion_pct=round(comp * rng.uniform(0.85, 1.15), 1),
                    incremental_spend_per_hh=round(inc * rng.uniform(0.7, 1.3), 2),
                )
            campaign_mart.append(dict(campaign_id=f"MS-{cid}", brand_id=brand_id,
                                      category_id=BRANDS[brand_id]["category"],
                                      start=start.isoformat(), weeks=4, by_cohort=by_cohort))

    t1 = as_of.isoformat()
    source_as_of = {
        "feat_customer_brand_6w": t1, "feat_brand_week": t1, "feat_psg_week": t1,
        "segmentation_weekly": (last_sunday - timedelta(days=last_sunday.weekday() + 1)).isoformat(),
        "stock_api": f"{as_of.isoformat()}T09:00", "promotions_api": f"{as_of.isoformat()}T09:00",
        "price_api": t1, "commercial_mart": t1, "kantar_cache": release["period"],
        "sentiment_daily": t1, "campaign_mart": t1, "taxonomy": t1,
        "seasonality_monthly": date(as_of.year, as_of.month, 1).isoformat(),
    }

    return DataStore(
        as_of=as_of, weeks=weeks, households=households, customer_brand=customer_brand,
        feat_brand_week=feat_brand_week, feat_psg_week=feat_psg_week, brand_summary=brand_summary,
        psgs=psgs, kantar=kantar, kantar_release=release, sentiment_daily=sentiment_daily,
        live_promos=live_promos, campaign_mart=campaign_mart, source_as_of=source_as_of,
    ).index()
