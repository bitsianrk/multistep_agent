"""Evaluation scorecard (offline, on the dummy data).

    python -m campaign_agent.evaluation

* Metric accuracy - tool outputs vs an independent "certified" recomputation
  (stands in for certified SQL);
* Brand resolution accuracy - a labelled request set;
* Run metrics from audit logs - override rate by stage, Brand Scout
  acceptance, scope adherence, time to Agreed Brief, faithfulness.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime

from .agents.base import AgentRuntime
from .agents.understand import EntityResolutionAgent
from .config import DEFAULT_SETTINGS, Settings
from .data import get_store
from .data.taxonomy import BRANDS
from .tools.registry import RunBudget, get_registry

LABELLED_REQUESTS = [
    ("Plan a Multi-Step for Crunchwell to win back lapsed shoppers", "BR001"),
    ("crunchwel crisps christmas push", "BR001"),
    ("Golden Harvest frequency campaign", "BR002"),
    ("Snapjack sharing bags for the festive period", "BR003"),
    ("grow golden coffee shoppers", "BR004"),
    ("Morning Ritual retention", "BR005"),
    ("barista bay trial", "BR006"),
    ("Meadow Fresh yoghurt multi-step", "BR007"),
    ("alpine swirl lapsed", "BR008"),
    ("Greek Isle new shoppers in January", "BR009"),
    ("golden crisps", "BR002"),
    ("something for golden", None),  # ambiguous: must ask back, not guess
]


def certified_metrics(store, brand_id: str) -> dict:
    """Independent, deliberately plain recomputation of blocks 1-2."""
    shoppers = sales = 0
    cat_hh = set()
    for r in store.customer_brand:
        if r["category_id"] == BRANDS[brand_id]["category"] and r["trips_6w"] > 0:
            cat_hh.add(r["hh_id"])
        if r["brand_id"] == brand_id and r["trips_6w"] > 0:
            shoppers += 1
            sales += r["spend_6w"]
    return {"shoppers_6w": shoppers, "spc": round(sales / shoppers, 2),
            "penetration_pct": round(100 * shoppers / len(cat_hh), 1)}


def metric_accuracy(settings: Settings = DEFAULT_SETTINGS) -> dict:
    store = get_store(settings)
    reg = get_registry(settings, store)
    allow = set(reg.names())
    rows, ok = [], 0
    for b in BRANDS:
        cert = certified_metrics(store, b)
        b1 = reg.call("brand_shoppers", {"brand_id": b}, caller="eval", allow_list=allow,
                      role="campaign_manager_role")["output"]
        b2 = reg.call("brand_spc", {"brand_id": b}, caller="eval", allow_list=allow,
                      role="campaign_manager_role")["output"]
        got = {"shoppers_6w": b1["shoppers_6w"], "spc": b2["spc"], "penetration_pct": b1["penetration_pct"]}
        match = got == cert
        ok += match
        rows.append({"brand_id": b, "match": match, "tool": got, "certified": cert})
    return {"accuracy_pct": round(100 * ok / len(rows), 1), "rows": rows}


def brand_resolution_accuracy(settings: Settings = DEFAULT_SETTINGS) -> dict:
    store = get_store(settings)
    reg = get_registry(settings, store)
    rt = AgentRuntime(registry=reg, settings=settings, store=store,
                      user={"id": "eval", "role": "campaign_manager_role"}, run_id="eval",
                      budget=RunBudget(10_000))
    er = EntityResolutionAgent(rt)
    rows, ok = [], 0
    for text, expected in LABELLED_REQUESTS:
        got = er.resolve(text)["brand_id"]
        ok += got == expected
        rows.append({"text": text, "expected": expected, "got": got})
    return {"accuracy_pct": round(100 * ok / len(rows), 1), "rows": rows}


def run_metrics(values: dict) -> dict:
    """Scorecard metrics derivable from one run's state and audit log."""
    audit = values.get("audit", [])
    msgs = [a for a in audit if a["event"] == "message"]
    by_stage = Counter(a["stage"] for a in msgs)
    overrides = Counter(a["stage"] for a in msgs if a["action"] in ("edit", "send_back"))
    dec = values.get("decision") or {}
    transcript = dec.get("transcript", [])
    t0 = datetime.fromisoformat(audit[0]["ts"]) if audit else None
    t1 = next((datetime.fromisoformat(a["ts"]) for a in audit if a["event"] == "handoff"), None)
    proposed_switch = any(a["event"] == "brand_switch" for a in audit)
    return {
        "override_rate_by_stage": {s: round(overrides[s] / n, 2) for s, n in by_stage.items()},
        "brand_scout_switch_accepted": proposed_switch,
        "scope_adherence_pct": dec.get("scope_adherence_pct"),
        "faithfulness_pct": round(100 * sum(c.get("cites_valid", True) for c in transcript) / len(transcript), 1)
        if transcript else None,
        "time_to_agreed_brief_s": (t1 - t0).total_seconds() if t0 and t1 else None,
        "tool_calls": values.get("tool_calls"),
        "agent_skill_runs": len(values.get("harness_log", [])),
        "llm_share_pct": round(100 * sum(r["source"] == "llm" for r in values.get("harness_log", []))
                               / max(1, len(values.get("harness_log", []))), 1),
        "fallbacks": [{"skill": r["skill"], "reason": r["reason"]} for r in values.get("harness_log", [])
                      if r["source"] != "llm"],
        "outputs_rejected_by_guards": sum(r.get("rejected", 0) for r in values.get("harness_log", [])),
    }


def main() -> int:
    import tempfile
    from pathlib import Path

    from .demo import run

    out = Path(tempfile.mkdtemp(prefix="campaign_eval_"))
    session = run("switch", out, quiet=True)
    card = {
        "metric_accuracy": metric_accuracy(),
        "brand_resolution": brand_resolution_accuracy(),
        "demo_run": run_metrics(session.values),
    }
    for k in ("metric_accuracy", "brand_resolution"):
        card[k] = {"accuracy_pct": card[k]["accuracy_pct"],
                   "misses": [r for r in card[k]["rows"] if not r.get("match", r.get("got") == r.get("expected"))]}
    print(json.dumps(card, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
