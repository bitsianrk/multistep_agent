"""Product taxonomy (semantic memory): category -> brand -> PSG.

All brands are fictional. In production this is the graph / vector index the
Entity Resolution Agent searches; Kantar's hierarchy is mapped to PSGs here,
once, not inside any agent.
"""

from __future__ import annotations

# Seasonal profile per category: (iso_week_from, iso_week_to, multiplier).
# Weeks not covered default to 1.0.
CATEGORY_SEASONALITY = {
    "CAT_CRISPS": [(1, 3, 0.85), (22, 34, 1.12), (46, 47, 1.15), (48, 52, 1.45)],
    "CAT_COFFEE": [(1, 9, 1.12), (14, 36, 0.88), (44, 52, 1.18)],
    "CAT_YOGHURT": [(1, 4, 1.18), (18, 35, 1.15), (49, 52, 0.82)],
}

CATEGORIES = {
    "CAT_CRISPS": {"name": "Crisps & Snacks", "aliases": ["crisps", "snacks", "crisp"]},
    "CAT_COFFEE": {"name": "Coffee", "aliases": ["coffee", "instant coffee", "ground coffee"]},
    "CAT_YOGHURT": {"name": "Yoghurt", "aliases": ["yoghurt", "yogurt", "yoghurts"]},
}

# Brand spec. size = relative household reach; momentum = current 6w trips vs
# prior 6w; lapse = probability a past buyer is lapsed in the window;
# xmas_amp scales the category's winter peak; share/share_trend drive Kantar;
# sentiment/sent_trend drive the sentiment feed; price = spend per trip (GBP).
BRANDS = {
    "BR001": dict(name="Crunchwell", category="CAT_CRISPS", aliases=["crunchwell", "crunch well"],
                  supplier="Crunchwell Foods Ltd", size=1.0, momentum=0.90, lapse=0.24, xmas_amp=0.8,
                  share=21.5, share_trend=-0.45, sentiment=18, sent_trend=-4.0, price=3.1),
    "BR002": dict(name="Golden Harvest", category="CAT_CRISPS", aliases=["golden harvest", "golden"],
                  supplier="Harvest Snacks plc", size=0.6, momentum=1.00, lapse=0.17, xmas_amp=1.0,
                  share=12.0, share_trend=0.05, sentiment=22, sent_trend=0.5, price=2.8),
    "BR003": dict(name="Snapjack", category="CAT_CRISPS", aliases=["snapjack", "snap jack"],
                  supplier="Snapjack Brands Ltd", size=0.7, momentum=1.12, lapse=0.12, xmas_amp=1.6,
                  share=14.5, share_trend=0.55, sentiment=34, sent_trend=5.5, price=3.4),
    "BR004": dict(name="Golden Bean", category="CAT_COFFEE", aliases=["golden bean", "golden"],
                  supplier="Golden Bean Roasters", size=0.9, momentum=1.06, lapse=0.13, xmas_amp=1.1,
                  share=19.0, share_trend=0.30, sentiment=30, sent_trend=2.0, price=5.6),
    "BR005": dict(name="Morning Ritual", category="CAT_COFFEE", aliases=["morning ritual"],
                  supplier="Ritual Beverages Ltd", size=0.7, momentum=0.99, lapse=0.16, xmas_amp=1.0,
                  share=13.0, share_trend=-0.05, sentiment=15, sent_trend=-0.5, price=4.9),
    "BR006": dict(name="Barista Bay", category="CAT_COFFEE", aliases=["barista bay", "barista"],
                  supplier="Bay Coffee Co", size=0.5, momentum=0.94, lapse=0.20, xmas_amp=0.9,
                  share=8.0, share_trend=-0.25, sentiment=10, sent_trend=-2.5, price=6.2),
    "BR007": dict(name="Meadow Fresh", category="CAT_YOGHURT", aliases=["meadow fresh", "meadow"],
                  supplier="Meadow Dairies Ltd", size=1.0, momentum=1.01, lapse=0.15, xmas_amp=1.0,
                  share=22.0, share_trend=0.10, sentiment=25, sent_trend=0.8, price=2.6),
    "BR008": dict(name="Alpine Swirl", category="CAT_YOGHURT", aliases=["alpine swirl", "alpine"],
                  supplier="Alpine Swirl GmbH", size=0.6, momentum=0.97, lapse=0.19, xmas_amp=1.0,
                  share=11.0, share_trend=-0.20, sentiment=12, sent_trend=-1.5, price=2.9),
    "BR009": dict(name="Greek Isle", category="CAT_YOGHURT", aliases=["greek isle", "greek"],
                  supplier="Isle Foods Ltd", size=0.5, momentum=1.09, lapse=0.11, xmas_amp=1.0,
                  share=9.5, share_trend=0.40, sentiment=31, sent_trend=3.0, price=3.3),
}

