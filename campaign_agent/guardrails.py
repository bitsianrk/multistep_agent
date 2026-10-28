"""Guardrails that sit outside individual tools.

Tool-level controls (role scope, minimum cell size, typed contracts) live in
the registry and the bindings. This module covers what is shown in chat and
who may start a run.
"""

from __future__ import annotations

import copy

ALLOWED_ROLES = {"campaign_manager_role"}
# Kantar fields outside the licence scope for display in agent outputs.
KANTAR_STRIP = {"panel_sample_households"}


class AccessDenied(PermissionError):
    pass


def authorize(user: dict) -> None:
    """Entra ID role check (stubbed): internal campaign managers only."""
    if user.get("role") not in ALLOWED_ROLES:
        raise AccessDenied(f"user {user.get('id')!r} lacks the campaign_manager_role")
    if user.get("external", False):
        raise AccessDenied("external parties cannot use the Campaign Planning Agent")


def display_safe(obj):
    """Deep copy with out-of-licence Kantar fields removed, for chat display."""
    obj = copy.deepcopy(obj)

    def strip(o):
        if isinstance(o, dict):
            for k in list(o):
                if k in KANTAR_STRIP:
                    del o[k]
                else:
                    strip(o[k])
        elif isinstance(o, list):
            for x in o:
                strip(x)

    strip(obj)
    return obj
