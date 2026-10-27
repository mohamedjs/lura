"""Connectors on disk: mcp.json, in the format every MCP client reads.

The built-ins are not written there — they come from code, so an upgrade can
fix their command — but disabling one is remembered under ``lura``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ....shared import paths
from ..domain import Connector, builtin_connectors, from_entry

log = logging.getLogger(__name__)


class McpJsonRepository:
    def __init__(self, path: Path | None = None):
        self.path = path or paths.config_dir() / "mcp.json"

    def _raw(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            text = "\n".join(l for l in self.path.read_text("utf-8").splitlines()
                             if not l.strip().startswith(("//", "#")))
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Ignoring unreadable %s: %s", self.path, exc)
            return {}

    def load(self) -> list[Connector]:
        raw = self._raw()
        disabled = set((raw.get("lura") or {}).get("disabledBuiltins") or [])
        builtins = builtin_connectors()
        for c in builtins:
            c.enabled = c.name not in disabled
        names = {c.name for c in builtins}
        custom = []
        for name, entry in (raw.get("mcpServers") or {}).items():
            try:
                connector = from_entry(name, entry)
            except ValueError as exc:
                log.warning("Skipping connector %s: %s", name, exc)
                continue
            if connector.name not in names:
                names.add(connector.name)
                custom.append(connector)
        return builtins + custom

    def save(self, connectors: list[Connector]) -> None:
        raw = self._raw()
        raw["mcpServers"] = {c.name: c.to_mcp_json() for c in connectors if not c.builtin}
        raw["lura"] = {**(raw.get("lura") or {}),
                       "disabledBuiltins": sorted(c.name for c in connectors if c.builtin and not c.enabled)}
        # Tokens live in here, so it is private like keys.json.
        paths.write_private(self.path, json.dumps(raw, indent=2, ensure_ascii=False) + "\n")
