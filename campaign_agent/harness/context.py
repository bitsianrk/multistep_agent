"""State awareness and memory for agent prompts.

Every harness run gives the agent three kinds of context:

* run state   - a compact view of where the run is (stage, brand, objective,
                window, plan, pack, decisions, overrides, send-backs);
* memory      - working memory (notes agents wrote earlier in this run with
                `remember`) and episodic memory (past runs and human overrides
                in the same category);
* conversation - the last few chat turns, so a reply like "yes, but drop
                heavy" is read against what was just shown.

The same views back the get_run_state / recall_memory tools, so an agent can
re-read them mid-loop.
"""

from __future__ import annotations

from ..data.taxonomy import BRANDS, COHORT_LABELS

RECENT_TURNS = 8


def _brand(bid):
    if bid not in BRANDS:
        return None
    return {"brand_id": bid, "name": BRANDS[bid]["name"], "category_id": BRANDS[bid]["category"]}


def state_view(state: dict, section: str | None = None) -> dict:
    pack = state.get("pack") or {}
    plan = state.get("plan") or {}
    dec = state.get("decision") or {}
    props = state.get("proposals") or {}
    view = {
        "run": {"run_id": state.get("run_id"), "stage": state.get("stage"), "status": state.get("status"),
                "user": (state.get("user") or {}).get("id"), "send_backs": state.get("send_backs", 0),
                "brand_switches": state.get("brand_switches", 0), "tool_calls_used": state.get("tool_calls", 0)},
        "request": {"text": state.get("request"), "brand": _brand(state.get("brand_id")),
                    "requested_brand": _brand(state.get("requested_brand_id")),
                    "objective": state.get("objective"), "window": state.get("window")},
        "plan": {"tools": [s["tool"] for s in plan.get("steps", [])],
                 "added_by_planner": [s["reason"] for s in plan.get("steps", []) if s.get("added_by") == "planner"]}
        if plan else None,
        "pack": {"pack_id": pack.get("pack_id"), "blocks": sorted(pack.get("blocks", {}), key=int),
                 "reconciled": (pack.get("reconciliation") or {}).get("passed"),
                 "errors": len(pack.get("errors", [])), "extra_cuts": len(pack.get("extra_cuts", [])),
                 "audience_proposal": [c["cohort"] for c in pack.get("proposals", {}).get("audience", {})
                                       .get("cohorts", []) if c["include"]],
                 "psg_hero": pack.get("proposals", {}).get("psgs", {}).get("hero")} if pack else None,
        "discussion": {"top_alternative": props.get("top_alternative"), "brand_locked": props.get("locked"),
                       "human_positions": state.get("position_edits") or [],
                       "decision": {"brand": dec.get("brand_decision", {}).get("selected"),
                                    "audience": [COHORT_LABELS[a["cohort"]] for a in dec.get("audience", [])
                                                 if a["include"]],
                                    "hero": dec.get("qualifying_psgs", {}).get("hero"),
                                    "dissent": len(dec.get("dissent", [])),
                                    "human_overrides": dec.get("human_overrides", [])} if dec else None},
        "brief": {"ready": bool(state.get("brief")), "approved_by": (state.get("brief") or {}).get("approved_by")},
    }
    if section:
        return {section: view.get(section)}
    return view


def recent_conversation(state: dict, n: int = RECENT_TURNS) -> list[dict]:
    out = []
    for m in (state.get("chat") or [])[-n:]:
        who = "campaign_manager" if m["role"] == "human" else m.get("agent", "agent")
        out.append({"from": who, "text": m["text"][:400]})
    return out


def memory_view(state: dict, episodic, agent: str | None = None) -> dict:
    wm = state.get("working_memory") or []
    cat = BRANDS.get(state.get("brand_id"), {}).get("category")
    return {
        "working_memory": [w for w in wm if agent is None or w["agent"] in (agent, "campaign_manager")][-10:],
        "episodic": episodic.recall(cat) if (episodic is not None and cat) else {},
    }
