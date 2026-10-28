"""Stage 3: the five tool agents run in parallel and build the Diagnostic Pack.

Then the Qualifying PSG Agent and the Audience Agent turn the evidence into
proposals, and the Critic reconciles the numbers before the human sees them.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from ..data.taxonomy import AUDIENCE_COHORTS
from .base import Agent, AgentRuntime

BLOCK_NAMES = {
    1: "Brand shoppers", 2: "SPC", 3: "Cohort split", 4: "Spend distribution", 5: "Qualifying PSGs",
    6: "Seasonal strength", 7: "Market sentiment", 8: "Profitability", 9: "Multi-Step history",
    10: "Brand alternatives",
}
BLOCK_FEEDS = {1: "Audience", 2: "Audience", 3: "Audience", 4: "Audience", 5: "PSGs", 6: "Brand",
               7: "Brand", 8: "PSGs, Brand", 9: "Audience", 10: "Brand"}
WINDOW_PARAMS = {"window_start", "window_weeks"}


class BrandDiagnosticsAgent(Agent):
    key = "brand_diagnostics"


class QualifyingPSGAgent(Agent):
    key = "qualifying_psg"

    def propose(self, pack: dict, hero_count: int) -> dict:
        b5 = pack["blocks"].get("5", {}).get("data", {})
        b6 = pack["blocks"].get("6", {}).get("data", {})
        b8 = pack["blocks"].get("8", {}).get("data", {})
        scores = {p["psg_id"]: p for p in b5.get("psg_score", {}).get("psgs", [])}
        stock = {p["psg_id"]: p for p in b5.get("stock_check", {}).get("psgs", [])}
        clashes = {c["psg_id"]: c for c in b5.get("promo_clash_check", {}).get("clashes", [])}
        margin = {p["psg_id"]: p for p in b8.get("margin_after_funding", {}).get("psgs", [])}
        psg_season = next((v["by_psg"] for k, v in b6.items() if k.startswith("seasonality_index[")
                           and "by_psg" in v), {})
        include, exclude = [], []
        for psg_id, s in scores.items():
            reasons, filters = [], []
            st = stock.get(psg_id)
            if st and not st["ok"]:
                reasons.append(f"below cover threshold ({st['forecast_cover_weeks']} < {st['required_cover_weeks']} wks)")
                filters.append("availability")
            if psg_id in clashes:
                c = clashes[psg_id]
                reasons.append(f"double discount: live {c['mechanic']} until {c['end']}")
                filters.append("promo_clash")
            m = margin.get(psg_id)
            if m and m["below_floor"]:
                reasons.append(f"margin after funding {m['margin_after_funding_pct']}% below floor "
                               f"{b8['margin_after_funding']['margin_floor_pct']}%")
                filters.append("margin")
            if reasons:
                exclude.append({"psg_id": psg_id, "name": s["name"], "reason": "; ".join(reasons),
                                "filters": filters})
            else:
                include.append(psg_id)

        def hero_key(pid):
            return scores[pid]["hero_score"] * psg_season.get(pid, 1.0)

        hero = sorted(include, key=hero_key, reverse=True)[:hero_count]
        return {"include": include, "exclude": exclude, "hero": hero,
                "hero_basis": "reach x momentum" + (" x PSG seasonality" if psg_season else ""),
                "scores": {pid: round(hero_key(pid), 3) for pid in scores}}


class MarketIntelligenceAgent(Agent):
    key = "market_intelligence"


class CommercialAgent(Agent):
    key = "commercial"


class PerformanceMemoryAgent(Agent):
    key = "performance_memory"


class BrandScoutAgent(Agent):
    key = "brand_scout"


class AudienceAgent(Agent):
    key = "audience"

    def propose(self, brand_id: str, objective: str, pack: dict, extra_steps: list[dict],
                cohort_bias: dict) -> dict:
        sizes, details = {}, {}
        for cohort in AUDIENCE_COHORTS:
            res = self.out("cohort_size", brand_id=brand_id, cohort=cohort)
            sizes[cohort] = res["size"]
        for step in extra_steps:
            res = self.out("cohort_size", brand_id=brand_id, **step["params"])
            details[f"{step['params'].get('cohort')}_by_{step['params'].get('by')}"] = res
        hist = pack["blocks"].get("9", {}).get("data", {}).get("multistep_history", {})
        response = hist.get("by_cohort", {})
        prop = self.out("audience_propose", brand_id=brand_id, objective=objective, cohort_sizes=sizes,
                        response=response, cohort_bias=cohort_bias or None)
        prop["details"] = details
        return prop


class CriticJudge(Agent):
    key = "critic_judge"

    def reconcile(self, brand_id: str, pack: dict) -> dict:
        return self.out("reconcile_metrics", brand_id=brand_id, pack=pack)

    def scope(self, brand_id: str, claims: list[dict]) -> dict:
        return self.out("scope_check", brand_id=brand_id, claims=claims)


SPECIALISTS = {cls.key: cls for cls in [BrandDiagnosticsAgent, QualifyingPSGAgent, MarketIntelligenceAgent,
                                        CommercialAgent, PerformanceMemoryAgent, BrandScoutAgent]}


def step_key(step: dict) -> str:
    extra = {k: v for k, v in step["params"].items() if k not in WINDOW_PARAMS}
    if not extra:
        return step["tool"]
    return f"{step['tool']}[{','.join(f'{k}={v}' for k, v in sorted(extra.items()))}]"


def _run_agent_steps(agent: Agent, brand_id: str, steps: list[dict]) -> list[tuple[dict, dict | None, str | None]]:
    results = []
    for step in steps:
        try:
            res = agent.call(step["tool"], brand_id=brand_id, **step["params"])
            results.append((step, res, None))
        except Exception as exc:  # a failed tool degrades the pack, it does not stop the run
            results.append((step, None, str(exc)))
    return results


def build_pack(rt: AgentRuntime, brand_id: str, objective: str, window: dict, plan: dict,
               cohort_bias: dict, hero_bias: dict, switch_bias: float, pack_seq: int) -> dict:
    pack = {
        "pack_id": f"pack-{rt.run_id}-{brand_id}-{pack_seq}",
        "brand_id": brand_id, "objective": objective, "window": window,
        "blocks": {}, "errors": [], "extra_cuts": [], "proposals": {},
    }
    by_agent: dict[str, list[dict]] = {}
    audience_extra = []
    for step in plan["steps"]:
        if step["agent"] == "audience":
            if step["tool"] == "cohort_size" and step["params"]:
                audience_extra.append(step)
            continue
        params = dict(step["params"])
        if step["tool"] == "psg_score" and hero_bias:
            params["hero_bias"] = hero_bias
        if step["tool"] == "brand_scout_score" and switch_bias:
            params["switch_bias"] = switch_bias
        by_agent.setdefault(step["agent"], []).append({**step, "params": params})

    agents = {key: SPECIALISTS[key](rt) for key in by_agent}
    with ThreadPoolExecutor(max_workers=len(agents) or 1, thread_name_prefix="agent") as pool:
        futures = {key: pool.submit(_run_agent_steps, agents[key], brand_id, steps)
                   for key, steps in by_agent.items()}
        outcomes = {key: f.result() for key, f in futures.items()}

    for key in by_agent:  # deterministic assembly order
        for step, res, err in outcomes[key]:
            block = str(step["block"])
            entry = pack["blocks"].setdefault(block, {
                "block": int(step["block"]), "name": BLOCK_NAMES[int(step["block"])],
                "feeds": BLOCK_FEEDS[int(step["block"])], "agent": key, "data": {}, "provenance": [],
            })
            if err:
                pack["errors"].append({"block": block, "tool": step["tool"], "error": err})
                continue
            entry["data"][step_key(step)] = res["output"]
            for p in res["provenance"]:
                if p not in entry["provenance"]:
                    entry["provenance"].append(p)

    psg_agent = QualifyingPSGAgent(rt)
    pack["proposals"]["psgs"] = psg_agent.propose(pack, rt.settings.hero_psg_count)
    pack["proposals"]["audience"] = AudienceAgent(rt).propose(brand_id, objective, pack, audience_extra, cohort_bias)
    pack["reconciliation"] = CriticJudge(rt).reconcile(brand_id, pack)
    return pack
