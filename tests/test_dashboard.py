"""The dashboard server: security checks and the main endpoints, for real."""

import _setup  # noqa: F401
import json
import socket
import sys
import urllib.error
import urllib.request


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start():
    from lura.bootstrap import build
    from lura.modules.dashboard import DashboardApi, DashboardServer

    c = build()
    server = DashboardServer(DashboardApi(c.settings, c.brain, c.voice, c.connectors, c.billing,
                                          c.bus, lambda: "ready"), free_port())
    server.start()
    return c, server


def call(server, method, path, body=None, token=True, host=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Lura-Token"] = server.token
    if host:
        headers["Host"] = host
    req = urllib.request.Request(f"http://127.0.0.1:{server.port}{path}", method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            raw = res.read()
            return res.status, (json.loads(raw) if res.headers.get("Content-Type") == "application/json" else raw), res.headers
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw), exc.headers
        except json.JSONDecodeError:
            return exc.code, raw, exc.headers


def main() -> int:
    c, server = start()
    try:
        status, page, headers = call(server, "GET", "/", token=False)
        assert status == 200 and b'<script src="app.js"' in page
        assert "script-src 'self'" in headers["Content-Security-Policy"]
        assert call(server, "GET", "/fonts/marhey-arabic.woff2", token=False)[2]["Content-Type"] == "font/woff2"
        assert call(server, "GET", "/../../keys.json", token=False)[0] == 404

        assert call(server, "GET", "/api/state", token=False)[0] == 401
        assert call(server, "GET", "/api/state", host="evil.example:80")[0] == 403

        status, state, _ = call(server, "GET", "/api/state")
        assert status == 200 and state["capabilities"]["can_talk"] is False
        assert set(state["i18n"]) == {"en", "ar"} and state["plan"]["plan"] == "free"
        assert [x["name"] for x in state["connectors"]][:3] == ["chrome", "web", "files"]

        status, res, _ = call(server, "PUT", "/api/settings", {"patch": {"language": "ar", "voice_gender": "male"}})
        assert status == 200 and res["settings"]["voice_name"] == "Puck"
        assert c.settings.current.language == "ar"
        assert call(server, "PUT", "/api/settings", {"patch": {"mode": "nope"}})[0] == 400

        status, res, _ = call(server, "POST", "/api/connectors",
                              {"kind": "url", "name": "github", "url": "https://api.githubcopilot.com/mcp/", "token": "x"})
        assert status == 402 and res["pro_required"]
        assert call(server, "PUT", "/api/keys/gemini", {"key": ""})[0] == 400
        assert call(server, "PUT", "/api/keys/nobody", {"key": "x"})[0] == 404
        assert call(server, "GET", "/api/nothing")[0] == 404
        status, res, _ = call(server, "GET", "/api/status")
        assert status == 200 and res["status"] == "ready"
    finally:
        server.stop()
    print("dashboard OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
