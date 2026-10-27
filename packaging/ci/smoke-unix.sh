#!/usr/bin/env bash
# Run the built app headlessly (Linux under Xvfb) or on the macOS runner's
# desktop, and take screenshots of the face and the dashboard.
#   packaging/ci/smoke-unix.sh
set -euo pipefail
SHOTS="$PWD/screenshots"
mkdir -p "$SHOTS"
if [ "$(uname)" = "Darwin" ]; then
  APP="dist/Lura.app/Contents/MacOS"
  URLFILE="$HOME/Library/Application Support/Lura/dashboard.url"
  LOG="$HOME/Library/Application Support/Lura/data/lura.log"
  "$APP/LuraApp" run --no-browser & PID=$!
else
  APP="dist/lura"
  URLFILE="$HOME/.config/lura/dashboard.url"
  LOG="$HOME/.local/share/lura/lura.log"
  Xvfb :99 -screen 0 1440x900x24 & XPID=$!
  export DISPLAY=:99
  sleep 2
  "$APP/LuraApp" run --no-browser & PID=$!
fi
"$APP/lura" --version
for _ in $(seq 60); do [ -f "$URLFILE" ] && break; sleep 1; done
[ -f "$URLFILE" ] || { echo "dashboard never came up"; cat "$LOG" || true; exit 1; }
sleep 20
kill -0 "$PID" || { echo "LuraApp died"; cat "$LOG"; exit 1; }
URL="$(cat "$URLFILE")"
curl -s -H "X-Lura-Token: ${URL##*#t=}" "${URL%%#*}api/status" | python3 -c '
import json, sys
s = json.load(sys.stdin)
print("status:", s["status"], "can_talk:", s["capabilities"]["can_talk"])
for c in s["connectors"]:
    print("  connector", c["name"], c["status"], len(c["tools"]), "tools", c["error"])'
if [ "$(uname)" = "Darwin" ]; then
  screencapture -x "$SHOTS/macos-desktop-face.png" || echo "screencapture not permitted on this runner"
else
  import -window root "$SHOTS/linux-desktop-face.png"
  # The settings as a native Qt window, the way the menu entry opens them.
  # Xvfb has no GPU; a real desktop does not need this flag.
  QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" "$APP/LuraApp" dashboard & WPID=$!
  sleep 15
  xwininfo -root -tree | grep '"Lura"' | head -3 || echo "no Lura window found"
  import -window root "$SHOTS/linux-desktop-dashboard.png"
  kill "$WPID" || true
fi
( cd "$RUNNER_TEMP" && npm init -y >/dev/null && npm install --silent playwright@1.56.1 >/dev/null \
  && npx --yes playwright@1.56.1 install --with-deps chromium >/dev/null \
  && cp "$OLDPWD/packaging/ci/shoot_dashboard.mjs" . && node shoot_dashboard.mjs "$URLFILE" "$SHOTS" )
echo "== log"; cat "$LOG"
kill "$PID" || true
[ -n "${XPID:-}" ] && kill "$XPID" || true
