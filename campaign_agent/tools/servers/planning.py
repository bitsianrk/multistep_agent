"""MCP domain server: planning (tool plans, audience proposal, critic, brief).

These tools hold the judgement-support logic the orchestration agents call:
the planner's tool plan, the Audience Agent's proposal, the Critic / Judge's
reconciliation and scope checks, and the Brief Writer's contract.
"""

from __future__ import annotations

import re
from datetime import date

from ...data.taxonomy import BRANDS, COHORT_LABELS
from ._common import ToolContext, ToolError, require_brand, window_weeks_list
from .market import kantar_share

STAGES = ["understand", "plan", "diagnose", "discuss", "brief"]
CORE_TOOLS = {"brand_shoppers", "brand_spc", "cohort_split"}  # blocks 1-3, the core asks

BASE_PLAN = [
    # agent, tool, block, reason, needs_window
    ("brand_diagnostics", "brand_shoppers", 1, "Core ask: brand shoppers, penetration, lapsed pool", False),
    ("brand_diagnostics", "brand_spc", 2, "Core ask: spend per customer, frequency, spend per trip", False),
    ("brand_diagnostics", "cohort_split", 3, "Core ask: shoppers and sales by loyalty tier and segment", False),
    ("brand_diagnostics", "spend_distribution", 4, "Spend percentiles and purchase interval by cohort", False),
    ("qualifying_psg", "psg_score", 5, "Reach and momentum per PSG for hero selection", False),
    ("qualifying_psg", "stock_check", 5, "Hard filter: forecast stock cover for the window", True),
    ("qualifying_psg", "promo_clash_check", 5, "Hard filter: live promotions overlapping the window", True),
    ("performance_memory", "seasonality_index", 6, "Seasonal strength of the brand in the window", True),
    ("market_intelligence", "kantar_share", 7, "Kantar value share and trend vs category", False),
    ("market_intelligence", "kantar_penetration", 7, "Kantar penetration and trend", False),
    ("market_intelligence", "sentiment_scan", 7, "Net sentiment trend from reviews, social, search", False),
    ("commercial", "margin_after_funding", 8, "Margin after supplier funding per PSG (margin floor)", False),
    ("commercial", "supplier_funding_lookup", 8, "Supplier funding available for the brand", False),
    ("performance_memory", "multistep_history", 9, "Past Multi-Step response by cohort", False),
    ("brand_scout", "category_brands", 10, "Other brands in the category", False),
    ("brand_scout", "brand_scout_score", 10, "Rank alternatives on seasonal and market signals", True),
    ("audience", "cohort_size", None, "Size each candidate cohort", False),
    ("audience", "audience_propose", None, "Propose cohorts with sizes and rationale", False),
]

TOOL_AGENT = {tool: agent for agent, tool, *_ in BASE_PLAN}
TOOL_AGENT.update({"brand_shoppers": "brand_diagnostics"})


