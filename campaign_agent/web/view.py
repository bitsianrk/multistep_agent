"""View model: turn a CampaignSession's state into what the web UI renders."""

from __future__ import annotations

from ..data.taxonomy import BRANDS, CATEGORIES, COHORT_LABELS
from ..graph import decision_card, diagnose_card
from ..guardrails import display_safe

STAGES = [("understand", "Understand"), ("plan", "Plan"), ("diagnose", "Diagnose"),
          ("discuss", "Discuss"), ("brief", "Agreed Brief")]
_ORDER = [s for s, _ in STAGES]


def _brand(bid):
    if bid not in BRANDS:
        return None
    b = BRANDS[bid]
    return {"brand_id": bid, "name": b["name"], "category": CATEGORIES[b["category"]]["name"]}


def _stages(current: str | None, done: bool) -> list[dict]:
    cur = _ORDER.index(current) if current in _ORDER else (len(_ORDER) if done else 0)
    if done:
        cur = len(_ORDER)
    return [{"key": k, "label": label,
             "status": "done" if i < cur else "current" if i == cur else "todo"}
            for i, (k, label) in enumerate(STAGES)]


def _psg_names(store, ids):
    return [{"psg_id": p, "name": store.psgs[p]["name"]} for p in ids if p in store.psgs]


def run_view(session) -> dict:
    v = session.values
    store = session.harness.registry.ctx.store
    pending = session.pending
    stage = (pending or {}).get("stage") or v.get("stage")
    out = {
        "run_id": session.run_id,
        "lrm_mode": session.harness.mode,
        "user": session.user["id"],
        "status": v.get("status"),
        "done": session.done,
        "stage": stage,
        "stages": _stages(stage, session.done),
        "pending": pending and {"stage": pending["stage"], "awaiting": pending["awaiting"],
                                "title": pending["card"]["title"], "summary": pending["card"]["summary"],
                                "key_numbers": pending["card"]["key_numbers"],
                                "actions": pending["card"]["actions"]},
        "chat": [{"role": m["role"], "agent": m.get("agent"), "text": m["text"]} for m in v.get("chat", [])],
        "request": {"text": v.get("request"), "brand": _brand(v.get("brand_id")),
                    "requested_brand": _brand(v.get("requested_brand_id")),
                    "objective": v.get("objective"), "window": v.get("window")},
        "counters": {"tool_calls": v.get("tool_calls", 0), "send_backs": v.get("send_backs", 0),
                     "brand_switches": v.get("brand_switches", 0)},
        "plan": None, "pack": None, "discussion": None, "brief": None,
        "harness_log": v.get("harness_log", []),
        "working_memory": v.get("working_memory", []),
        "audit": v.get("audit", [])[-50:],
    }

    plan = v.get("plan")
    if plan:
        out["plan"] = {"signals": plan.get("signals", {}), "steps": [
            {"agent": s["agent"], "tool": s["tool"], "block": s["block"], "reason": s["reason"],
             "added_by": s["added_by"],
             "params": {k: v2 for k, v2 in s["params"].items() if k not in ("window_start", "window_weeks")}}
            for s in plan["steps"]]}

    pack = v.get("pack")
    if pack:
        summary, kn = diagnose_card(pack)
        aud = pack["proposals"]["audience"]
        psg = pack["proposals"]["psgs"]
        out["pack"] = display_safe({
            "pack_id": pack["pack_id"], "key_numbers": kn, "notes": summary[1:],
            "reconciliation": pack["reconciliation"],
            "findings": [{"agent": a, "findings": f["findings"], "watch": f["watch"]}
                         for a, f in pack.get("findings", {}).items()],
            "audience": [{"cohort": c["cohort"], "label": c["label"], "size": c["size"], "include": c["include"],
                          "reason": c["reason"]} for c in aud["cohorts"]],
            "audience_total": aud["total_included"],
            "psgs": {"include": _psg_names(store, psg["include"]), "hero": _psg_names(store, psg["hero"]),
                     "exclude": [{**e, "name": store.psgs[e["psg_id"]]["name"]} for e in psg["exclude"]]},
            "extra_cuts": pack.get("extra_cuts", []),
            "errors": pack.get("errors", []),
        })

    props, dec = v.get("proposals"), v.get("decision")
    if props or dec:
        d = {"proposals": [], "decision": None}
        if props:
            d["top_alternative"] = _brand(props.get("top_alternative"))
            d["locked"] = props.get("locked")
            for p in props["proposals"]:
                pos = p["position"]
                d["proposals"].append({
                    "label": p["label"], "brand": _brand(pos.get("brand")),
                    "cohorts": [COHORT_LABELS[k.split(":")[1]] for k, val in pos.items()
                                if k.startswith("cohort:") and val],
                    "excluded_psgs": sum(1 for k, val in pos.items() if k.startswith("psg:") and val),
                    "hero": _psg_names(store, p["hero"]),
                    "claims": [{"topic": c["topic"], "text": c["text"], "cites": c["cites"]} for c in p["claims"]],
                })
        if dec:
            summary, kn = decision_card(dec, store)
            q = dec["qualifying_psgs"]
            d["decision"] = {
                "key_numbers": kn,
                "brand_decision": {**dec["brand_decision"], "selected_brand": _brand(dec["brand_decision"]["selected"]),
                                   "requested_brand": _brand(dec["brand_decision"]["requested"])},
                "audience": [{**a, "label": COHORT_LABELS[a["cohort"]]} for a in dec["audience"]],
                "hero": _psg_names(store, q["hero"]),
                "include": _psg_names(store, q["include"]),
                "exclude": [{**e, "name": store.psgs[e["psg_id"]]["name"]} for e in q["exclude"]],
                "dissent": dec.get("dissent", []), "notes_for_draft": dec.get("notes_for_draft", []),
                "rounds": dec.get("rounds"), "agreement": dec.get("agreement"),
                "converged": dec.get("converged"), "human_overrides": dec.get("human_overrides", []),
                "transcript": [{"round": t["round"], "speaker": t["speaker"], "text": t["text"],
                                "in_scope": t.get("in_scope", True)} for t in dec.get("transcript", [])],
            }
        out["discussion"] = d

    if v.get("brief"):
        out["brief"] = {"json": v["brief"], "summary_md": v.get("brief_summary", ""), "handoff": v.get("handoff")}
    return out


def run_summary(session) -> dict:
    v = session.values
    return {"run_id": session.run_id, "request": v.get("request"), "brand": _brand(v.get("brand_id")),
            "stage": (session.pending or {}).get("stage") or v.get("stage"), "done": session.done}
