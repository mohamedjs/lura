"""Connectors: MCP servers by URL + token or mcp.json (npx); three built-in, the rest Pro."""

from .application import ConnectorService
from .domain import BUILTIN_NAMES, Connector, builtin_connectors, may_add, parse_mcp_json, tool_name

__all__ = ["ConnectorService", "Connector", "BUILTIN_NAMES", "builtin_connectors", "may_add",
           "parse_mcp_json", "tool_name"]
