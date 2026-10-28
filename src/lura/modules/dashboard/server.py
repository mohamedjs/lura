"""The dashboard's local server: a JSON API plus the static page.

Bound to 127.0.0.1 only. Every API call must carry the per-run token from
the URL Lura opens; a page on some other site cannot read it, cannot send
the custom header without a CORS preflight we never approve, and the Host
check stops DNS-rebinding tricks. The token file is private to the user.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import secrets
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from ...shared import paths
from ...shared.errors import NeedsSetup
from ...shared.http import HttpError

log = logging.getLogger(__name__)

STATIC = Path(__file__).with_name("static")
Route = Callable[[dict[str, Any], re.Match], Any]


def url_file() -> Path:
    return paths.config_dir() / "dashboard.url"


def _stable_token() -> str:
    """One token per install, kept private: an open dashboard window keeps
    working across a restart of Lura instead of being locked out."""
    path = paths.config_dir() / "dashboard.token"
    try:
        token = path.read_text().strip()
        if len(token) >= 24:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(24)
    paths.write_private(path, token + "\n")
    return token


class ApiError(Exception):
    def __init__(self, status: int, message: str, **extra: Any):
        super().__init__(message)
        self.status, self.extra = status, extra


class DashboardServer:
    def __init__(self, api, port: int):
        self._api = api
        self.port = port
        self.token = _stable_token()
        self._routes: list[tuple[str, re.Pattern, Route]] = []
        self._httpd: ThreadingHTTPServer | None = None
        api.register(self)

    def route(self, method: str, pattern: str, handler: Route) -> None:
        self._routes.append((method, re.compile(f"^{pattern}$"), handler))

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/#t={self.token}"

    def start(self) -> None:
        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "Lura"

            def log_message(self, fmt, *args):
                log.debug("dashboard: " + fmt, *args)

            def _send(self, status: int, body: bytes, kind: str, cache: bool = False) -> None:
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
                self.send_header("Content-Security-Policy",
                                 "default-src 'self'; img-src 'self' data:; style-src 'self'; "
                                 "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status: int, payload: Any) -> None:
                self._send(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json")

            def _host_ok(self) -> bool:
                host = (self.headers.get("Host") or "").lower()
                return host in (f"127.0.0.1:{server.port}", f"localhost:{server.port}")

            def _static(self) -> None:
                rel = self.path.split("?", 1)[0].split("#", 1)[0].lstrip("/") or "index.html"
                target = (STATIC / rel).resolve()
                if STATIC.resolve() not in target.parents or not target.is_file():
                    return self._send(404, b"not found", "text/plain")
                kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                if target.suffix == ".woff2":
                    kind = "font/woff2"
                self._send(200, target.read_bytes(), kind, cache=target.suffix == ".woff2")

            def _dispatch(self, method: str) -> None:
                if not self._host_ok():
                    return self._send(403, b"forbidden host", "text/plain")
                path = self.path.split("?", 1)[0]
                if not path.startswith("/api/"):
                    return self._static() if method == "GET" else self._send(405, b"", "text/plain")
                if not secrets.compare_digest(self.headers.get("X-Lura-Token", ""), server.token):
                    return self._json(401, {"error": "bad token — reopen the dashboard from Lura"})
                length = int(self.headers.get("Content-Length") or 0)
                if length > 8_000_000:   # a zipped skill arrives base64 in JSON
                    return self._json(413, {"error": "too large"})
                try:
                    body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                except json.JSONDecodeError:
                    return self._json(400, {"error": "invalid JSON"})
                if method == "GET" and "?" in self.path:   # GET carries its arguments in the query
                    body = dict(urllib.parse.parse_qsl(self.path.split("?", 1)[1]))
                for m, pattern, handler in server._routes:
                    if m == method and (match := pattern.match(path)):
                        try:
                            return self._json(200, handler(body, match))
                        except ApiError as exc:
                            return self._json(exc.status, {"error": str(exc), **exc.extra})
                        except (ValueError, KeyError, HttpError, NeedsSetup) as exc:
                            msg = str(exc) if not isinstance(exc, KeyError) else f"unknown: {exc.args[0]}"
                            return self._json(400, {"error": msg})
                        except Exception as exc:
                            log.exception("dashboard %s %s", method, path)
                            return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
                return self._json(404, {"error": "no such endpoint"})

            def do_GET(self):
                self._dispatch("GET")

            def do_POST(self):
                self._dispatch("POST")

            def do_PUT(self):
                self._dispatch("PUT")

            def do_PATCH(self):
                self._dispatch("PATCH")

            def do_DELETE(self):
                self._dispatch("DELETE")

        self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, daemon=True, name="dashboard").start()
        paths.write_private(url_file(), self.url + "\n")
        log.info("Dashboard at http://127.0.0.1:%d", self.port)

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
        try:
            url_file().unlink()
        except FileNotFoundError:
            pass

    def open(self) -> None:
        """The dashboard in its own app window (see window.py)."""
        from .window import launch
        launch(self.url)


def running_url() -> str | None:
    """The URL of a dashboard another Lura process is serving, if any."""
    import urllib.request

    try:
        url = url_file().read_text().strip()
    except OSError:
        return None
    try:
        urllib.request.urlopen(url.split("#")[0] + "favicon.svg", timeout=1).close()
        return url
    except Exception:
        return None
