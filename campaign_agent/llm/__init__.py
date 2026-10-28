"""LRM access: the Tesco v1/lrm/ client and its offline transports."""

from .lrm_client import (
    HTTPTransport,
    LRMClient,
    LRMConfig,
    LRMError,
    LRMStats,
    build_tool_instructions,
    parse_tool_calls,
    strip_tool_blocks,
)
from .transports import ScriptedTransport, SimulatedModel

__all__ = ["HTTPTransport", "LRMClient", "LRMConfig", "LRMError", "LRMStats", "ScriptedTransport",
           "SimulatedModel", "build_tool_instructions", "parse_tool_calls", "strip_tool_blocks"]
