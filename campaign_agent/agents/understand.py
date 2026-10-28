"""Stage 1 and 2 agents: Entity Resolution and the Campaign Planning Agent."""

from __future__ import annotations

from ..data.taxonomy import BRANDS
from .base import Agent

OBJECTIVES = ["reactivate", "grow", "retain", "trial"]


class EntityResolutionAgent(Agent):
    key = "entity_resolution"

    def resolve(self, text: str) -> dict:
        ner = self.out("ner_extract", text=text)
        brands = ner["brand_mentions"]
        result = {
            "brand_id": None, "candidates": [b["brand_id"] for b in brands],
            "objective": ner["objective"], "window": ner["window"], "questions": [], "confidence": 0.0,
        }
        if len(brands) == 1:
            result["brand_id"] = brands[0]["brand_id"]
            result["confidence"] = brands[0]["score"]
        return self._questions(result)

    def apply_answer(self, pending: dict, reply: str, edits: list[dict]) -> dict:
        res = dict(pending)
        if res["brand_id"] is None and res["candidates"]:
            pick = self.out("disambiguate", text=reply, candidates=res["candidates"])
            if pick["brand_id"]:
                res["brand_id"], res["confidence"] = pick["brand_id"], pick["confidence"]
        if res["brand_id"] is None:
            fresh = self.out("ner_extract", text=reply)["brand_mentions"]
            if len(fresh) == 1:
                res["brand_id"], res["confidence"] = fresh[0]["brand_id"], fresh[0]["score"]
            elif fresh:
                res["candidates"] = [b["brand_id"] for b in fresh]
        if res["objective"] is None:
            ner = self.out("ner_extract", text=reply)
            obj = ner["objective"] or next((o for o in OBJECTIVES if o in reply.lower()), None)
            res["objective"] = obj
        for e in edits:
            if e["op"] == "set_window":
                res["window"] = e["window"]
            elif e["op"] == "set_objective":
                res["objective"] = e["objective"]
            elif e["op"] in ("switch_brand", "keep_brand") and e.get("brand_id"):
                res["brand_id"], res["confidence"] = e["brand_id"], 1.0
        return self._questions(res)

    def _questions(self, res: dict) -> dict:
        qs = []
        if res["brand_id"] is None:
            if res["candidates"]:
                opts = "; ".join(f"{i}. {BRANDS[b]['name']} ({self.out('taxonomy_lookup', brand_id=b)['category_name']})"
                                 for i, b in enumerate(res["candidates"], start=1))
                qs.append(f"Which brand did you mean? {opts}")
            else:
                qs.append("Which brand should this campaign plan for?")
        if res["objective"] is None:
            qs.append("What is the objective: reactivate (lapsed), grow (frequency/spend), retain (loyal), or trial (new)?")
        res["questions"] = qs
        res["status"] = "resolved" if not qs else "needs_clarification"
        if res["brand_id"]:
            res["brand"] = self.out("taxonomy_lookup", brand_id=res["brand_id"])
        return res


class PlannerAgent(Agent):
    key = "planner"

    def plan(self, brand_id: str, objective: str, window: dict) -> dict:
        return self.out("plan_campaign", brand_id=brand_id, objective=objective,
                        window_start=window["start"], window_weeks=window["weeks"])

    def resume(self, brand_id: str, stage: str) -> dict:
        return self.out("resume_from_stage", brand_id=brand_id, stage=stage)

    def apply_edits(self, plan: dict, edits: list[dict], registry_names: list[str]) -> tuple[dict, list[str]]:
        """Plan edits are restricted to registry tools (prompt-injection guardrail)."""
        from ..tools.servers.planning import BASE_PLAN, CORE_TOOLS, TOOL_AGENT

        plan = {**plan, "steps": [dict(s) for s in plan["steps"]]}
        notes = []
        window = {"window_start": plan["window"]["start"], "window_weeks": plan["window"]["weeks"]}
        windowed = {tool for _, tool, _, _, needs in BASE_PLAN if needs}
        for e in edits:
            if e["op"] == "remove_tool":
                if e["tool"] in CORE_TOOLS:
                    notes.append(f"{e['tool']} is a core ask (blocks 1-3) and stays in the plan.")
                    continue
                before = len(plan["steps"])
                plan["steps"] = [s for s in plan["steps"] if s["tool"] != e["tool"]]
                notes.append(f"Removed {e['tool']}." if len(plan["steps"]) < before
                             else f"{e['tool']} was not in the plan.")
            elif e["op"] == "add_tool":
                if e["tool"] not in registry_names or e["tool"] not in TOOL_AGENT:
                    notes.append(f"{e['tool']} is not a diagnostic tool in the registry; not added.")
                    continue
                if any(s["tool"] == e["tool"] and not s["params"].get("cut") for s in plan["steps"]):
                    notes.append(f"{e['tool']} is already in the plan.")
                    continue
                block = next((b for _, sk, b, _, _ in BASE_PLAN if sk == e["tool"]), None)
                plan["steps"].append({"agent": TOOL_AGENT[e["tool"]], "tool": e["tool"], "block": block,
                                      "params": dict(window) if e["tool"] in windowed else {},
                                      "reason": "Added by campaign manager", "added_by": "human"})
                notes.append(f"Added {e['tool']}.")
        for i, s in enumerate(plan["steps"], start=1):
            s["id"] = f"s{i}"
        return plan, notes
