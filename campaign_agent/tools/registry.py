"""Tool registry: one folder per tool (TOOL.md + manifest.yaml + tests/).

Agents never touch data directly. Every data access is a registry call, and
the registry is where the controls from the design are enforced:

* allow-list  - an agent may only call the tools in its allow-list;
* access scope - the caller's role must be in the manifest's access_scope;
* typed contract - inputs and outputs are checked against the manifest;
* latency SLA - a call is abandoned after latency_sla_ms;
* budget - a run has a maximum number of tool calls;
* provenance - every result carries the sources and as-of dates it used.
"""

from __future__ import annotations

import importlib
import inspect
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from ..config import Settings
from ..data import DataStore
from .servers._common import ToolContext, ToolError

LIBRARY_DIR = Path(__file__).parent / "library"
_TYPES = {"str": str, "int": int, "float": (int, float), "bool": bool, "dict": dict, "list": list}


class ToolCallError(RuntimeError):
    """A tool call was refused or failed (allow-list, role, contract, timeout...)."""


@dataclass
class Tool:
    name: str
    version: str
    owner: str
    domain: str
    description: str
    inputs: dict[str, str]
    outputs: dict[str, str]
    binding: str
    target_binding: str
    sources: list[str]
    freshness: str
    latency_sla_ms: int
    access_scope: list[str]
    mechanics_enabled: list[str]
    doc: str
    path: Path
    fn: object = field(repr=False, default=None)

    @property
    def when_to_use(self) -> str:
        """First paragraph under '## When to use' - what the planner reads."""
        text = self.doc.split("## When to use", 1)[-1].split("##", 1)[0]
        return " ".join(text.split())


@dataclass
class RunBudget:
    max_calls: int
    used: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def spend(self) -> None:
        with self._lock:
            if self.used >= self.max_calls:
                raise ToolCallError(f"tool-call budget of {self.max_calls} exhausted for this run")
            self.used += 1


def _check_type(name: str, value, type_name: str) -> None:
    optional = type_name.endswith("?")
    base = type_name.rstrip("?")
    if value is None:
        if optional:
            return
        raise ToolCallError(f"input {name!r} is required")
    if base == "date":
        try:
            date.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise ToolCallError(f"input {name!r} must be an ISO date") from exc
        return
    expected = _TYPES.get(base)
    if expected is None:
        raise ToolCallError(f"manifest declares unknown type {type_name!r} for {name!r}")
    if base == "int" and isinstance(value, bool) or not isinstance(value, expected):
        raise ToolCallError(f"input {name!r} must be {base}, got {type(value).__name__}")


class ToolRegistry:
    def __init__(self, settings: Settings, store: DataStore, library_dir: Path = LIBRARY_DIR):
        self.settings = settings
        self.ctx = ToolContext(settings=settings, store=store)
        self.tools: dict[str, Tool] = {}
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tool")
        self._load(library_dir)

    def _load(self, library_dir: Path) -> None:
        for manifest_path in sorted(library_dir.glob("*/manifest.yaml")):
            m = yaml.safe_load(manifest_path.read_text())
            module_name, fn_name = m["binding"].split(":")
            fn = getattr(importlib.import_module(module_name), fn_name)
            doc = (manifest_path.parent / "TOOL.md").read_text()
            self.tools[m["name"]] = Tool(
                name=m["name"], version=str(m["version"]), owner=m["owner"], domain=m["domain"],
                description=" ".join(m["description"].split()), inputs=m.get("inputs") or {},
                outputs=m.get("outputs") or {}, binding=m["binding"],
                target_binding=m.get("target_binding", ""), sources=m.get("sources") or [],
                freshness=m.get("freshness", ""), latency_sla_ms=int(m["latency_sla_ms"]),
                access_scope=m.get("access_scope") or [], mechanics_enabled=m.get("mechanics_enabled") or [],
                doc=doc, path=manifest_path.parent, fn=fn,
            )

    # Introspection --------------------------------------------------------
    def names(self) -> list[str]:
        return sorted(self.tools)

    def get(self, name: str) -> Tool:
        if name not in self.tools:
            raise ToolCallError(f"tool {name!r} is not in the registry")
        return self.tools[name]

    def signature_params(self, name: str) -> list[str]:
        params = list(inspect.signature(self.get(name).fn).parameters)
        return params[1:]  # drop ctx

    # Calls ------------------------------------------------------------------
    def call(self, name: str, args: dict, *, caller: str, allow_list: set[str], role: str,
             budget: RunBudget | None = None) -> dict:
        tool = self.get(name)
        if name not in allow_list:
            raise ToolCallError(f"agent {caller!r} is not allowed to call {name!r}")
        if role not in tool.access_scope:
            raise ToolCallError(f"role {role!r} is outside the access scope of {name!r}")
        unknown = set(args) - set(tool.inputs)
        if unknown:
            raise ToolCallError(f"unknown inputs for {name!r}: {sorted(unknown)}")
        for arg, type_name in tool.inputs.items():
            _check_type(arg, args.get(arg), type_name)
        if budget is not None:
            budget.spend()

        timeout = tool.latency_sla_ms * self.settings.tool_timeout_multiplier / 1000.0
        started = time.perf_counter()
        future = self._pool.submit(tool.fn, self.ctx, **args)
        try:
            output = future.result(timeout=timeout)
        except FutureTimeout as exc:
            raise ToolCallError(f"{name!r} exceeded its {tool.latency_sla_ms} ms SLA") from exc
        except ToolError as exc:
            raise ToolCallError(f"{name!r}: {exc}") from exc
        latency_ms = round((time.perf_counter() - started) * 1000, 1)

        missing = [k for k, t in tool.outputs.items() if not str(t).endswith("?") and k not in output]
        if missing:
            raise ToolCallError(f"{name!r} broke its output contract; missing {missing}")
        provenance = [{"source": s, "as_of": self.ctx.store.source_as_of.get(s, "n/a")} for s in tool.sources]
        return {"tool": name, "version": tool.version, "output": output,
                "provenance": provenance, "latency_ms": latency_ms}


_REGISTRIES: dict[int, ToolRegistry] = {}


def get_registry(settings: Settings, store: DataStore) -> ToolRegistry:
    key = id(store)
    if key not in _REGISTRIES:
        _REGISTRIES[key] = ToolRegistry(settings, store)
    reg = _REGISTRIES[key]
    reg.settings = settings
    reg.ctx = ToolContext(settings=settings, store=store)
    return reg
