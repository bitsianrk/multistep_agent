"""Deterministic findings per specialist agent (the harness fallback policy).

After the pack is built, each specialist agent states what its blocks mean
for the three decisions. With the LRM on, the agent writes these from its
SKILL.md; these templates are what it says when the LRM is off or fails.
Every number is read straight from the pack.
"""

from __future__ import annotations

from ..data.taxonomy import COHORT_LABELS

SPECIALIST_SKILLS = {
    "brand_diagnostics": "brand-diagnostics",
    "qualifying_psg": "qualifying-psg",
    "market_intelligence": "market-intelligence",
    "commercial": "commercial",
    "performance_memory": "performance-memory",
    "brand_scout": "brand-scout",
}
AGENT_BLOCKS = {"brand_diagnostics": [1, 2, 3, 4], "qualifying_psg": [5], "market_intelligence": [7],
                "commercial": [8], "performance_memory": [6, 9], "brand_scout": [10]}


def _d(pack, block, tool):
    return pack["blocks"].get(str(block), {}).get("data", {}).get(tool, {})


def findings_for(agent: str, pack: dict) -> dict:
    f, watch = [], []
    if agent == "brand_diagnostics":
        b1, b2, b3 = _d(pack, 1, "brand_shoppers"), _d(pack, 2, "brand_spc"), _d(pack, 3, "cohort_split")
        if b1:
            f.append({"text": f"{b1['shoppers_6w']:,} brand shoppers ({b1['change_vs_prior_pct']:+}% vs prior 6 wks); "
                              f"lapsed pool {b1['lapsed_pool']:,} hh.", "blocks": [1]})
            if (b1["change_vs_prior_pct"] or 0) < 0:
                watch.append("shopper base shrinking")
        if b2:
            f.append({"text": f"SPC {b2['spc']} from {b2['frequency']} trips at {b2['spend_per_trip']} per trip.",
                      "blocks": [2]})
        heavy = next((c for c in b3.get("loyalty_tier", []) if c["cohort"] == "heavy" and not c["suppressed"]), None)
        if heavy:
            f.append({"text": f"Heavy shoppers are {heavy['shoppers_pct']}% of the base but {heavy['sales_pct']}% of sales.",
                      "blocks": [3]})
    elif agent == "qualifying_psg":
        p = pack["proposals"]["psgs"]
        names = {x["psg_id"]: x["name"] for x in _d(pack, 5, "psg_score").get("psgs", [])}
        f.append({"text": f"{len(p['include'])} PSGs qualify; heroes {', '.join(names.get(h, h) for h in p['hero'])}.",
                  "blocks": [5]})
        for e in p["exclude"]:
            f.append({"text": f"Exclude {names.get(e['psg_id'], e['psg_id'])}: {e['reason']}.", "blocks": [5, 8]})
    elif agent == "market_intelligence":
        k, s = _d(pack, 7, "kantar_share"), _d(pack, 7, "sentiment_scan")
        if k:
            f.append({"text": f"Kantar value share {k['value_share']}% ({k['value_share_trend_pts']:+} pts) in {k['period']}.",
                      "blocks": [7]})
            if k["value_share_trend_pts"] < 0:
                watch.append("losing share in Kantar")
        if s:
            f.append({"text": f"Net sentiment {s['net_sentiment_30d']} ({s['sentiment_trend']:+} vs prior 30 days).",
                      "blocks": [7]})
    elif agent == "commercial":
        m = _d(pack, 8, "margin_after_funding")
        if m:
            low = [x["name"] for x in m["psgs"] if x["below_floor"]]
            f.append({"text": f"Brand margin after funding {m['brand_margin_after_funding_pct']}% vs floor "
                              f"{m['margin_floor_pct']}%." + (f" Below floor: {', '.join(low)}." if low else ""),
                      "blocks": [8]})
            if low:
                watch.append("PSGs below margin floor")
    elif agent == "performance_memory":
        si, h = _d(pack, 6, "seasonality_index"), _d(pack, 9, "multistep_history").get("by_cohort", {})
        if si:
            f.append({"text": f"Seasonality index {si['seasonality_index']} for the window.", "blocks": [6]})
        if h:
            best = max(h, key=lambda c: h[c]["incremental_spend_per_hh"])
            f.append({"text": f"Best past Multi-Step response: {COHORT_LABELS[best]} "
                              f"({h[best]['incremental_spend_per_hh']} incremental per hh).", "blocks": [9]})
    elif agent == "brand_scout":
        sc = _d(pack, 10, "brand_scout_score")
        beats = [a for a in sc.get("alternatives", []) if a["beats_requested"]]
        if beats:
            a = beats[0]
            f.append({"text": f"{a['name']} beats {sc['requested']['name']} (score {a['score']} vs "
                              f"{sc['requested']['score']}).", "blocks": [10]})
            watch.append(f"brand switch candidate: {a['name']}")
        elif sc:
            f.append({"text": f"No stronger alternative to {sc['requested']['name']} in the category.", "blocks": [10]})
    return {"findings": f[:4], "watch": watch}
