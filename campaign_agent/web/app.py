"""Web UI for the Campaign Planning Agent (standard library only).

    python -m campaign_agent.web                     # http://127.0.0.1:8000
    python -m campaign_agent.web --db runs/state.db  # runs survive a server restart
    python -m campaign_agent.web --lrm-mode off      # deterministic agents only

The page is the chat window from the design: a message starts a run, every
stage stops at a checkpoint card, and the side panels show the plan,
Diagnostic Pack, discussion, Agreed Brief, harness log and memory. It drives
the same CampaignSession as the CLI.

The server is built on http.server, so it needs no packages beyond the agent's
own (corporate package mirrors often do not carry web frameworks). It binds
to localhost by default: internal use only.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..config import DEFAULT_SETTINGS, Settings
from ..guardrails import AccessDenied
from ..harness import resolve_mode
from ..llm import LRMConfig
from ..memory import EpisodicMemory
from ..session import CampaignSession
from .view import run_summary, run_view

STATIC = Path(__file__).parent / "static"
MAX_MESSAGE = 2000
MAX_BODY = 64 * 1024
RUN_ID = r"([A-Za-z0-9_-]{1,64})"
EXAMPLES = [
    "Plan a Multi-Step for Crunchwell crisps to win back lapsed shoppers, 4 weeks from 16 Nov",
    "Set up something for Golden this Christmas",
    "Grow Meadow Fresh yoghurt frequency, 4 weeks from 2 Mar",
]


class RunStore:
    """Sessions by run_id. With a persistent checkpointer, unknown ids are re-opened from disk."""

    def __init__(self, settings: Settings, checkpointer=None, transport=None, handoff=None):
        self.settings = settings
        self.checkpointer = checkpointer
        self.transport = transport
        self.handoff = handoff
        self.memory = EpisodicMemory(settings.memory_path)
        self.sessions: dict[str, CampaignSession] = {}
        self.locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    def new(self, user: dict) -> CampaignSession:
        s = self._make(user, None)
        with self._lock:
            self.sessions[s.run_id] = s
            self.locks[s.run_id] = threading.Lock()
        return s

    def get(self, run_id: str, user: dict) -> CampaignSession | None:
        with self._lock:
            if run_id in self.sessions:
                return self.sessions[run_id]
        if self.checkpointer is None:
            return None
        s = self._make(user, run_id)
        if not s.values:
            return None
        with self._lock:
            self.sessions[run_id] = s
            self.locks[run_id] = threading.Lock()
        return s

    def all(self) -> list[CampaignSession]:
        with self._lock:
            return list(self.sessions.values())

    def lock(self, run_id: str) -> threading.Lock:
        return self.locks[run_id]

    def _make(self, user, run_id):
        kw = {"memory": self.memory, "transport": self.transport}
        if self.checkpointer is not None:
            kw["checkpointer"] = self.checkpointer
        if self.handoff is not None:
            kw["handoff"] = self.handoff
        return CampaignSession(user=user, settings=self.settings, run_id=run_id, **kw)


class Response:
    def __init__(self, status: int, body: bytes, content_type: str = "application/json", headers=None):
        self.status = status
        self.body = body
        self.headers = {"Content-Type": content_type, **(headers or {})}

    @classmethod
    def json(cls, obj, status: int = 200, headers=None) -> "Response":
        return cls(status, json.dumps(obj, default=str).encode(), "application/json; charset=utf-8", headers)


class WebApp:
    """Routes requests to the run store. `handle` is transport-free, so tests call it directly."""

    def __init__(self, settings: Settings | None = None, checkpointer=None, transport=None, handoff=None,
                 user: dict | None = None):
        self.settings = settings or DEFAULT_SETTINGS
        self.transport = transport
        self.runs = RunStore(self.settings, checkpointer, transport, handoff)
        self.default_user = user or {"id": "cm.local", "name": "Campaign Manager", "role": "campaign_manager_role"}
        self.routes = [
            ("GET", r"/", self.index),
            ("GET", r"/static/([A-Za-z0-9_.-]+)", self.static),
            ("GET", r"/api/config", self.config),
            ("GET", r"/api/runs", self.list_runs),
            ("POST", r"/api/runs", self.start_run),
            ("GET", rf"/api/runs/{RUN_ID}", self.get_run),
            ("POST", rf"/api/runs/{RUN_ID}/reply", self.reply),
            ("GET", rf"/api/runs/{RUN_ID}/brief\.json", self.brief),
            ("GET", r"/api/memory", self.memory),
        ]

    # Dispatch -------------------------------------------------------------
    def handle(self, method: str, path: str, body: bytes = b"", headers: dict | None = None) -> Response:
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        path = path.split("?", 1)[0]
        allowed = False
        for m, pattern, fn in self.routes:
            match = re.fullmatch(pattern, path)
            if not match:
                continue
            allowed = True
            if m != method:
                continue
            req = {"body": body, "headers": headers}
            try:
                return fn(req, *match.groups())
            except AccessDenied as exc:
                return Response.json({"error": str(exc)}, 403)
        if allowed:
            return Response.json({"error": "method not allowed"}, 405)
        return Response.json({"error": "not found"}, 404)

    def _user(self, req) -> dict:
        # Entra ID in production; locally the user id can be set with X-User-Id.
        uid = req["headers"].get("x-user-id", self.default_user["id"])
        return {**self.default_user, "id": re.sub(r"[^\w.@-]", "", uid)[:64] or self.default_user["id"]}

    @staticmethod
    def _message(req) -> str:
        try:
            data = json.loads(req["body"] or b"{}")
        except ValueError:
            data = {}
        return str((data if isinstance(data, dict) else {}).get("message", "")).strip()[:MAX_MESSAGE]

    # Pages -----------------------------------------------------------------
    def index(self, req):
        return self.static(req, "index.html")

    def static(self, req, name):
        f = (STATIC / name).resolve()
        if f.parent != STATIC.resolve() or not f.is_file():
            return Response.json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        return Response(200, f.read_bytes(), ctype, {"Cache-Control": "no-cache"})

    # API -------------------------------------------------------------------
    def config(self, req):
        s = self.settings
        mode = "live" if self.transport is not None and s.lrm_mode != "off" else \
            resolve_mode(s.lrm_mode, LRMConfig.from_env())
        return Response.json({"lrm_mode": mode, "user": self._user(req)["id"], "autonomy": s.autonomy_level,
                              "examples": EXAMPLES})

    def list_runs(self, req):
        return Response.json([run_summary(s) for s in reversed(self.runs.all())])

    def start_run(self, req):
        text = self._message(req)
        if not text:
            return Response.json({"error": "message is required"}, 400)
        s = self.runs.new(self._user(req))
        with self.runs.lock(s.run_id):
            s.start(text)
        return Response.json(run_view(s), 201)

    def get_run(self, req, run_id):
        s = self.runs.get(run_id, self._user(req))
        if s is None:
            return Response.json({"error": "unknown run"}, 404)
        return Response.json(run_view(s))

    def reply(self, req, run_id):
        s = self.runs.get(run_id, self._user(req))
        if s is None:
            return Response.json({"error": "unknown run"}, 404)
        text = self._message(req)
        if not text:
            return Response.json({"error": "message is required"}, 400)
        lock = self.runs.lock(run_id)
        if not lock.acquire(blocking=False):
            return Response.json({"error": "this run is still working on the previous message"}, 409)
        try:
            if not s.awaiting:
                return Response.json({"error": "run is not waiting at a checkpoint"}, 409)
            s.reply(text)
        finally:
            lock.release()
        return Response.json(run_view(s))

    def brief(self, req, run_id):
        s = self.runs.get(run_id, self._user(req))
        if s is None or not s.values.get("brief"):
            return Response.json({"error": "no brief yet"}, 404)
        return Response(200, json.dumps(s.values["brief"], indent=2).encode(), "application/json",
                        {"Content-Disposition": f"attachment; filename=agreed_brief_{run_id}.json"})

    def memory(self, req):
        return Response.json(self.runs.memory.data)

    # Serving ---------------------------------------------------------------
    def make_server(self, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
        app = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "CampaignAgentUI"

            def _go(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                if n > MAX_BODY:
                    res = Response.json({"error": "request too large"}, 413)
                else:
                    body = self.rfile.read(n) if n else b""
                    try:
                        res = app.handle(method, self.path, body, dict(self.headers))
                    except Exception as exc:  # keep the server up; show the error in the UI
                        res = Response.json({"error": f"{type(exc).__name__}: {exc}"}, 500)
                self.send_response(res.status)
                for k, v in res.headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(res.body)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(res.body)

            def do_GET(self):  # noqa: N802
                self._go("GET")

            def do_POST(self):  # noqa: N802
                self._go("POST")

            def log_message(self, fmt, *args):
                if not self.path.startswith("/static/"):
                    print(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

        return ThreadingHTTPServer((host, port), Handler)


def create_app(settings: Settings | None = None, checkpointer=None, transport=None, handoff=None,
               user: dict | None = None) -> WebApp:
    return WebApp(settings, checkpointer, transport, handoff, user)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--db", type=Path, help="SQLite file for persistent checkpoints")
    ap.add_argument("--lrm-mode", choices=["auto", "live", "simulated", "off"], default=DEFAULT_SETTINGS.lrm_mode)
    ap.add_argument("--autonomy", choices=["L1", "L2"], default="L1")
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_SETTINGS.output_dir)
    args = ap.parse_args(argv)
    settings = replace(DEFAULT_SETTINGS, lrm_mode=args.lrm_mode, autonomy_level=args.autonomy,
                       output_dir=args.output_dir, memory_path=args.output_dir / "memory.json")
    checkpointer = None
    if args.db:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver

        args.db.parent.mkdir(parents=True, exist_ok=True)
        checkpointer = SqliteSaver(sqlite3.connect(str(args.db), check_same_thread=False))
    server = create_app(settings, checkpointer).make_server(args.host, args.port)
    print(f"Campaign Planning Agent UI on http://{args.host}:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0
