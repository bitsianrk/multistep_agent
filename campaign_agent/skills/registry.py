"""Skill registry: one folder per skill (SKILL.md + manifest.yaml + tests/).

Agents never touch data directly. Every data access is a registry call, and
the registry is where the controls from the design are enforced:

* allow-list  - an agent may only call the skills in its allow-list;
* access scope - the caller's role must be in the manifest's access_scope;
* typed contract - inputs and outputs are checked against the manifest;
* latency SLA - a call is abandoned after latency_sla_ms;
* budget - a run has a maximum number of skill calls;
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
from .servers._common import SkillContext, SkillError

LIBRARY_DIR = Path(__file__).parent / "library"
_TYPES = {"str": str, "int": int, "float": (int, float), "bool": bool, "dict": dict, "list": list}


class SkillCallError(RuntimeError):
    """A skill call was refused or failed (allow-list, role, contract, timeout...)."""


@dataclass
class Skill:
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
    skill_md: str
    path: Path
    fn: object = field(repr=False, default=None)

    @property
    def when_to_use(self) -> str:
        """First paragraph under '## When to use' - what the planner reads."""
        text = self.skill_md.split("## When to use", 1)[-1].split("##", 1)[0]
        return " ".join(text.split())


@dataclass
class RunBudget:
    max_calls: int
    used: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def spend(self) -> None:
        with self._lock:
            if self.used >= self.max_calls:
                raise SkillCallError(f"skill-call budget of {self.max_calls} exhausted for this run")
            self.used += 1


def _check_type(name: str, value, type_name: str) -> None:
    optional = type_name.endswith("?")
    base = type_name.rstrip("?")
    if value is None:
        if optional:
            return
        raise SkillCallError(f"input {name!r} is required")
    if base == "date":
        try:
            date.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise SkillCallError(f"input {name!r} must be an ISO date") from exc
        return
    expected = _TYPES.get(base)
    if expected is None:
        raise SkillCallError(f"manifest declares unknown type {type_name!r} for {name!r}")
    if base == "int" and isinstance(value, bool) or not isinstance(value, expected):
        raise SkillCallError(f"input {name!r} must be {base}, got {type(value).__name__}")


class SkillRegistry:
    def __init__(self, settings: Settings, store: DataStore, library_dir: Path = LIBRARY_DIR):
        self.settings = settings
        self.ctx = SkillContext(settings=settings, store=store)
        self.skills: dict[str, Skill] = {}
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="skill")
        self._load(library_dir)

    def _load(self, library_dir: Path) -> None:
        for manifest_path in sorted(library_dir.glob("*/manifest.yaml")):
            m = yaml.safe_load(manifest_path.read_text())
            module_name, fn_name = m["binding"].split(":")
            fn = getattr(importlib.import_module(module_name), fn_name)
            skill_md = (manifest_path.parent / "SKILL.md").read_text()
            self.skills[m["name"]] = Skill(
                name=m["name"], version=str(m["version"]), owner=m["owner"], domain=m["domain"],
                description=" ".join(m["description"].split()), inputs=m.get("inputs") or {},
                outputs=m.get("outputs") or {}, binding=m["binding"],
                target_binding=m.get("target_binding", ""), sources=m.get("sources") or [],
                freshness=m.get("freshness", ""), latency_sla_ms=int(m["latency_sla_ms"]),
                access_scope=m.get("access_scope") or [], mechanics_enabled=m.get("mechanics_enabled") or [],
                skill_md=skill_md, path=manifest_path.parent, fn=fn,
            )

    # Introspection --------------------------------------------------------
    def names(self) -> list[str]:
        return sorted(self.skills)

    def get(self, name: str) -> Skill:
        if name not in self.skills:
            raise SkillCallError(f"skill {name!r} is not in the registry")
        return self.skills[name]

    def signature_params(self, name: str) -> list[str]:
        params = list(inspect.signature(self.get(name).fn).parameters)
        return params[1:]  # drop ctx

    # Calls ------------------------------------------------------------------
    def call(self, name: str, args: dict, *, caller: str, allow_list: set[str], role: str,
             budget: RunBudget | None = None) -> dict:
        skill = self.get(name)
        if name not in allow_list:
            raise SkillCallError(f"agent {caller!r} is not allowed to call {name!r}")
        if role not in skill.access_scope:
            raise SkillCallError(f"role {role!r} is outside the access scope of {name!r}")
        unknown = set(args) - set(skill.inputs)
        if unknown:
            raise SkillCallError(f"unknown inputs for {name!r}: {sorted(unknown)}")
        for arg, type_name in skill.inputs.items():
            _check_type(arg, args.get(arg), type_name)
        if budget is not None:
            budget.spend()

        timeout = skill.latency_sla_ms * self.settings.skill_timeout_multiplier / 1000.0
        started = time.perf_counter()
        future = self._pool.submit(skill.fn, self.ctx, **args)
        try:
            output = future.result(timeout=timeout)
        except FutureTimeout as exc:
            raise SkillCallError(f"{name!r} exceeded its {skill.latency_sla_ms} ms SLA") from exc
        except SkillError as exc:
            raise SkillCallError(f"{name!r}: {exc}") from exc
        latency_ms = round((time.perf_counter() - started) * 1000, 1)

        missing = [k for k, t in skill.outputs.items() if not str(t).endswith("?") and k not in output]
        if missing:
            raise SkillCallError(f"{name!r} broke its output contract; missing {missing}")
        provenance = [{"source": s, "as_of": self.ctx.store.source_as_of.get(s, "n/a")} for s in skill.sources]
        return {"skill": name, "version": skill.version, "output": output,
                "provenance": provenance, "latency_ms": latency_ms}


_REGISTRIES: dict[int, SkillRegistry] = {}


def get_registry(settings: Settings, store: DataStore) -> SkillRegistry:
    key = id(store)
    if key not in _REGISTRIES:
        _REGISTRIES[key] = SkillRegistry(settings, store)
    reg = _REGISTRIES[key]
    reg.settings = settings
    reg.ctx = SkillContext(settings=settings, store=store)
    return reg