def plan_campaign(ctx: ToolContext, brand_id: str, objective: str, window_start: str,
                  window_weeks: int) -> dict:
    require_brand(brand_id)
    if objective not in ("reactivate", "grow", "retain", "trial"):
        raise ToolError(f"unknown objective {objective!r}")
    window = window_weeks_list(window_start, window_weeks)
    win = {"window_start": window_start, "window_weeks": window_weeks}
    steps = []
    for agent, tool, block, reason, needs_window in BASE_PLAN:
        steps.append({"agent": agent, "tool": tool, "block": block, "reason": reason,
                      "params": dict(win) if needs_window else {}, "added_by": "base"})

    # Agentic part: the plan adapts to what this brand and request need.
    signals = {}
    share = kantar_share(ctx, brand_id)
    signals["kantar_share_trend_pts"] = share["value_share_trend_pts"]
    if share["value_share_trend_pts"] <= -0.2:
        steps.insert(1, {"agent": "brand_diagnostics", "tool": "brand_shoppers", "block": 1,
                         "params": {"cut": "lapsed_recency"}, "added_by": "planner",
                         "reason": f"Kantar share falling ({share['value_share_trend_pts']:+.2f} pts) "
                                   "-> add lapsed-shopper analysis"})
    if objective == "reactivate":
        steps.append({"agent": "brand_diagnostics", "tool": "spend_distribution", "block": 4,
                      "params": {"cohort": "lapsed"}, "added_by": "planner",
                      "reason": "Objective is reactivate -> pre-lapse spend of the lapsed pool"})
    elif objective == "trial":
        steps.append({"agent": "audience", "tool": "cohort_size", "block": None,
                      "params": {"cohort": "non_buyers", "by": "lifestage"}, "added_by": "planner",
                      "reason": "Objective is trial -> category non-buyers by lifestage"})
    elif objective == "retain":
        steps.append({"agent": "brand_diagnostics", "tool": "spend_distribution", "block": 4,
                      "params": {"cohort": "heavy"}, "added_by": "planner",
                      "reason": "Objective is retain -> heavy-shopper spend distribution"})
    peak = any(w.isocalendar()[1] >= 46 for w in window)
    signals["window_in_peak_weeks"] = peak
    if peak:
        steps.append({"agent": "performance_memory", "tool": "seasonality_index", "block": 6,
                      "params": {**win, "level": "psg"}, "added_by": "planner",
                      "reason": "Window covers peak weeks -> PSG-level seasonality for hero picks"})
    for i, s in enumerate(steps, start=1):
        s["id"] = f"s{i}"
    return {"brand_id": brand_id, "objective": objective, "window": {"start": window_start, "weeks": window_weeks},
            "signals": signals, "steps": steps}


def resume_from_stage(ctx: ToolContext, brand_id: str, stage: str) -> dict:
    if stage not in STAGES:
        raise ToolError(f"unknown stage {stage!r}; one of {STAGES}")
    clears = {
        "understand": ["plan", "pack", "reconciliation", "debate", "brief"],
        "plan": ["plan", "pack", "reconciliation", "debate", "brief"],
        "diagnose": ["pack", "reconciliation", "debate", "brief"],
        "discuss": ["debate", "brief"],
        "brief": ["brief"],
    }[stage]
    return {"brand_id": brand_id, "stage": stage, "clears": clears}


OBJECTIVE_PRIOR = {
    "reactivate": {"lapsed": 1.0, "light": 0.6, "medium": 0.3, "heavy": -0.6, "non_buyers": 0.1},
    "grow": {"lapsed": 0.2, "light": 0.9, "medium": 0.8, "heavy": -0.5, "non_buyers": 0.1},
    "retain": {"lapsed": -0.2, "light": 0.1, "medium": 0.7, "heavy": 0.9, "non_buyers": -0.5},
    "trial": {"lapsed": 0.5, "light": 0.1, "medium": -0.3, "heavy": -0.8, "non_buyers": 1.0},
}
TYPICAL = {"lapsed": "reactivate", "light": "grow frequency", "medium": "grow spend",
           "heavy": "retain; often excluded to limit subsidy", "non_buyers": "trial"}


def audience_propose(ctx: ToolContext, brand_id: str, objective: str, cohort_sizes: dict,
                     response: dict, cohort_bias: dict | None = None) -> dict:
    require_brand(brand_id)
    prior = OBJECTIVE_PRIOR.get(objective)
    if prior is None:
        raise ToolError(f"unknown objective {objective!r}")
    cohort_bias = cohort_bias or {}
    incs = [r["incremental_spend_per_hh"] for r in response.values()] or [0]
    lo, hi = min(incs), max(incs)
    out = []
    for cohort, base in prior.items():
        size = cohort_sizes.get(cohort)
        resp = response.get(cohort)
        resp_term = 0.0
        if resp and hi > lo:
            resp_term = 0.4 * (resp["incremental_spend_per_hh"] - lo) / (hi - lo) - 0.2
        bias = float(cohort_bias.get(cohort, 0.0))
        score = round(base + resp_term + bias, 3)
        include = score >= 0.4 and size is not None
        why = [f"typical objective: {TYPICAL[cohort]}"]
        if resp:
            why.append(f"past Multi-Step: {resp['participation_pct']}% participation, "
                       f"GBP {resp['incremental_spend_per_hh']:.2f} incremental/hh")
        if bias:
            why.append(f"learned bias {bias:+.2f} from past human overrides")
        if size is None:
            why.append("size below minimum cell size")
        out.append({"cohort": cohort, "label": COHORT_LABELS[cohort], "size": size, "include": include,
                    "score": score, "reason": "; ".join(why)})
    out.sort(key=lambda c: -c["score"])
    return {"brand_id": brand_id, "objective": objective, "cohorts": out,
            "total_included": sum(c["size"] or 0 for c in out if c["include"])}


