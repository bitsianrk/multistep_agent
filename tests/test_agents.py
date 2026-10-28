"""Agent-level behaviour: diagnostics reconciliation, PSG rules, debate protocol."""

from __future__ import annotations

from campaign_agent.agents.base import AgentRuntime
from campaign_agent.agents.debate import propose, run_debate
from campaign_agent.agents.diagnose import build_pack
from campaign_agent.agents.understand import PlannerAgent
from campaign_agent.config import DEFAULT_SETTINGS
from campaign_agent.evaluation import brand_resolution_accuracy, metric_accuracy
from campaign_agent.skills.registry import RunBudget

WINDOW = {"start": "2026-11-16", "weeks": 4}


def _rt(registry):
    return AgentRuntime(registry=registry, settings=DEFAULT_SETTINGS, store=registry.ctx.store,
                        user={"id": "t", "role": "campaign_manager_role"}, run_id="t", budget=RunBudget(500))


def _pack(registry, brand="BR001", objective="reactivate"):
    rt = _rt(registry)
    plan = PlannerAgent(rt).plan(brand, objective, WINDOW)
    return rt, build_pack(rt, brand, objective, WINDOW, plan, {}, {}, 0.0, 1)


def test_pack_has_all_ten_blocks_with_provenance_and_reconciles(registry):
    for brand in ("BR001", "BR004", "BR007"):
        _, pack = _pack(registry, brand)
        assert sorted(pack["blocks"], key=int) == [str(i) for i in range(1, 11)]
        assert all(b["provenance"] for k, b in pack["blocks"].items())
        assert pack["reconciliation"]["passed"], pack["reconciliation"]
        assert not pack["errors"]


def test_each_agent_only_called_its_own_skills(registry):
    from campaign_agent.agents.base import ROSTER

    rt, _ = _pack(registry)
    for t in rt.trace:
        assert t["skill"] in ROSTER[t["agent"]].skills, t


def test_psg_hard_filters(registry):
    _, pack = _pack(registry)
    ex = {e["psg_id"]: e["filters"] for e in pack["proposals"]["psgs"]["exclude"]}
    assert ex["PSG-BR001-02"] == ["promo_clash"]      # live Clubcard Price overlaps window
    assert ex["PSG-BR001-04"] == ["availability"]     # 1.4 wks cover < 4 wk window
    assert "margin" in ex["PSG-BR001-05"]             # below margin floor
    assert not set(pack["proposals"]["psgs"]["hero"]) & set(ex)


def test_debate_cites_pack_blocks_and_rejects_off_scope_claims(registry):
    rt, pack = _pack(registry, "BR003")
    props = propose(rt, pack, "BR003", "BR003", locked=False)
    assert len(props["proposals"]) == 3
    dec = run_debate(rt, pack, props, None)
    assert dec["rounds"] <= DEFAULT_SETTINGS.max_debate_rounds
    assert all(c["cites_valid"] for c in dec["transcript"])
    assert any("reward" in n for n in dec["notes_for_draft"])
    assert dec["scope_adherence_pct"] < 100


def test_brand_scout_switch_wins_the_debate_for_a_declining_brand(registry):
    rt, pack = _pack(registry, "BR001")
    props = propose(rt, pack, "BR001", "BR001", locked=False)
    assert props["top_alternative"] == "BR003"
    dec = run_debate(rt, pack, props, None)
    assert dec["brand_decision"]["selected"] == "BR003"
    assert any(d["by"] == "Margin advocate" and d["item"] == "brand" for d in dec["dissent"])


def test_locked_brand_is_not_re_debated(registry):
    rt, pack = _pack(registry, "BR003")
    props = propose(rt, pack, "BR003", "BR001", locked=True)
    assert all(p["position"]["brand"] == "BR003" for p in props["proposals"])


def test_scorecard_accuracy():
    assert metric_accuracy()["accuracy_pct"] == 100.0
    assert brand_resolution_accuracy()["accuracy_pct"] == 100.0