# PSGs per brand: (suffix, display name, popularity weight, base margin %,
# supplier funding %, stock cover weeks, momentum tilt).
PSG_TEMPLATES = {
    "CAT_CRISPS": [
        ("01", "Multipack Crisps", 1.6, 18.0, 4.0, 9.0, 0.98),
        ("02", "Sharing Bags", 1.3, 24.0, 3.0, 7.0, 1.08),
        ("03", "Single Bags", 0.7, 27.0, 1.0, 10.0, 0.95),
        ("04", "Baked Range", 0.5, 22.0, 2.0, 2.0, 1.05),
        ("05", "Tortilla Chips", 0.6, 15.0, 1.5, 8.0, 1.02),
    ],
    "CAT_COFFEE": [
        ("01", "Instant Jar 200g", 1.5, 26.0, 3.0, 12.0, 1.00),
        ("02", "Ground Coffee 227g", 1.0, 29.0, 2.0, 9.0, 1.04),
        ("03", "Coffee Pods", 1.1, 31.0, 2.5, 6.0, 1.10),
        ("04", "Whole Beans 1kg", 0.4, 33.0, 1.0, 2.5, 1.02),
        ("05", "Decaf Instant", 0.4, 17.0, 1.0, 11.0, 0.96),
    ],
    "CAT_YOGHURT": [
        ("01", "Fruit Multipack", 1.5, 21.0, 3.5, 4.0, 1.00),
        ("02", "Greek Style 500g", 1.0, 26.0, 2.0, 5.0, 1.06),
        ("03", "Kids Fromage Frais", 0.8, 19.0, 2.5, 4.0, 0.97),
        ("04", "High Protein Pots", 0.6, 28.0, 2.0, 1.5, 1.12),
        ("05", "Drinking Yoghurt", 0.5, 16.0, 1.0, 4.5, 1.01),
    ],
}

LOYALTY_TIERS = ["lapsed", "light", "medium", "heavy"]
LIFESTAGES = ["Young Adults", "Families", "Older Adults", "Retired"]
VALUE_SEGMENTS = ["Value", "Mainstream", "Premium"]
AUDIENCE_COHORTS = ["lapsed", "light", "medium", "heavy", "non_buyers"]
COHORT_LABELS = {
    "lapsed": "Lapsed",
    "light": "Light",
    "medium": "Medium",
    "heavy": "Heavy",
    "non_buyers": "Category non-buyers",
}


def psgs_for_brand(brand_id: str) -> list[dict]:
    brand = BRANDS[brand_id]
    out = []
    for suffix, name, pop, margin, funding, cover, tilt in PSG_TEMPLATES[brand["category"]]:
        out.append(dict(
            psg_id=f"PSG-{brand_id}-{suffix}",
            name=f"{brand['name']} {name}",
            brand_id=brand_id,
            popularity=pop,
            base_margin_pct=margin,
            supplier_funding_pct=funding,
            stock_cover_weeks=cover,
            momentum_tilt=tilt,
        ))
    return out


def brands_in_category(category_id: str) -> list[str]:
    return [b for b, spec in BRANDS.items() if spec["category"] == category_id]
