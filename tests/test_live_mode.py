"""Live mode end to end: .env credentials, the v1/lrm/ HTTP contract, and resilience."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from campaign_agent.demo import SCENARIOS
from campaign_agent.envfile import default_lrm_mode, load_env_file
from campaign_agent.handoff import MemoryHandoff
from campaign_agent.llm.check import live_blocker
from campaign_agent.web import create_app
from campaign_agent.web import app as web_app

LRM_VARS = ("LRM_EMPLOYEE_ID", "LRM_API_KEY", "LRM_MODE", "LRM_BASE_URL", "LRM_RETRIES", "LRM_TIMEOUT_S")


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    import os

    for v in LRM_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    for v in LRM_VARS:  # load_env_file writes os.environ directly: never leak into other tests
        os.environ.pop(v, None)


class _UnhelpfulLRM(BaseHTTPRequestHandler):
    """A real HTTP endpoint on the v1/lrm/ contract whose model never produces valid output."""

    calls = 0

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.headers["X-API-Key"] == "k-test" and self.path.endswith("/IN-TEST/v1/lrm/")
        assert {"system_prompt", "user_prompt", "model"} <= set(body)
        type(self).calls += 1
        out = json.dumps({"response": "I think we should do something sensible.", "finish_reason": "stop"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def test_env_file_is_loaded_without_overriding_the_environment(clean_env, monkeypatch):
    (clean_env / ".env").write_text('# comment\nLRM_EMPLOYEE_ID="IN123"\nexport LRM_API_KEY=abc # key\nLRM_MODEL=\n')
    monkeypatch.setenv("LRM_MODE", "simulated")
    (clean_env / ".env").write_text((clean_env / ".env").read_text() + "LRM_MODE=live\n")
    assert sorted(load_env_file()) == ["LRM_API_KEY", "LRM_EMPLOYEE_ID"]
    import os

    assert os.environ["LRM_EMPLOYEE_ID"] == "IN123" and os.environ["LRM_API_KEY"] == "abc"
    assert default_lrm_mode() == "simulated"  # the environment wins over .env


def test_live_is_the_default_and_refuses_to_start_without_credentials(clean_env, capsys):
    assert default_lrm_mode() == "live"
    assert "copy .env.example to .env" in live_blocker("live")
    assert web_app.main(["--port", "0"]) == 2
    assert "Cannot start in live mode" in capsys.readouterr().out
    assert live_blocker("simulated") is None


def test_full_run_in_live_mode_over_http(clean_env, monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _UnhelpfulLRM)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    (clean_env / ".env").write_text(
        f"LRM_EMPLOYEE_ID=IN-TEST\nLRM_API_KEY=k-test\nLRM_BASE_URL=http://127.0.0.1:{srv.server_port}/api\n"
        "LRM_RETRIES=1\nLRM_TIMEOUT_S=10\n")
    load_env_file()
    from dataclasses import replace

    from campaign_agent.config import DEFAULT_SETTINGS

    settings = replace(DEFAULT_SETTINGS, lrm_mode="live", output_dir=clean_env / "runs",
                       memory_path=clean_env / "runs" / "memory.json")
    try:
        client = create_app(settings, handoff=MemoryHandoff()).test_client()
        assert client.get("/api/config").get_json()["lrm_mode"] == "live"
        first, *rest = SCENARIOS["switch"]
        view = client.post("/api/runs?wait=1", json={"message": first}).get_json()
        assert not view["error"], view["error"]
        for msg in rest:
            r = client.post(f"/api/runs/{view['run_id']}/reply?wait=1", json={"message": msg})
            assert r.status_code == 200, (msg, r.get_json(), view.get("error"), view.get("pending"))
            view = r.get_json()
            assert not view["error"], (msg, view["error"])
        # Every skill really called the endpoint, got no usable answer, and fell back - the run still completes.
        assert view["lrm_mode"] == "live" and view["done"]
        assert _UnhelpfulLRM.calls >= len(view["harness_log"]) > 30
        assert all(r["source"] == "fallback" for r in view["harness_log"])
        assert {r["reason"] for r in view["harness_log"]} == {"model did not call a tool"}
    finally:
        srv.shutdown()
