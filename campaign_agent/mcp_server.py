"""Expose registry skills as MCP tools, one server per domain.

    python -m campaign_agent.mcp_server --domain transactions
    python -m campaign_agent.mcp_server            # every domain in one server

The same skills then serve the LangGraph agents, a notebook, or an analyst
assistant. Every MCP call still goes through the registry, so the role check,
typed contract, latency SLA and provenance apply. The caller's role comes
from CAMPAIGN_AGENT_ROLE (Entra ID in production).
"""

from __future__ import annotations

import argparse
import inspect
import os

from .config import DEFAULT_SETTINGS
from .data import get_store
from .guardrails import display_safe
from .skills.registry import get_registry


def _server_class():
    try:  # mcp >= 2
        from mcp.server.mcpserver import MCPServer

        return MCPServer
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP

        return FastMCP


def build_server(domain: str | None = None):
    registry = get_registry(DEFAULT_SETTINGS, get_store(DEFAULT_SETTINGS))
    names = [n for n in registry.names() if domain in (None, registry.get(n).domain)]
    server = _server_class()(f"campaign-skills-{domain or 'all'}")
    role = os.environ.get("CAMPAIGN_AGENT_ROLE", "campaign_manager_role")

    for name in names:
        skill = registry.get(name)
        sig = inspect.signature(skill.fn)
        params = list(sig.parameters.values())[1:]  # drop ctx

        def make(n=name):
            def tool(**kwargs):
                res = registry.call(n, {k: v for k, v in kwargs.items() if v is not None},
                                    caller="mcp_client", allow_list=set(names), role=role)
                return display_safe({"output": res["output"], "provenance": res["provenance"],
                                     "version": res["version"]})
            return tool

        fn = make()
        fn.__name__ = name
        fn.__doc__ = skill.description
        fn.__signature__ = sig.replace(parameters=params)
        fn.__annotations__ = {p.name: p.annotation for p in params if p.annotation is not inspect._empty}
        server.add_tool(fn, name=name, description=skill.description)
    return server, names


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--domain", choices=["transactions", "commercial", "market", "ops", "taxonomy",
                                         "planning", "chat"])
    ap.add_argument("--list", action="store_true", help="print the tools and exit")
    args = ap.parse_args(argv)
    server, names = build_server(args.domain)
    if args.list:
        print("\n".join(names))
        return 0
    server.run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
