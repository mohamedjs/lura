"""MCP clients for the two transports: stdio (a local process) and Streamable HTTP."""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from typing import Any

from ....shared.platform import IS_WINDOWS

log = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "lura", "version": "2"}
#: The first `npx -y` of a server downloads it; that can take a while.
START_TIMEOUT = 120
CALL_TIMEOUT = 60


class MCPError(RuntimeError):
    pass


def _result_text(result: Any) -> str:
    """A tools/call result → the text the model reads."""
    if not isinstance(result, dict):
        return str(result)
    parts = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            parts.append(item.get("text", ""))
        elif item.get("type") == "resource":
            parts.append(str((item.get("resource") or {}).get("text", "")))
        elif item.get("type") == "image":
            parts.append("[image returned]")
    if not parts and "structuredContent" in result:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
    text = "\n".join(p for p in parts if p) or json.dumps(result, ensure_ascii=False)
    return f"Error: {text}" if result.get("isError") else text


class StdioClient:
    """JSON-RPC over a child process's stdin/stdout, one line per message.

    A reader thread owns stdout, so a server that stops answering costs a
    timeout, not a hung assistant.
    """

    def __init__(self, command: str, args: list[str], env: dict[str, str]):
        self._argv = [self._resolve(command), *args]
        self._env = {**os.environ, **env}
        self._proc: subprocess.Popen | None = None
        self._pending: dict[int, queue.Queue] = {}
        self._lock = threading.Lock()
        self._next = 0

    @staticmethod
    def _resolve(command: str) -> str:
        # Windows ships npx as npx.cmd; Popen without a shell will not find it.
        return shutil.which(command) or (shutil.which(command + ".cmd") if IS_WINDOWS else None) or command

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def open(self) -> None:
        flags = 0x08000000 if IS_WINDOWS else 0   # CREATE_NO_WINDOW: no console flashing up
        self._proc = subprocess.Popen(self._argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, env=self._env, creationflags=flags,
                                      text=True, encoding="utf-8", errors="replace", bufsize=1)
        threading.Thread(target=self._read, daemon=True, name="mcp-stdio").start()

    def _read(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue   # servers print banners; ignore anything that is not JSON
            if isinstance(message, dict) and (waiter := self._pending.get(message.get("id"))):
                waiter.put(message)
        for waiter in list(self._pending.values()):
            waiter.put({"error": {"message": "server exited"}})

    def request(self, method: str, params: dict | None = None, timeout: float = CALL_TIMEOUT) -> Any:
        if not self.alive:
            raise MCPError("server is not running")
        with self._lock:
            self._next += 1
            rid = self._next
            waiter: queue.Queue = queue.Queue(maxsize=1)
            self._pending[rid] = waiter
            message = {"jsonrpc": "2.0", "id": rid, "method": method}
            if params is not None:
                message["params"] = params
            assert self._proc and self._proc.stdin
            self._proc.stdin.write(json.dumps(message) + "\n")
            self._proc.stdin.flush()
        try:
            reply = waiter.get(timeout=timeout)
        except queue.Empty:
            raise MCPError(f"{method} timed out after {timeout:.0f}s") from None
        finally:
            self._pending.pop(rid, None)
        if "error" in reply:
            err = reply["error"]
            raise MCPError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
        return reply.get("result")

    def notify(self, method: str, params: dict | None = None) -> None:
        if self.alive and self._proc and self._proc.stdin:
            message = {"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})}
            with self._lock:
                self._proc.stdin.write(json.dumps(message) + "\n")
                self._proc.stdin.flush()

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            self._proc.wait(timeout=3)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None


class HttpClient:
    """MCP Streamable HTTP: JSON-RPC POSTs whose replies come back as JSON or SSE."""

    def __init__(self, url: str, token: str = "", headers: dict[str, str] | None = None):
        self._url = url
        self._headers = dict(headers or {})
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        self._session: str | None = None
        self._next = 0
        self._lock = threading.Lock()
        self.alive = False

    def open(self) -> None:
        self.alive = True

    def _post(self, message: dict, timeout: float) -> Any:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "User-Agent": "lura-desktop", **self._headers}
        if self._session:
            headers["Mcp-Session-Id"] = self._session
            headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
        request = urllib.request.Request(self._url, json.dumps(message).encode(), headers, method="POST")
        try:
            response = urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            if exc.code in (401, 403):
                raise MCPError(f"the server refused the token (HTTP {exc.code}). {detail}") from None
            raise MCPError(f"HTTP {exc.code}: {detail}") from None
        except urllib.error.URLError as exc:
            raise MCPError(f"cannot reach {self._url}: {exc.reason}") from None
        with response:
            if sid := response.headers.get("Mcp-Session-Id"):
                self._session = sid
            if "id" not in message:   # a notification: 202, no body
                return None
            kind = response.headers.get("Content-Type", "")
            if "text/event-stream" in kind:
                data: list[str] = []
                for raw in response:
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if line.startswith("data:"):
                        data.append(line[5:].lstrip())
                    elif not line and data:
                        reply = json.loads("\n".join(data))
                        data = []
                        if isinstance(reply, dict) and reply.get("id") == message["id"]:
                            return reply
                raise MCPError("the stream ended without a reply")
            body = response.read()
            return json.loads(body) if body else None

    def request(self, method: str, params: dict | None = None, timeout: float = CALL_TIMEOUT) -> Any:
        with self._lock:
            self._next += 1
            message = {"jsonrpc": "2.0", "id": self._next, "method": method}
        if params is not None:
            message["params"] = params
        reply = self._post(message, timeout) or {}
        if "error" in reply:
            err = reply["error"]
            raise MCPError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
        return reply.get("result")

    def notify(self, method: str, params: dict | None = None) -> None:
        self._post({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})}, 15)

    def close(self) -> None:
        if self._session:
            try:
                request = urllib.request.Request(self._url, headers={"Mcp-Session-Id": self._session,
                                                                     **self._headers}, method="DELETE")
                urllib.request.urlopen(request, timeout=5).close()
            except Exception:
                pass
        self.alive = False


def handshake(client) -> list[dict]:
    """initialize → initialized → tools/list (all pages). Returns the tools."""
    client.request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                  "clientInfo": CLIENT_INFO}, timeout=START_TIMEOUT)
    client.notify("notifications/initialized")
    tools: list[dict] = []
    cursor = None
    for _ in range(20):
        result = client.request("tools/list", {"cursor": cursor} if cursor else {}) or {}
        tools += [t for t in result.get("tools") or [] if isinstance(t, dict) and t.get("name")]
        cursor = result.get("nextCursor")
        if not cursor:
            break
    return tools


def call(client, tool: str, arguments: dict) -> str:
    return _result_text(client.request("tools/call", {"name": tool, "arguments": arguments}))
