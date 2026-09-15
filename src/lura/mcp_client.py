"""Model Context Protocol (MCP) client for Lura assistant."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
import threading

from google.genai import types

log = logging.getLogger(__name__)

MCP_CONFIG = Path.home() / ".config" / "lura" / "mcp.json"


def _json_type_to_gemini(json_type: str | None) -> types.Type:
    m = {"string": types.Type.STRING, "number": types.Type.NUMBER, "integer": types.Type.INTEGER,
         "boolean": types.Type.BOOLEAN, "array": types.Type.ARRAY, "object": types.Type.OBJECT}
    return m.get(str(json_type).lower(), types.Type.TYPE_UNSPECIFIED)


def _convert_schema(schema: dict | None) -> types.Schema:
    if not isinstance(schema, dict):
        return types.Schema(type=types.Type.OBJECT, properties={})
    stype = _json_type_to_gemini(schema.get("type", "object"))
    props = {k: _convert_schema(v) for k, v in schema.get("properties", {}).items() if isinstance(v, dict)}
    req, it = schema.get("required"), schema.get("items")
    items = _convert_schema(it) if stype == types.Type.ARRAY and isinstance(it, dict) else None
    return types.Schema(type=stype, properties=props if stype == types.Type.OBJECT else None,
                        required=list(req) if isinstance(req, list) else None,
                        description=schema.get("description"), items=items)


class MCPServer:
    """Manages an individual MCP server subprocess over stdio JSON-RPC."""

    def __init__(self, name: str, command: str, args: list[str] | None = None, env: dict[str, str] | None = None) -> None:
        self.name, self.command, self.args, self.env = name, command, list(args or []), dict(env or {})
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.tools: list[dict] = []
        self._req_id = 0

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _send_request_locked(self, method: str, params: dict | None = None) -> dict | None:
        if not self.alive:
            return None
        self._req_id += 1
        req_id = self._req_id
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, **({"params": params} if params is not None else {})}
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
            while self.alive:
                line = self.proc.stdout.readline()
                if not line: break
                if not line.strip(): continue
                try:
                    data = json.loads(line)
                    if isinstance(data, dict) and data.get("id") == req_id: return data
                except Exception: pass
        except Exception as exc:
            log.warning("MCPServer %s request %s failed: %s", self.name, method, exc)
        return None

    def _send_notification_locked(self, method: str, params: dict | None = None) -> None:
        if self.alive:
            try:
                self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})}) + "\n")
                self.proc.stdin.flush()
            except Exception as exc:
                log.warning("MCPServer %s notify %s failed: %s", self.name, method, exc)

    def _stop_locked(self) -> None:
        if self.proc is not None:
            try: self.proc.terminate(); self.proc.wait(timeout=2)
            except Exception:
                try: self.proc.kill()
                except Exception: pass
            self.proc = None
        self.tools = []

    def stop(self) -> None:
        with self.lock:
            self._stop_locked()

    def start(self) -> None:
        with self.lock:
            if self.alive or not self.command: return
            env = dict(os.environ)
            if self.env: env.update({k: str(v) for k, v in self.env.items()})
            try:
                self.proc = subprocess.Popen([self.command] + self.args, stdin=subprocess.PIPE,
                                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                             text=True, bufsize=1, env=env)
                if not self._send_request_locked("initialize", {
                    "protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "lura", "version": "0.1.0"},
                }):
                    self._stop_locked()
                    return
                self._send_notification_locked("notifications/initialized")
                res = self._send_request_locked("tools/list", {})
                self.tools = res.get("result", {}).get("tools", []) if res else []
                log.info("MCPServer %s ready with %d tools", self.name, len(self.tools))
            except Exception as exc:
                log.warning("Failed to start MCPServer %s: %s", self.name, exc)
                self._stop_locked()

    def call_tool(self, name: str, args: dict | None = None) -> str:
        with self.lock:
            if not self.alive:
                return f"Error: MCP server '{self.name}' is not running"
            resp = self._send_request_locked("tools/call", {"name": name, "arguments": args or {}})
            if not resp:
                return f"Error: No response from MCP server '{self.name}'"
            if "error" in resp:
                err = resp["error"]
                return f"Error from {self.name}: {err.get('message', str(err)) if isinstance(err, dict) else str(err)}"
            res = resp.get("result", {})
            if isinstance(res, dict):
                texts = [c.get("text", "") for c in res.get("content", []) if isinstance(c, dict) and c.get("type") == "text"]
                if texts:
                    out = "\n".join(texts)
                    return f"Error: {out}" if res.get("isError") else out
                return json.dumps(res)
            return str(res)


class MCPManager:
    """Manages MCP server lifecycles and routes tool calls to Gemini formats."""

    def __init__(self, config_path: Path | str = MCP_CONFIG) -> None:
        self.config_path = Path(config_path)
        self.servers: dict[str, MCPServer] = {}
        self._tool_map: dict[str, tuple[MCPServer, str]] = {}

    def load_config(self, path: Path | str | None = None) -> dict:
        if path is not None: self.config_path = Path(path)
        if not self.config_path.exists():
            log.warning("MCP config not found: %s", self.config_path)
            return {}
        try:
            lines = [l for l in self.config_path.read_text("utf-8").splitlines() if not l.strip().startswith(("//", "#"))]
            data = json.loads("\n".join(lines))
            self.servers = {n: MCPServer(n, c.get("command", ""), c.get("args", []), c.get("env", {}))
                            for n, c in data.get("mcpServers", {}).items()}
            return data
        except Exception as exc:
            log.warning("Failed to load MCP config %s: %s", self.config_path, exc)
            return {}

    def start_all(self) -> None:
        if not self.servers and self.config_path.exists():
            self.load_config(self.config_path)
        self._tool_map.clear()
        for server in self.servers.values():
            server.start()
            for t in server.tools:
                if raw_name := t.get("name", ""):
                    self._tool_map[f"mcp_{server.name}_{raw_name}"] = (server, raw_name)

    def stop_all(self) -> None:
        for server in self.servers.values():
            try: server.stop()
            except Exception as exc: log.warning("Error stopping MCPServer %s: %s", server.name, exc)
        self._tool_map.clear()

    def get_gemini_tools(self) -> list[types.FunctionDeclaration]:
        decls: list[types.FunctionDeclaration] = []
        for s in self.servers.values():
            for t in s.tools:
                if not (raw := t.get("name", "")): continue
                try:
                    decls.append(types.FunctionDeclaration(
                        name=f"mcp_{s.name}_{raw}", description=f"[{s.name}] {t.get('description', '')}".strip(),
                        parameters=_convert_schema(t.get("inputSchema")),
                    ))
                except Exception as exc:
                    log.warning("Failed converting tool %s: %s", raw, exc)
        return decls

    def get_openai_tools(self) -> list[dict]:
        """The same tools in OpenAI shape, for the OpenRouter path.

        No schema conversion here: MCP already speaks JSON Schema, which is
        exactly what the OpenAI tools field wants.
        """
        out: list[dict] = []
        for s in self.servers.values():
            for t in s.tools:
                if not (raw := t.get("name", "")):
                    continue
                schema = t.get("inputSchema")
                if not isinstance(schema, dict):
                    schema = {"type": "object", "properties": {}}
                out.append({
                    "type": "function",
                    "function": {
                        "name": f"mcp_{s.name}_{raw}",
                        "description": f"[{s.name}] {t.get('description', '')}".strip(),
                        "parameters": schema,
                    },
                })
        return out

    def call_tool(self, gemini_tool_name: str, arguments: dict | None = None) -> str:
        if gemini_tool_name in self._tool_map:
            server, raw_name = self._tool_map[gemini_tool_name]
            return server.call_tool(raw_name, arguments or {})
        for name, server in self.servers.items():
            if gemini_tool_name.startswith(prefix := f"mcp_{name}_"):
                return server.call_tool(gemini_tool_name[len(prefix):], arguments or {})
        log.warning("Unknown MCP tool: %s", gemini_tool_name)
        return f"Error: unknown MCP tool {gemini_tool_name}"
