"""Connectors: MCP servers by URL + token or mcp.json (npx); three of them built in."""

from .application import ConnectorService
from .domain import BUILTIN_NAMES, Connector, builtin_connectors, parse_mcp_json, tool_name

__all__ = ["ConnectorService", "Connector", "BUILTIN_NAMES", "builtin_connectors",
           "parse_mcp_json", "tool_name"]
