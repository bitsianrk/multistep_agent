"""The agent harness and the LRM client (same API setup as the PR coupon design agent)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from campaign_agent.agents.base import ROSTER
from campaign_agent.harness import AgentHarness, SkillLibrary
from campaign_agent.harness.guards import ungrounded_numbers
from campaign_agent.llm import HTTPTransport, LRMClient, LRMConfig, LRMError, ScriptedTransport, parse_tool_calls
from campaign_agent.memory import EpisodicMemory

from .conftest import drive

REQUEST = "Plan a Multi-Step for Crunchwell crisps to win back lapsed shoppers, 4 weeks from 16 Nov"
BUILTINS = {"get_run_state", "recall_memory", "remember", "read_pack_block"}


# ── LRM client ─────────────────────────────────────────────────────────────

class _LRMHandler(BaseHTTPRequestHandler):
    """Speaks the v1/lrm/ contract; fails the first `fail_first` calls with 503."""

    calls: list = []
    fail_first = 0
    reply = "TOOL: get_run_state\nARGS: {}"

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).calls.append({"path": self.path, "key": self.headers.get("X-API-Key"), "body": body})
        if len(type(self).calls) <= type(self).fail_first:
            self.send_response(503)
            self.end_headers()
            return
        out = json.dumps({"response": type(self).reply, "finish_reason": "stop",
                          "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture
def lrm_server():
    _LRMHandler.calls, _LRMHandler.fail_first = [], 0
    srv = HTTPServer(("127.0.0.1", 0), _LRMHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


def test_http_transport_uses_the_coupon_agent_api_contract(lrm_server):
    cfg = LRMConfig(employee_id="IN000TEST", api_key="test-key", model="o4-mini",
                    base_url=f"http://127.0.0.1:{lrm_server.server_port}/api/azure-openai")
    client = LRMClient(HTTPTransport(cfg, sleep=lambda s: None), model=cfg.model)
    choice = client.call_conversation([{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
                                      tools=[{"type": "function", "function": {"name": "get_run_state",
                                                                               "parameters": {"properties": {}}}}])
    call = _LRMHandler.calls[0]
    assert call["path"] == "/api/azure-openai/IN000TEST/v1/lrm/"
    assert call["key"] == "test-key"
    assert set(call["body"]) == {"system_prompt", "user_prompt", "model", "reasoning_effort", "max_tokens"}
    assert call["body"]["system_prompt"] == "sys" and "TOOL: <tool_name>" in call["body"]["user_prompt"]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["tool_calls"][0]["function"]["name"] == "get_run_state"
    assert client.stats.calls == 1 and client.stats.usage["prompt_tokens"] == 10


def test_http_transport_retries_5xx_then_succeeds(lrm_server):
    _LRMHandler.fail_first = 2
    cfg = LRMConfig(employee_id="E", api_key="K", base_url=f"http://127.0.0.1:{lrm_server.server_port}", retries=3)
    client = LRMClient(HTTPTransport(cfg, sleep=lambda s: None))
    assert client.call_simple("s", "u") is not None
    assert len(_LRMHandler.calls) == 3


def test_live_mode_requires_credentials_from_the_environment(monkeypatch):
    monkeypatch.delenv("LRM_API_KEY", raising=False)
    monkeypatch.delenv("LRM_EMPLOYEE_ID", raising=False)
    assert not LRMConfig.from_env().configured
    with pytest.raises(LRMError):
        HTTPTransport(LRMConfig.from_env())


def test_unreachable_endpoint_returns_none_for_fallback():
    cfg = LRMConfig(employee_id="E", api_key="K", base_url="http://127.0.0.1:9", retries=1, timeout_s=1)
    assert LRMClient(HTTPTransport(cfg, sleep=lambda s: None)).call_simple("s", "u") is None


def test_tool_call_parser_handles_nested_json_and_ignores_bad_blocks():
    text = ('thinking...\nTOOL: finish\nARGS: {"a": {"b": [1, {"c": "}"}]}, "d": "x"}\n'
            'TOOL: broken\nARGS: {not json}')
    calls = parse_tool_calls(text)
    assert len(calls) == 1 and json.loads(calls[0]["function"]["arguments"])["a"]["b"][1]["c"] == "}"


# ── Skills ───────────────────────────────────────────────────────────────

def test_every_skill_is_well_formed_and_within_its_agent_allow_list(registry):
    lib = SkillLibrary()
    assert len(lib.names()) == 16
    for name in lib.names():
        s = lib.get(name)
        assert s.agent in ROSTER, name
        assert s.instructions.startswith("#"), name
        for t in s.tools:
            assert t in BUILTINS or (t in registry.tools and t in ROSTER[s.agent].tools), (name, t)


# ── Harness behaviour with a scripted model ─────────────────────────────────

def _session(make_session, replies):
    return make_session(transport=ScriptedTransport(replies))


def test_scripted_model_drives_a_stage_and_its_memory_persists(make_session):
    resolution = {"brand_id": "BR001", "candidates": ["BR001"], "objective": "reactivate",
                  "window": {"start": "2026-11-16", "weeks": 4, "assumed": []}, "confidence": 1.0}
    s = _session(make_session, [
        'TOOL: ner_extract\nARGS: {"text": "Crunchwell lapsed"}',
        'TOOL: remember\nARGS: {"note": "CM wants Christmas weeks only"}',
        f"TOOL: finish\nARGS: {json.dumps(resolution)}",
    ])
    out = s.start(REQUEST)
    assert out[-1]["stage"] == "understand" and "Crunchwell" in out[-1]["text"]
    log = s.values["harness_log"][0]
    assert (log["skill"], log["source"], log["tools"]) == ("entity-resolution", "llm", ["ner_extract", "remember"])
    assert s.values["working_memory"] == [{"agent": "entity_resolution", "stage": "understand",
                                           "note": "CM wants Christmas weeks only"}]
    assert any(t.get("via") == "harness" and t["tool"] == "ner_extract" for t in s.values["trace"])


def test_invalid_output_is_sent_back_to_the_model_then_accepted(make_session):
    s = _session(make_session, [
        'TOOL: finish\nARGS: {"brand_id": "BR999", "candidates": [], "objective": "reactivate", '
        '"window": {"start": "2026-11-16", "weeks": 4, "assumed": []}, "confidence": 1}',
        'TOOL: finish\nARGS: {"brand_id": "BR001", "candidates": ["BR001"], "objective": "reactivate", '
        '"window": {"start": "2026-11-16", "weeks": 4, "assumed": []}, "confidence": 1}',
    ])
    s.start(REQUEST)
    log = s.values["harness_log"][0]
    assert log["source"] == "llm" and log["rejected"] == 1
    assert s.values["resolution"]["brand_id"] == "BR001"


def test_lrm_outage_falls_back_to_the_deterministic_policy(make_session):
    s = _session(make_session, [ConnectionError("504 Gateway Time-out")])
    out = s.start(REQUEST)
    log = s.values["harness_log"][0]
    assert (log["source"], log["reason"]) == ("fallback", "LRM unavailable")
    assert out[-1]["stage"] == "understand" and s.values["resolution"]["brand_id"] == "BR001"


def test_loop_detection_then_iteration_cap_falls_back(make_session):
    s = _session(make_session, ['TOOL: get_run_state\nARGS: {}'] * 6)
    s.start(REQUEST)
    log = s.values["harness_log"][0]
    assert log["source"] == "fallback" and log["reason"] == "max iterations reached"


def test_gateway_reads_intent_beyond_the_rules(make_session, settings):
    """'yes, but lose the heavies' - the rule parser sees approve; the model sees an edit."""
    s = make_session()
    drive(s, [REQUEST, "yes", "go", "go"])
    assert s.pending["stage"] == "discuss"
    script = ScriptedTransport(['TOOL: finish\nARGS: {"action": "edit", "target_stage": null, '
                                '"edits": [{"op": "exclude_cohort", "cohort": "heavy"}]}'])
    s.graph = __import__("campaign_agent.graph", fromlist=["build_graph"]).build_graph(
        s.settings, s.checkpointer, memory=EpisodicMemory(None), transport=script)
    s.reply("yes, but lose the heavies")
    gw = [r for r in s.values["harness_log"] if r["skill"] == "chat-gateway"][-1]
    assert gw["source"] == "llm"
    assert s.values["position_edits"] == [{"op": "exclude_cohort", "cohort": "heavy"}]


def test_gateway_cannot_invent_psgs_or_actions(registry):
    from campaign_agent.agents.harnessed import clean_edits

    state = {"brand_id": "BR001"}
    with pytest.raises(ValueError, match="not a PSG of the current brand"):
        clean_edits([{"op": "exclude_psg", "psg_id": "PSG-BR003-01"}], state, registry.ctx.store, registry.names())
    with pytest.raises(ValueError, match="unknown op"):
        clean_edits([{"op": "email_supplier", "to": "x@y.com"}], state, registry.ctx.store, registry.names())


def test_number_guard():
    src = [{"spc": 7.06, "seasonal_fit": 1.405, "shoppers": 1710}]
    assert ungrounded_numbers("SPC GBP 7.06, index 1.41, 1,710 shoppers, 6 weeks", src) == []
    assert ungrounded_numbers("SPC GBP 9.99 and 2,500 households", src) == ["9.99", "2500"]


def test_off_mode_never_calls_the_lrm(make_session, settings):
    from dataclasses import replace

    from campaign_agent.demo import SCENARIOS

    s = make_session(settings=replace(settings, lrm_mode="off"))
    drive(s, SCENARIOS["switch"])  # every stage on the fallback policies, end to end
    assert s.done
    assert all(r["source"] == "fallback" for r in s.values["harness_log"])


def test_simulated_mode_runs_every_skill_through_the_loop(make_session):
    from campaign_agent.demo import SCENARIOS

    s = make_session()
    drive(s, SCENARIOS["switch"])
    log = s.values["harness_log"]
    assert all(r["source"] == "llm" for r in log), [r for r in log if r["source"] != "llm"]
    assert {r["skill"] for r in log} == set(SkillLibrary().names())


def test_harness_rejects_unknown_mode(registry):
    with pytest.raises(ValueError):
        AgentHarness(registry, EpisodicMemory(None), mode="turbo")
