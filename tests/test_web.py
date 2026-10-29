"""Web UI: Flask API over the same run, plus the live activity stream."""

from __future__ import annotations

import json
import time

import pytest

from campaign_agent.demo import SCENARIOS
from campaign_agent.handoff import MemoryHandoff
from campaign_agent.web import create_app
from campaign_agent.web.app import DIST

FIRST, *REST = SCENARIOS["switch"]


@pytest.fixture
def client(settings):
    return create_app(settings, handoff=MemoryHandoff()).test_client()


def _wait_idle(client, run_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        v = client.get(f"/api/runs/{run_id}").get_json()
        if not v["busy"]:
            return v
        time.sleep(0.05)
    raise AssertionError("run stayed busy")


def test_react_build_is_served(client):
    assert (DIST / "index.html").exists(), "run: cd campaign_agent/web/frontend && npm run build"
    html = client.get("/").data.decode()
    assert '<div id="root">' in html
    asset = html.split('src="')[1].split('"')[0]
    assert asset.startswith("/assets/") and client.get(asset).status_code == 200


def test_config_reports_lrm_mode(client):
    cfg = client.get("/api/config").get_json()
    assert cfg["lrm_mode"] == "simulated" and cfg["examples"]


def test_full_run_over_http(client):
    r = client.post("/api/runs?wait=1", json={"message": FIRST})
    assert r.status_code == 201
    view = r.get_json()
    run_id = view["run_id"]
    assert view["pending"]["stage"] == "understand" and not view["busy"]
    assert [s["status"] for s in view["stages"]] == ["current", "todo", "todo", "todo", "todo"]
    for msg in REST:
        view = client.post(f"/api/runs/{run_id}/reply?wait=1", json={"message": msg}).get_json()
        if view["stage"] == "diagnose" and view["pack"]:
            assert view["pack"]["key_numbers"] and view["pack"]["findings"]
    assert view["done"] and view["brief"]["json"]["brand_id"] == "BR003"
    assert all(s["status"] == "done" for s in view["stages"])
    assert len(view["harness_log"]) > 30
    brief = client.get(f"/api/runs/{run_id}/brief.json")
    assert brief.status_code == 200 and "attachment" in brief.headers["Content-Disposition"]
    assert any(x["run_id"] == run_id for x in client.get("/api/runs").get_json())


def test_background_run_and_live_activity_stream(client):
    r = client.post("/api/runs", json={"message": FIRST})
    assert r.status_code == 202
    run_id = r.get_json()["run_id"]
    view = _wait_idle(client, run_id)
    assert view["pending"]["stage"] == "understand" and view["last_event"] > 0
    assert client.post(f"/api/runs/{run_id}/reply", json={"message": "yes"}).status_code == 202
    _wait_idle(client, run_id)

    resp = client.get(f"/api/runs/{run_id}/events?after=0", buffered=False)
    assert resp.mimetype == "text/event-stream"
    events, chunks = [], resp.response
    for chunk in chunks:
        for line in chunk.decode().splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
        if any(e["type"] == "idle" for e in events) and sum(e["type"] == "idle" for e in events) >= 2:
            break
    resp.close()
    types = [e["type"] for e in events]
    assert types[0] == "busy" and "idle" in types
    assert {"skill_start", "finish"} <= set(types)
    assert any(e["type"] == "skill_start" and e["skill"] == "entity-resolution" for e in events)
    assert any(e["type"] == "skill_start" and e["skill"] == "campaign-planner" for e in events)
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)


def test_reply_while_busy_is_refused(client, settings):
    app = create_app(settings, handoff=MemoryHandoff())
    c = app.test_client()
    run_id = c.post("/api/runs?wait=1", json={"message": FIRST}).get_json()["run_id"]
    run = app.config["RUNS"].runs[run_id]
    run.lock.acquire()
    run.busy = True
    try:
        assert c.post(f"/api/runs/{run_id}/reply", json={"message": "yes"}).status_code == 409
    finally:
        run.busy = False
        run.lock.release()


def test_errors(client):
    assert client.post("/api/runs", json={"message": "  "}).status_code == 400
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/runs/nope/events").status_code == 404
    run_id = client.post("/api/runs?wait=1", json={"message": FIRST}).get_json()["run_id"]
    assert client.post(f"/api/runs/{run_id}/reply", json={}).status_code == 400
    assert client.get(f"/api/runs/{run_id}/brief.json").status_code == 404
    assert client.get("/assets/../app.py").status_code == 404


def test_view_never_leaks_licence_restricted_kantar_fields(client):
    run_id = client.post("/api/runs?wait=1", json={"message": FIRST}).get_json()["run_id"]
    for msg in ("yes", "go"):
        r = client.post(f"/api/runs/{run_id}/reply?wait=1", json={"message": msg})
    assert b"panel_sample_households" not in r.data
