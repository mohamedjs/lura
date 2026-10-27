"""Connectors: MCP servers Lura can use as tools.

Two ways in, and deliberately no third:

* **URL + token** — a remote MCP server over Streamable HTTP, with the
  user's own token if it needs one.
* **mcp.json** — the same JSON every MCP client uses (``mcpServers`` with
  ``command``/``args``/``env``, usually ``npx``), pasted or imported.

There is no OAuth "sign in with…" connector flow. That needs a hosted
redirect endpoint and per-vendor app registrations; a token the user pastes
needs neither.

Three connectors ship with the app and are free. Adding any other is Pro.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

STDIO, HTTP = "stdio", "http"
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


@dataclass
class Connector:
    name: str
    transport: str = STDIO
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    token: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    builtin: bool = False
    description: str = ""

    def validate(self) -> None:
        if not _NAME.match(self.name):
            raise ValueError("A connector name is 1–32 lowercase letters, digits, - or _.")
        if self.transport == HTTP:
            if not self.url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
                raise ValueError("A connector URL must be https:// (or http://localhost).")
        elif self.transport == STDIO:
            if not self.command.strip():
                raise ValueError("A command connector needs a command, e.g. npx.")
        else:
            raise ValueError(f"Unknown transport {self.transport!r}.")

    def public(self) -> dict[str, Any]:
        """For the dashboard: everything but secrets."""
        out = asdict(self)
        out["token"] = "••••" if self.token else ""
        out["env"] = {k: "••••" for k in self.env}
        out["headers"] = {k: "••••" for k in self.headers}
        return out

    def to_mcp_json(self) -> dict[str, Any]:
        """The entry as it is written to mcp.json, in the shape every client reads."""
        if self.transport == HTTP:
            headers = dict(self.headers)
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            entry: dict[str, Any] = {"type": "http", "url": self.url}
            if headers:
                entry["headers"] = headers
        else:
            entry = {"command": self.command, "args": list(self.args)}
            if self.env:
                entry["env"] = dict(self.env)
        if not self.enabled:
            entry["disabled"] = True
        if self.description:
            entry["description"] = self.description
        return entry


def builtin_connectors(home: Path | None = None) -> list[Connector]:
    """Installed with the app, always available, always free."""
    home = home or Path.home()
    return [
        Connector("chrome", command="npx", args=["-y", "chrome-devtools-mcp@latest"], builtin=True,
                  description="Drive Google Chrome: open tabs, pages and videos, click, read, screenshot."),
        Connector("web", command="npx", args=["-y", "mcp-fetch-server"], builtin=True,
                  description="Fetch live web pages, news and YouTube transcripts as text."),
        Connector("files", command="npx",
                  args=["-y", "@modelcontextprotocol/server-filesystem", str(home)], builtin=True,
                  description="Read and search files in your home folder."),
    ]


BUILTIN_NAMES = frozenset(c.name for c in builtin_connectors(Path("~")))


def may_add(connector: Connector, is_pro: bool) -> bool:
    """The plan rule, in one place: the built-ins are free; the rest is Pro."""
    return connector.builtin or is_pro


def from_entry(name: str, entry: dict[str, Any]) -> Connector:
    """One ``mcpServers`` entry → a Connector. Accepts Claude Desktop, VS Code
    (``servers`` with ``type``) and Cursor shapes."""
    if not isinstance(entry, dict):
        raise ValueError(f"{name}: expected an object")
    clean = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-")[:32] or "server"
    enabled = not entry.get("disabled", False)
    url = entry.get("url") or entry.get("serverUrl") or ""
    if url:
        headers = {str(k): str(v) for k, v in (entry.get("headers") or {}).items()}
        token = ""
        auth = headers.pop("Authorization", "") or headers.pop("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        elif auth:
            headers["Authorization"] = auth
        return Connector(clean, HTTP, url=url, token=token, headers=headers, enabled=enabled,
                         description=str(entry.get("description", "")))
    command = entry.get("command", "")
    args = entry.get("args") or []
    if isinstance(command, list):   # some configs put the whole argv in command
        command, args = (command[0] if command else ""), command[1:] + list(args)
    return Connector(clean, STDIO, command=str(command), args=[str(a) for a in args],
                     env={str(k): str(v) for k, v in (entry.get("env") or {}).items()},
                     enabled=enabled, description=str(entry.get("description", "")))


def parse_mcp_json(text: str) -> list[Connector]:
    """Everything a pasted mcp.json describes. Raises ValueError with a reason."""
    lines = [line for line in text.splitlines() if not line.strip().startswith("//")]
    try:
        data = json.loads("\n".join(lines))
    except json.JSONDecodeError as exc:
        raise ValueError(f"That is not valid JSON: {exc.msg} (line {exc.lineno})") from None
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object.")
    servers = data.get("mcpServers") or data.get("servers") or (data.get("mcp") or {}).get("servers")
    if servers is None:
        if "command" in data or "url" in data:   # a single bare entry
            servers = {data.get("name", "server"): data}
        else:
            raise ValueError('No "mcpServers" in that JSON.')
    connectors = [from_entry(n, e) for n, e in servers.items()]
    for c in connectors:
        c.validate()
    return connectors


def tool_name(connector: str, tool: str) -> str:
    """The name the brain sees. Every provider allows [A-Za-z0-9_-]{1,64}."""
    raw = f"mcp_{connector.replace('-', '_')}_{re.sub(r'[^A-Za-z0-9_-]', '_', tool)}"
    return raw[:64]
