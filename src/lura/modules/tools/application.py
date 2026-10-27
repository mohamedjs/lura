"""Running tools: the built-ins here, everything else through connectors."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from ..brain import ToolSpec
from .domain import BUILTIN_TOOLS, END_SESSION, FIND_TOOLS

log = logging.getLogger(__name__)


class ConnectorTools(Protocol):
    """What tools needs from the connectors module (MCP)."""

    def search(self, query: str, limit: int = 8) -> list[ToolSpec]: ...
    def all_tools(self) -> list[ToolSpec]: ...
    def owns(self, name: str) -> bool: ...
    def call(self, name: str, arguments: dict[str, Any]) -> str: ...


class ToolSession:
    """Per conversation: which connector tools ``find_tools`` has pulled in."""

    def __init__(self) -> None:
        self.found: list[ToolSpec] = []

    def add(self, specs: list[ToolSpec]) -> None:
        known = {s.name for s in self.found}
        self.found.extend(s for s in specs if s.name not in known)


class ToolService:
    def __init__(self, connectors: ConnectorTools | None = None, language=lambda: "en"):
        self._connectors = connectors
        self._language = language

    def specs(self, session: ToolSession) -> list[ToolSpec]:
        """Built-ins plus what this conversation asked for. Rebuilt every round:
        a stale list makes the model call tools it was just told it lacks."""
        return list(BUILTIN_TOOLS) + session.found

    def realtime_specs(self) -> list[ToolSpec]:
        """Gemini Live declares everything up front; it has no second round."""
        extra = self._connectors.all_tools() if self._connectors else []
        return [t for t in BUILTIN_TOOLS if t.name != FIND_TOOLS] + extra

    def dispatch(self, name: str, args: dict[str, Any] | None,
                 session: ToolSession | None = None) -> tuple[str, bool]:
        """Run one tool. Returns (text for the model, does this end the session).

        Never raises: a tool that blows up comes back as text the model can
        talk about, not as a dead conversation.
        """
        from .infrastructure import briefing, system

        args = args or {}
        try:
            if name == "run_command":
                return system.run_command(str(args.get("command", ""))), False
            if name == "get_system_briefing":
                return briefing.as_text(briefing.gather(self._language())), False
            if name == "list_applications":
                return system.list_applications(bool(args.get("running_only", True))), False
            if name == "open_application":
                return system.open_application(str(args.get("app_name", ""))), False
            if name == "open_url":
                return system.open_url(str(args.get("url", ""))), False
            if name == "find_files":
                return system.find_files(str(args.get("query", "")), str(args.get("folder", ""))), False
            if name == END_SESSION:
                return "Session ending now. Say a brief friendly goodbye.", True
            if name == FIND_TOOLS:
                if not self._connectors:
                    return "No extra tools are connected. Try run_command or open_url.", False
                found = self._connectors.search(str(args.get("query", "")))
                if not found:
                    return "No connected tool matches that. Try run_command or open_url instead.", False
                if session is not None:
                    session.add(found)
                listing = "\n".join(f"- {t.name}: {t.description[:120]}" for t in found)
                return f"These tools are now available to call:\n{listing}", False
            if self._connectors and self._connectors.owns(name):
                return self._connectors.call(name, args), False
            return f"Error: unknown tool {name}.", False
        except Exception as exc:
            log.warning("Tool %s failed: %s", name, exc)
            return f"Error running {name}: {exc}", False

    @staticmethod
    def machine_context() -> str:
        from .infrastructure.system import machine_context
        return machine_context()

    # ── briefing ──
    def briefing_due(self) -> bool:
        from .infrastructure import briefing
        return briefing.due()

    def mark_briefing_done(self) -> None:
        from .infrastructure import briefing
        briefing.mark_done()

    def briefing_data(self) -> dict[str, str]:
        from .infrastructure import briefing
        data = briefing.gather(self._language())
        if self._connectors and self._connectors.owns("mcp_github_get_me"):
            if line := briefing.github_line(self._connectors.call):
                data["latest_commit"] = line
        return data
