"""The dashboard server: security checks and the main endpoints, for real."""

import _setup  # noqa: F401
import json
import socket
import sys
import urllib.error
import urllib.parse
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
                                          c.bus, lambda: "ready", c.memory, c.skills), free_port())
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
        # Wake word: refused if the offline model cannot hear it; follows a rename when it can.
        c.voice.wake_word_known = lambda word: word != "zzqx"
        assert call(server, "PUT", "/api/settings", {"patch": {"wake_word": "zzqx"}})[0] == 400
        status, res, _ = call(server, "PUT", "/api/settings", {"patch": {"assistant_name": "Nour"}})
        assert status == 200 and res["settings"]["wake_word"] == "nour" and res["notes"], res
        c.voice.wake_word_known = lambda word: False if word == "zzqx" else (word == "nour")
        status, res, _ = call(server, "PUT", "/api/settings", {"patch": {"assistant_name": "Zzqx"}})
        assert res["settings"]["wake_word"] == "nour" and "nour" in res["notes"][0], "kept, and said so"

        status, res, _ = call(server, "POST", "/api/connectors",
                              {"kind": "url", "name": "github", "url": "https://api.githubcopilot.com/mcp/", "token": "x"})
        assert status == 402 and res["pro_required"]
        assert call(server, "PUT", "/api/keys/gemini", {"key": ""})[0] == 400
        assert call(server, "PUT", "/api/keys/nobody", {"key": "x"})[0] == 404
        assert call(server, "GET", "/api/nothing")[0] == 404
        status, res, _ = call(server, "GET", "/api/status")
        assert status == 200 and res["status"] == "ready"
        assert res["restart_pending"] is True, "the settings changed above should offer a restart"
        asked = []
        from lura.shared import events
        c.bus.subscribe(events.RESTART_REQUESTED, lambda p: asked.append(1))
        assert call(server, "POST", "/api/restart", {})[0] == 200 and asked == [1]
        # The token survives a restart, so an open dashboard window keeps working.
        from lura.modules.dashboard.server import _stable_token
        assert _stable_token() == server.token
        # Memory & history
        status, res, _ = call(server, "PUT", "/api/memory", {"facts": "- Likes tea\n"})
        assert status == 200 and res["facts"] == "- Likes tea\n"
        assert call(server, "PUT", "/api/memory", {"facts": 3})[0] == 400
        c.memory.call("remember", {"fact": "Has a cat"})   # Lura, while the page was open
        status, res, _ = call(server, "PUT", "/api/memory", {"facts": "- Likes coffee\n", "base": "- Likes tea\n"})
        assert status == 409 and "Has a cat" in res["facts"] and "Has a cat" in c.memory.facts()
        conv = c.memory.start()
        c.memory.add(conv, "user", "عايز شاي")
        c.memory.end(conv)
        status, res, _ = call(server, "GET", "/api/history?q=" + urllib.parse.quote("شاي"))
        assert status == 200 and [x["id"] for x in res["conversations"]] == [conv]
        assert call(server, "GET", "/api/history?q=coffee")[1]["conversations"] == []
        assert call(server, "GET", f"/api/history/{conv}")[1]["messages"][0]["text"] == "عايز شاي"
        assert call(server, "POST", "/api/memory/reset", {})[0] == 200
        assert call(server, "DELETE", f"/api/history/{conv}")[0] == 200
        assert call(server, "DELETE", f"/api/history/{conv}")[0] == 404

        # Skills
        status, res, _ = call(server, "POST", "/api/skills",
                              {"kind": "paste", "text": "---\nname: tea\ndescription: Make tea\n---\nBoil water."})
        assert status == 200 and res["added"] == "tea" and res["skills"][0]["enabled"]
        assert call(server, "POST", "/api/skills", {"kind": "paste", "text": "no front matter"})[0] == 400
        assert call(server, "PATCH", "/api/skills/tea", {"enabled": False})[1]["skills"][0]["enabled"] is False
        assert call(server, "DELETE", "/api/skills/tea")[0] == 200
        assert call(server, "DELETE", "/api/skills/tea")[0] == 404

        status, res, _ = call(server, "GET", "/api/catalog")
        assert status == 200 and res["catalog"] == {}, "no keys → no providers in the picker"
    finally:
        server.stop()
    window_fallback()
    print("dashboard OK")
    return 0


def window_fallback():
    """A window process that crashes is reaped and the page opens in a browser (#6)."""
    import time
    from lura.modules.dashboard import window

    opened = []
    real = window._self_command, window.app_window_or_browser
    window.app_window_or_browser = opened.append
    try:
        window._self_command = lambda: [sys.executable, "-c", "import os, sys; sys.argv[1:] and os.abort()"]
        assert window.run("http://x/crash") is False and opened == ["http://x/crash"]
        window.launch("http://x/daemon")
        for _ in range(100):
            if opened[-1] == "http://x/daemon":
                break
            time.sleep(0.05)
        assert opened[-1] == "http://x/daemon", "the daemon's crashed window falls back too"
        assert window._child.poll() is not None, "and is reaped, not left <defunct>"
        window._self_command = lambda: [sys.executable, "-c", "pass"]
        assert window.run("http://x/closed") is True and opened[-1] != "http://x/closed"
    finally:
        window._self_command, window.app_window_or_browser = real


if __name__ == "__main__":
    sys.exit(main())
