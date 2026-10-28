"""Web UI API: the same run, driven over HTTP."""

from __future__ import annotations

import pytest

from campaign_agent.demo import SCENARIOS
from campaign_agent.handoff import MemoryHandoff
from campaign_agent.web import create_app


@pytest.fixture
def client(settings):
    app = create_app(settings, handoff=MemoryHandoff())
    return app.test_client()


def test_index_and_static_assets_are_served(client):
    assert b"Multi-Step Campaign Planner" in client.get("/").data
    for f in ("app.js", "styles.css"):
        assert client.get(f"/static/{f}").status_code == 200


def test_config_reports_lrm_mode(client):
    cfg = client.get("/api/config").get_json()
    assert cfg["lrm_mode"] == "simulated" and cfg["examples"]


def test_full_run_over_http(client):
    first, *rest = SCENARIOS["switch"]
    r = client.post("/api/runs", json={"message": first})
    assert r.status_code == 201
    view = r.get_json()
    run_id = view["run_id"]
    assert view["pending"]["stage"] == "understand"
    assert [s["status"] for s in view["stages"]] == ["current", "todo", "todo", "todo", "todo"]
    for msg in rest:
        view = client.post(f"/api/runs/{run_id}/reply", json={"message": msg}).get_json()
        if msg == "go" and view["stage"] == "diagnose":
            assert view["pack"]["key_numbers"] and view["pack"]["findings"]
    assert view["done"] and view["brief"]["json"]["brand_id"] == "BR003"
    assert all(s["status"] == "done" for s in view["stages"])
    assert view["discussion"]["decision"]["dissent"] is not None
    assert len(view["harness_log"]) > 30
    brief = client.get(f"/api/runs/{run_id}/brief.json")
    assert brief.status_code == 200 and "attachment" in brief.headers["Content-Disposition"]
    assert any(x["run_id"] == run_id for x in client.get("/api/runs").get_json())


def test_errors(client):
    assert client.post("/api/runs", json={"message": "  "}).status_code == 400
    assert client.get("/api/runs/nope").status_code == 404
    run_id = client.post("/api/runs", json={"message": SCENARIOS["switch"][0]}).get_json()["run_id"]
    assert client.post(f"/api/runs/{run_id}/reply", json={}).status_code == 400
    assert client.get(f"/api/runs/{run_id}/brief.json").status_code == 404


def test_view_never_leaks_licence_restricted_kantar_fields(client):
    run_id = client.post("/api/runs", json={"message": SCENARIOS["switch"][0]}).get_json()["run_id"]
    for msg in ("yes", "go"):
        r = client.post(f"/api/runs/{run_id}/reply", json={"message": msg})
    assert b"panel_sample_households" not in r.data
