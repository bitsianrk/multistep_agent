"""Registry and guardrail controls from the design's risk table."""

from __future__ import annotations

import time
from dataclasses import replace

import pytest

from campaign_agent.config import DEFAULT_SETTINGS
from campaign_agent.data import get_store
from campaign_agent.guardrails import AccessDenied, authorize, display_safe
from campaign_agent.session import CampaignSession
from campaign_agent.skills.registry import RunBudget, SkillCallError, SkillRegistry

ROLE = "campaign_manager_role"


def test_agent_allow_list_is_enforced(registry):
    with pytest.raises(SkillCallError, match="not allowed"):
        registry.call("kantar_share", {"brand_id": "BR001"}, caller="brand_diagnostics",
                      allow_list={"brand_spc"}, role=ROLE)


def test_role_outside_access_scope_is_refused(registry):
    with pytest.raises(SkillCallError, match="access scope"):
        registry.call("brand_spc", {"brand_id": "BR001"}, caller="x", allow_list={"brand_spc"}, role="supplier")


def test_session_refuses_non_campaign_managers():
    with pytest.raises(AccessDenied):
        CampaignSession(user={"id": "sup.1", "role": "supplier"})
    with pytest.raises(AccessDenied):
        authorize({"id": "x", "role": ROLE, "external": True})


def test_typed_contract(registry):
    allow = {"brand_scout_score", "brand_spc"}
    with pytest.raises(SkillCallError, match="must be int"):
        registry.call("brand_scout_score", {"brand_id": "BR001", "window_start": "2026-11-16", "window_weeks": "4"},
                      caller="x", allow_list=allow, role=ROLE)
    with pytest.raises(SkillCallError, match="ISO date"):
        registry.call("brand_scout_score", {"brand_id": "BR001", "window_start": "16 Nov", "window_weeks": 4},
                      caller="x", allow_list=allow, role=ROLE)
    with pytest.raises(SkillCallError, match="unknown inputs"):
        registry.call("brand_spc", {"brand_id": "BR001", "sql": "DROP TABLE"}, caller="x", allow_list=allow, role=ROLE)
    with pytest.raises(SkillCallError, match="unknown brand_id"):
        registry.call("brand_spc", {"brand_id": "BR999"}, caller="x", allow_list=allow, role=ROLE)


def test_budget_caps_skill_calls(registry):
    budget = RunBudget(max_calls=1)
    registry.call("brand_spc", {"brand_id": "BR001"}, caller="x", allow_list={"brand_spc"}, role=ROLE, budget=budget)
    with pytest.raises(SkillCallError, match="budget"):
        registry.call("brand_spc", {"brand_id": "BR001"}, caller="x", allow_list={"brand_spc"}, role=ROLE, budget=budget)


def test_latency_sla_timeout():
    s = replace(DEFAULT_SETTINGS, skill_timeout_multiplier=1e-4)  # 3000 ms SLA -> 0.3 ms
    reg = SkillRegistry(s, get_store(s))
    reg.skills["cohort_split"].fn = lambda ctx, **kw: time.sleep(0.2) or {}
    with pytest.raises(SkillCallError, match="SLA"):
        reg.call("cohort_split", {"brand_id": "BR001"}, caller="x", allow_list={"cohort_split"}, role=ROLE)


def test_minimum_cell_size_suppresses_small_cells():
    s = replace(DEFAULT_SETTINGS, min_cell_size=600)
    reg = SkillRegistry(s, get_store(s))
    out = reg.call("cohort_size", {"brand_id": "BR001", "cohort": "lapsed"}, caller="x",
                   allow_list={"cohort_size"}, role=ROLE)["output"]
    assert out["suppressed"] and out["size"] is None
    split = reg.call("cohort_split", {"brand_id": "BR001"}, caller="x", allow_list={"cohort_split"}, role=ROLE)["output"]
    assert any(c["suppressed"] and c["households"] is None for c in split["loyalty_tier"])


def test_kantar_figures_carry_period_and_licence_and_panel_detail_is_stripped(registry):
    res = registry.call("kantar_share", {"brand_id": "BR001"}, caller="x", allow_list={"kantar_share"}, role=ROLE)
    out = res["output"]
    assert out["period"] and out["licence_scope"] == "tesco-internal"
    assert "panel_sample_households" in out
    assert "panel_sample_households" not in display_safe(out)
    assert res["provenance"] == [{"source": "kantar_cache", "as_of": "2026 P10"}]


def test_kantar_is_cached_per_release_period(registry):
    args = {"brand_id": "BR002"}
    registry.call("kantar_penetration", args, caller="x", allow_list={"kantar_penetration"}, role=ROLE)
    again = registry.call("kantar_penetration", args, caller="x", allow_list={"kantar_penetration"}, role=ROLE)
    assert again["output"]["cache_hit"] is True


def test_chat_text_cannot_add_unregistered_skills(registry):
    from campaign_agent.agents.base import AgentRuntime
    from campaign_agent.agents.understand import PlannerAgent

    rt = AgentRuntime(registry=registry, settings=DEFAULT_SETTINGS, store=registry.ctx.store,
                      user={"id": "t", "role": ROLE}, run_id="t", budget=RunBudget(50))
    planner = PlannerAgent(rt)
    plan = planner.plan("BR001", "reactivate", {"start": "2026-11-16", "weeks": 4})
    new, notes = planner.apply_edits(plan, [{"op": "add_skill", "skill": "send_email"},
                                            {"op": "remove_skill", "skill": "brand_spc"}], registry.names())
    assert new["steps"] == plan["steps"]
    assert any("not a diagnostic skill" in n for n in notes) and any("core ask" in n for n in notes)
