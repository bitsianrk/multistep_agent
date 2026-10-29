"""Preflight check for the live LRM (python -m campaign_agent.llm.check)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from campaign_agent.llm import LRMConfig
from campaign_agent.llm.check import check, mode_reason


def _server(status: int, body: dict):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def live_ok():
    srv = _server(200, {"response": "OK", "finish_reason": "stop"})
    yield f"http://127.0.0.1:{srv.server_port}/api"
    srv.shutdown()


@pytest.fixture
def forbidden():
    srv = _server(403, {"error": "Forbidden"})
    yield f"http://127.0.0.1:{srv.server_port}/api"
    srv.shutdown()


def test_missing_credentials_explains_how_to_set_them():
    ok, report = check(LRMConfig())
    assert not ok
    assert "credentials are missing from this terminal" in report and "LRM_API_KEY" in report


def test_live_round_trip(live_ok):
    ok, report = check(LRMConfig(employee_id="IN000TEST", api_key="secret-key", base_url=live_ok))
    assert ok and "LIVE OK" in report and "'OK'" in report
    assert "secret-key" not in report  # the key is never printed


def test_rejected_key_or_off_network(forbidden):
    ok, report = check(LRMConfig(employee_id="E", api_key="K", base_url=forbidden))
    assert not ok and "refused" in report and "Tesco network" in report


def test_unreachable_endpoint():
    ok, report = check(LRMConfig(employee_id="E", api_key="K", base_url="http://127.0.0.1:9"), timeout=2)
    assert not ok and "NOT LIVE" in report


def test_mode_reason():
    assert mode_reason(LRMConfig(), "auto") == "simulated — LRM_EMPLOYEE_ID and LRM_API_KEY not set in this terminal"
    assert mode_reason(LRMConfig(employee_id="IN46", api_key="k"), "auto").startswith("live — ")
    assert mode_reason(LRMConfig(), "off").startswith("off")
