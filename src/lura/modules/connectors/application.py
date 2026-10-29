"""Managing connectors and routing tool calls to them."""

from __future__ import annotations

import logging
import re
import shutil
import threading
from typing import Any, Callable

from ...shared import events
from ...shared.events import EventBus
from ..brain import ToolSpec
from .domain import CHROME_ENDPOINT, HTTP, Connector, parse_mcp_json, tool_name
from .infrastructure.repository import McpJsonRepository

log = logging.getLogger(__name__)

#: What a Chrome connector says when the browser it was attached to is gone
#: (closed, restarted) or remote debugging is off.
_CHROME_GONE = re.compile(r"Could not connect to Chrome|ECONNREFUSED|connectOverCDP|has been closed|"
                          r"Browser was closed|DevToolsActivePort|socket hang up|Target closed", re.I)
#: Started when Chrome is closed: the server still lists its tools, and the
#: first call starts Chrome and reconnects.
_NO_CHROME = "ws://127.0.0.1:9/chrome-is-closed"


class _Running:
    def __init__(self, connector: Connector):
        self.connector = connector
        self.client = None
        self.tools: list[dict] = []
        self.status = "starting"     # starting | ready | error | stopped
        self.error = ""
        self.lock = threading.Lock()


class ConnectorService:
    """Implements the tools module's ``ConnectorTools`` port."""

    def __init__(self, repo: McpJsonRepository | None = None, bus: EventBus | None = None):
        self._repo = repo or McpJsonRepository()
        self._bus = bus
        self._connectors = self._repo.load()
        self._running: dict[str, _Running] = {}
        self._index: dict[str, tuple[str, str]] = {}   # brain-facing name → (connector, tool)
        self._lock = threading.RLock()

    # ── management ──
    def list(self) -> list[dict[str, Any]]:
        out = []
        for c in self._connectors:
            run = self._running.get(c.name)
            status = run.status if run else ("disabled" if not c.enabled else "stopped")
            out.append({**c.public(), "status": status, "error": run.error if run else "",
                        "tools": [t["name"] for t in run.tools] if run else []})
        return out

    def connected(self) -> list[dict[str, Any]]:
        """Servers that are up, with what they can do — for the system prompt,
        so the model knows a service exists before it searches for its tools."""
        out = []
        for c in self._connectors:
            run = self._running.get(c.name)
            if run and run.status == "ready" and run.tools:
                out.append({"name": c.name, "description": c.description,
                            "tools": [t["name"] for t in run.tools]})
        return out

    def get(self, name: str) -> Connector | None:
        return next((c for c in self._connectors if c.name == name), None)

    def _changed(self) -> None:
        self._repo.save(self._connectors)
        if self._bus:
            self._bus.publish(events.CONNECTORS_CHANGED)

    def add(self, connector: Connector, replace: bool = False) -> Connector:
        connector.builtin = False
        connector.validate()
        with self._lock:
            existing = self.get(connector.name)
            if existing and (existing.builtin or not replace):
                raise ValueError(f"A connector named {connector.name!r} already exists.")
            if existing:
                self._stop(existing.name)
                self._connectors.remove(existing)
            self._connectors.append(connector)
            self._changed()
        self.start_one(connector.name)
        return connector

    def add_url(self, name: str, url: str, token: str = "") -> Connector:
        return self.add(Connector(name, HTTP, url=url.strip(), token=token.strip()))

    def add_command(self, name: str, command_line: str, env: dict[str, str] | None = None) -> Connector:
        import shlex

        from ...shared.platform import IS_WINDOWS

        # POSIX rules would eat the backslashes in C:\Program Files\…
        argv = [a[1:-1] if len(a) > 1 and a[0] == a[-1] == '"' else a
                for a in shlex.split(command_line, posix=not IS_WINDOWS)]
        if not argv:
            raise ValueError("Empty command.")
        return self.add(Connector(name, command=argv[0], args=argv[1:], env=env or {}))

    def import_json(self, text: str) -> list[str]:
        parsed = parse_mcp_json(text)
        added = []
        for connector in parsed:
            self.add(connector, replace=True)
            added.append(connector.name)
        return added

    def remove(self, name: str) -> bool:
        with self._lock:
            connector = self.get(name)
            if connector is None:
                return False
            if connector.builtin:
                raise ValueError("Built-in connectors can be turned off, not removed.")
            self._stop(name)
            self._connectors.remove(connector)
            self._changed()
        return True

    def set_enabled(self, name: str, enabled: bool) -> None:
        with self._lock:
            connector = self.get(name)
            if connector is None:
                raise KeyError(name)
            connector.enabled = enabled
            self._changed()
        if enabled:
            self.start_one(name)
        else:
            self._stop(name)

    # ── lifecycle ──
    def start_all(self) -> None:
        """Start every enabled connector, each in the background:
        a first `npx -y` download must not hold up the wake word."""
        for connector in self._connectors:
            if connector.enabled:
                self.start_one(connector.name)

    def start_one(self, name: str) -> None:
        connector = self.get(name)
        if connector is None or not connector.enabled:
            return
        self._stop(name)
        run = _Running(connector)
        with self._lock:
            self._running[name] = run
        threading.Thread(target=self._start, args=(run,), daemon=True, name=f"mcp-{name}").start()

    def _start(self, run: _Running) -> None:
        from .infrastructure.clients import HttpClient, StdioClient, handshake

        c = run.connector
        try:
            if c.transport == HTTP:
                client = HttpClient(c.url, c.token, c.headers)
            else:
                if not shutil.which(c.command) and not shutil.which(c.command + ".cmd"):
                    hint = " Install Node.js (it provides npx)." if c.command in ("npx", "node") else ""
                    raise RuntimeError(f"{c.command!r} is not installed.{hint}")
                args = c.args
                if CHROME_ENDPOINT in args:
                    from .infrastructure import chrome
                    url = chrome.endpoint() or _NO_CHROME
                    args = [url if a == CHROME_ENDPOINT else a for a in args]
                client = StdioClient(c.command, args, c.env)
            client.open()
            tools = handshake(client)
        except Exception as exc:
            run.status, run.error = "error", str(exc)
            log.warning("Connector %s failed to start: %s", c.name, exc)
            return
        with self._lock:
            if self._running.get(c.name) is not run:   # stopped meanwhile
                client.close()
                return
            run.client, run.tools, run.status = client, tools, "ready"
            self._reindex()
        log.info("Connector %s ready with %d tools", c.name, len(tools))

    def _stop(self, name: str) -> None:
        with self._lock:
            run = self._running.pop(name, None)
            self._reindex()
        if run and run.client:
            try:
                run.client.close()
            except Exception as exc:
                log.debug("Closing %s: %s", name, exc)

    def stop_all(self) -> None:
        for name in list(self._running):
            self._stop(name)

    def _reindex(self) -> None:
        self._index = {tool_name(name, t["name"]): (name, t["name"])
                       for name, run in self._running.items() if run.status == "ready"
                       for t in run.tools}

    # ── the ConnectorTools port ──
    def _spec(self, connector: str, tool: dict) -> ToolSpec:
        schema = tool.get("inputSchema")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            schema = {"type": "object", "properties": {}}
        return ToolSpec(tool_name(connector, tool["name"]),
                        f"[{connector}] {tool.get('description', '')}".strip()[:1000], schema)

    def all_tools(self) -> list[ToolSpec]:
        return [self._spec(name, t) for name, run in list(self._running.items())
                if run.status == "ready" for t in run.tools]

    def search(self, query: str, limit: int = 8) -> list[ToolSpec]:
        """The connector tools that best match a plain-language request.

        Words are cut to a six-letter stem so "repositories" finds
        "repository", and a hit in the name outweighs any number of hits in
        the description — otherwise a common word like "list" drags in every
        tool whose prose happens to contain it.
        """
        stems = {w[:6] for w in re.split(r"\W+", query.lower()) if len(w) > 2}
        if not stems:
            return []
        scored = []
        for spec in self.all_tools():
            name, desc = spec.name.lower(), spec.description.lower()
            in_name = sum(1 for s in stems if s in name)
            in_desc = sum(1 for s in stems if s in desc)
            if in_name or in_desc:
                scored.append((in_name, in_desc, spec))
        scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return [spec for _, _, spec in scored[:limit]]

    def owns(self, name: str) -> bool:
        return name in self._index

    def call(self, name: str, arguments: dict[str, Any]) -> str:
        target = self._index.get(name)
        if not target:
            return f"Error: no connected tool called {name}."
        run = self._running.get(target[0])
        if not run or not run.client:
            return f"Error: connector {target[0]} is not running."
        log.info("connector %s → %s", target[0], target[1])
        result = self._call_once(run, target[1], arguments)
        if run.connector.uses_chrome and _CHROME_GONE.search(result[:600]):
            result = self._reconnect_chrome(run, target, arguments) or result
        return result[:6000]

    @staticmethod
    def _call_once(run: _Running, tool: str, arguments: dict[str, Any]) -> str:
        from .infrastructure.clients import call

        try:
            with run.lock:
                return call(run.client, tool, arguments)
        except Exception as exc:
            return f"Error from {run.connector.name}: {exc}"

    def _reconnect_chrome(self, run: _Running, target: tuple[str, str], arguments: dict[str, Any]) -> str | None:
        """Chrome was closed, restarted or not yet allowed: open the user's
        Chrome, re-attach, and try the call once more."""
        from .infrastructure import chrome

        log.info("Connector %s lost Chrome; starting / re-attaching to the user's Chrome.", target[0])
        if chrome.ensure_running() is None:
            return f"Error from {target[0]}: Chrome is not reachable. {chrome.ENABLE_HINT}"
        if CHROME_ENDPOINT in run.connector.args:
            # The address changes with every Chrome start: restart the server on the new one.
            self._stop(target[0])
            fresh = _Running(run.connector)
            with self._lock:
                self._running[target[0]] = fresh
            self._start(fresh)
            if fresh.status != "ready":
                return f"Error from {target[0]}: {fresh.error}"
            run = fresh
        return self._call_once(run, target[1], arguments)

    # ── the user's own Chrome ──
    @staticmethod
    def chrome_status() -> dict[str, Any]:
        """Whether the chrome / playwright connectors can reach the user's Chrome."""
        from .infrastructure import chrome
        return {"connected": chrome.endpoint() is not None, "hint": chrome.ENABLE_HINT}

    @staticmethod
    def open_chrome_setup() -> bool:
        """Open Chrome on the page where remote debugging is switched on."""
        from .infrastructure import chrome
        return chrome.launch("chrome://inspect/#remote-debugging")

    @property
    def config_path(self):
        return self._repo.path
