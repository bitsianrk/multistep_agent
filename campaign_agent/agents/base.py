"""Agents own judgement; tools own capability.

An agent is a persona + goal + an allow-list of tools. Agents reach data
only through registry calls, which enforce the allow-list and record a trace
entry (tool, version, latency) for observability.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Settings
from ..data import DataStore
from ..tools.registry import RunBudget, ToolRegistry


@dataclass
class AgentSpec:
    key: str
    name: str
    role: str
    tools: frozenset[str]
    whiteboard: str


ROSTER: dict[str, AgentSpec] = {s.key: s for s in [
    AgentSpec("chat_gateway", "Chat Gateway",
              "Receives the chat trigger, routes human messages to the active stage, renders checkpoints",
              frozenset({"route_message", "render_checkpoint"}), "Human chat window"),
    AgentSpec("planner", "Campaign Planning Agent",
              "Parses request, sets objective, builds plan, holds state",
              frozenset({"plan_campaign", "resume_from_stage"}), "Campaign Planning Agent"),
    AgentSpec("entity_resolution", "Entity Resolution Agent",
              "Maps free text to exactly one brand; asks back when ambiguous",
              frozenset({"ner_extract", "taxonomy_lookup", "disambiguate"}), "NER + Taxonomy of Product Knowledge"),
    AgentSpec("brand_diagnostics", "Brand Diagnostics Agent", "Blocks 1-4",
              frozenset({"brand_shoppers", "brand_spc", "cohort_split", "spend_distribution"}),
              "Customers / Spend / ABV panel"),
    AgentSpec("qualifying_psg", "Qualifying PSG Agent", "Block 5: include / exclude, hero PSGs",
              frozenset({"psg_score", "stock_check", "promo_clash_check"}), "Offer (PSGs)"),
    AgentSpec("market_intelligence", "Market Intelligence Agent", "Block 7",
              frozenset({"kantar_share", "kantar_penetration", "sentiment_scan"}), "(new)"),
    AgentSpec("commercial", "Commercial Agent", "Block 8",
              frozenset({"margin_after_funding", "supplier_funding_lookup"}), "Supplier / Buyer"),
    AgentSpec("performance_memory", "Performance Memory Agent", "Blocks 6 and 9",
              frozenset({"seasonality_index", "multistep_history"}), "(new)"),
    AgentSpec("brand_scout", "Brand Scout Agent", "Block 10: alternative brands in the category",
              frozenset({"category_brands", "brand_scout_score"}), "(new)"),
    AgentSpec("audience", "Audience Agent", "Proposes cohorts with sizes and rationale",
              frozenset({"cohort_size", "audience_propose"}), "INPUT (customer-wise)"),
    AgentSpec("reach_advocate", "Reach advocate", "Argues brand, audience and PSGs for maximum reach",
              frozenset(), "Discussion loop"),
    AgentSpec("margin_advocate", "Margin advocate", "Argues brand, audience and PSGs to protect margin",
              frozenset(), "Discussion loop"),
    AgentSpec("customer_advocate", "Customer advocate", "Argues brand, audience and PSGs from customer response",
              frozenset(), "Discussion loop"),
    AgentSpec("critic_judge", "Critic / Judge",
              "Checks numbers reconcile, enforces discussion scope, decides convergence",
              frozenset({"reconcile_metrics", "scope_check"}), "Discussion loop"),
    AgentSpec("pack_analyst", "Pack Analyst", "Answers campaign-manager questions from the Diagnostic Pack; "
              "runs one more cut when the pack does not have it",
              frozenset({"brand_spc", "brand_shoppers", "cohort_size", "spend_distribution"}), "Human chat window"),
    AgentSpec("brief_writer", "Brief Writer", "Writes the Agreed Brief and chat summary",
              frozenset({"write_brief"}), "Hand-off to Recommendation Draft"),
]}


@dataclass
class AgentRuntime:
    """Per-run services handed to agents. The trace collects every tool call."""

    registry: ToolRegistry
    settings: Settings
    store: DataStore
    user: dict
    run_id: str
    budget: RunBudget
    trace: list[dict] = field(default_factory=list)


class Agent:
    key: str = ""

    def __init__(self, rt: AgentRuntime):
        self.rt = rt
        self.spec = ROSTER[self.key]

    @property
    def name(self) -> str:
        return self.spec.name

    def call(self, tool: str, **args) -> dict:
        args = {k: v for k, v in args.items() if v is not None}
        try:
            res = self.rt.registry.call(tool, args, caller=self.key, allow_list=set(self.spec.tools),
                                        role=self.rt.user["role"], budget=self.rt.budget)
        except Exception as exc:
            self.rt.trace.append({"agent": self.key, "tool": tool, "ok": False, "error": str(exc)})
            raise
        self.rt.trace.append({"agent": self.key, "tool": tool, "version": res["version"], "ok": True,
                              "latency_ms": res["latency_ms"],
                              "args": {k: v for k, v in args.items() if k not in ("pack", "decision")}})
        return res

    def out(self, tool: str, **args) -> dict:
        return self.call(tool, **args)["output"]