def _close(a: float, b: float, tol_pct: float) -> bool:
    return abs(a - b) <= abs(b) * tol_pct / 100.0


def reconcile_metrics(ctx: ToolContext, brand_id: str, pack: dict) -> dict:
    blocks = pack.get("blocks", {})
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "passed": bool(ok), "detail": detail})

    b1 = blocks.get("1", {}).get("data", {}).get("brand_shoppers", {})
    b2 = blocks.get("2", {}).get("data", {}).get("brand_spc", {})
    b3 = blocks.get("3", {}).get("data", {}).get("cohort_split", {})
    tol = ctx.settings.reconcile_share_tol_pct
    for dim in ("loyalty_tier", "lifestage", "value_segment", "clubcard_plus"):
        cells = b3.get(dim, [])
        if not cells:
            continue
        if any(c["suppressed"] for c in cells):
            add(f"cohort shares sum to 100% ({dim})", True, "skipped: suppressed cells")
            continue
        s1 = sum(c["shoppers_pct"] for c in cells)
        s2 = sum(c["sales_pct"] for c in cells)
        add(f"cohort shares sum to 100% ({dim})", abs(s1 - 100) <= tol and abs(s2 - 100) <= tol,
            f"shoppers {s1:.2f}%, sales {s2:.2f}%")
    if b1 and b2 and not b2.get("suppressed"):
        implied = b1["shoppers_6w"] * b2["spc"]
        actual = b1["sales_6w_brand_week"]
        add("shoppers x SPC ~= sales", _close(implied, actual, ctx.settings.reconcile_sales_tol_pct),
            f"{b1['shoppers_6w']} x GBP {b2['spc']:.2f} = GBP {implied:,.0f} vs feat_brand_week GBP {actual:,.0f}")
        add("frequency x spend per trip ~= SPC",
            _close(b2["frequency"] * b2["spend_per_trip"], b2["spc"], 1.0),
            f"{b2['frequency']} x {b2['spend_per_trip']} vs {b2['spc']}")
        add("block 1 and block 2 shopper counts agree", b1["shoppers_6w"] == b2["shoppers"],
            f"{b1['shoppers_6w']} vs {b2['shoppers']}")
    if b1 and b3:
        lap = next((c for c in b3.get("loyalty_tier", []) if c["cohort"] == "lapsed"), None)
        if lap and not lap["suppressed"]:
            add("lapsed pool agrees with cohort split", lap["households"] == b1["lapsed_pool"],
                f"{b1['lapsed_pool']} vs {lap['households']}")
    # Kantar must be the release that lines up with the 6-week diagnostics window
    # (no older than kantar_max_staleness_weeks, and never after the window).
    b7 = blocks.get("7", {}).get("data", {}).get("kantar_share")
    if b7 and b1:
        diag_end = date.fromisoformat(b1["period"]["end"])
        end = date.fromisoformat(b7["period_end"])
        gap_weeks = (diag_end - end).days / 7
        add("Kantar period matches window", 0 <= gap_weeks <= ctx.settings.kantar_max_staleness_weeks,
            f"{b7['period']} ends {b7['period_end']}, {gap_weeks:.1f} wks before the diagnostics window ends")
    return {"brand_id": brand_id, "passed": all(c["passed"] for c in checks), "checks": checks}


