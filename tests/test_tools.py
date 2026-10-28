"""Tool library: folder anatomy, manifest contracts and golden cases."""

from __future__ import annotations

import json

import pytest

from campaign_agent.tools.registry import LIBRARY_DIR

ROLE = "campaign_manager_role"


def _cases():
    for path in sorted(LIBRARY_DIR.glob("*/tests/golden.json")):
        for case in json.loads(path.read_text())["cases"]:
            yield pytest.param(path.parent.parent.name, case, id=case["name"])


def _subset(expected, actual, path="$"):
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: expected object"
        for k, v in expected.items():
            assert k in actual, f"{path}.{k} missing"
            _subset(v, actual[k], f"{path}.{k}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), f"{path}: list length differs"
        for i, (e, a) in enumerate(zip(expected, actual)):
            _subset(e, a, f"{path}[{i}]")
    else:
        assert actual == expected, f"{path}: {actual!r} != {expected!r}"


def test_every_tool_folder_has_the_anatomy(registry):
    folders = sorted(p.name for p in LIBRARY_DIR.iterdir() if p.is_dir())
    assert folders == registry.names()
    for name in folders:
        for f in ("TOOL.md", "manifest.yaml", "tests/golden.json"):
            assert (LIBRARY_DIR / name / f).exists(), f"{name}/{f}"
        assert "## When to use" in registry.get(name).doc


def test_manifest_inputs_match_binding_signature(registry):
    for name in registry.names():
        assert registry.signature_params(name) == list(registry.get(name).inputs), name


def test_every_tool_is_internal_only(registry):
    for name in registry.names():
        assert registry.get(name).access_scope == [ROLE], name


@pytest.mark.parametrize("tool,case", list(_cases()))
def test_golden(registry, tool, case):
    res = registry.call(tool, case["input"], caller="golden", allow_list={tool}, role=ROLE)
    _subset(case["expect"], res["output"])