OFF_SCOPE = re.compile(
    r"\b(thresholds?|rewards?|points|coupons?|mechanics?|discount depth|step \d|step[- ]?(one|two|three)|"
    r"budget|redemptions?|creative|copy|messaging|launch date|media)\b", re.I)
TOPIC_WORDS = {
    "brand": re.compile(r"\b(brand|switch|keep)\b", re.I),
    "audience": re.compile(r"\b(cohort|audience|lapsed|light|medium|heavy|non-?buyers|shoppers)\b", re.I),
    "psgs": re.compile(r"\b(psgs?|hero|exclude|include|stock|promo|margin)\b", re.I),
}


def scope_check(ctx: ToolContext, brand_id: str, claims: list[dict]) -> dict:
    results = []
    for i, claim in enumerate(claims):
        text = claim.get("text", "")
        topic = claim.get("topic")
        off = OFF_SCOPE.search(text)
        if off or topic not in (None, "brand", "audience", "psgs"):
            results.append({"index": i, "in_scope": False, "topic": topic,
                            "reason": f"outside brand/audience/PSGs ('{off.group(0) if off else topic}')"})
            continue
        if topic is None:
            topic = next((k for k, rx in TOPIC_WORDS.items() if rx.search(text)), None)
        results.append({"index": i, "in_scope": topic is not None, "topic": topic,
                        "reason": "ok" if topic else "no brand/audience/PSG content"})
    return {"brand_id": brand_id, "results": results,
            "in_scope_pct": round(100 * sum(r["in_scope"] for r in results) / len(results), 1) if results else 100.0}


def write_brief(ctx: ToolContext, brand_id: str, decision: dict, pack: dict, approved_by: str | None = None) -> dict:
    require_brand(brand_id)
    provenance = [{"block": k, "source": ", ".join(p["source"] for p in v.get("provenance", [])),
                   "as_of": ", ".join(sorted({p["as_of"] for p in v.get("provenance", [])}))}
                  for k, v in sorted(pack.get("blocks", {}).items(), key=lambda kv: int(kv[0]))]
    psgs = decision["qualifying_psgs"]
    brief = {
        "brand_id": brand_id,
        "brand_decision": decision["brand_decision"],
        "objective": decision["objective"],
        "window": {"start": decision["window"]["start"], "weeks": decision["window"]["weeks"]},
        "audience": [{"cohort": a["cohort"], "size": a["size"], "include": a["include"], "reason": a["reason"]}
                     for a in decision["audience"]],
        "qualifying_psgs": {"include": psgs["include"], "exclude": psgs["exclude"], "hero": psgs["hero"]},
        "diagnostics_ref": pack.get("pack_id"),
        "dissent": decision.get("dissent", []),
        "notes_for_draft": decision.get("notes_for_draft", []),
        "approved_by": approved_by,
        "provenance": provenance,
    }
    name = BRANDS[brand_id]["name"]
    bd = brief["brand_decision"]
    inc = [a for a in brief["audience"] if a["include"]]
    lines = [
        f"**Agreed Brief - {name}** ({brief['objective']}, {brief['window']['weeks']} wks from {brief['window']['start']})",
        f"- Brand: {name}" + (f" (switched from {BRANDS[bd['requested']]['name']})"
                              if bd["requested"] != bd["selected"] else " (kept as requested)") + f" - {bd['reason']}",
        "- Audience: " + ", ".join(f"{COHORT_LABELS[a['cohort']]} ({a['size']:,} hh)" for a in inc)
        + f" = {sum(a['size'] for a in inc):,} households",
        f"- PSGs: {len(psgs['include'])} included, {len(psgs['exclude'])} excluded; heroes: "
        + ", ".join(ctx.store.psgs[p]["name"] for p in psgs["hero"]),
    ]
    if brief["dissent"]:
        lines.append(f"- Dissent recorded: {len(brief['dissent'])}")
    if brief["notes_for_draft"]:
        lines.append(f"- Notes for the draft process: {len(brief['notes_for_draft'])}")
    return {"brief": brief, "summary_md": "\n".join(lines)}
