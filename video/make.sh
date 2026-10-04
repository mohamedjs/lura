#!/usr/bin/env bash
# One command: Google TTS narration → motion graphics → out/lura.mp4
#   ./make.sh                                   # voice from your Lura Gemini key
#   ./make.sh ~/Downloads/"studio lura 2.mp4"   # + your screen recording in scene 4
#   FOOTAGE_START=5 ./make.sh clip.mp4          # start the clip 5s in
#   GOOGLE_TTS_API_KEY=… ./make.sh              # use Cloud Text-to-Speech instead
set -euo pipefail
cd "$(dirname "$0")"

command -v ffmpeg >/dev/null || { echo "need ffmpeg: sudo apt install ffmpeg"; exit 1; }
command -v node   >/dev/null || { echo "need node 18+"; exit 1; }
if [ ! -d node_modules/playwright ]; then
  npm install --no-audit --no-fund
  npx playwright install chromium
fi

python3 voice.py ${TTS_ENGINE:+--engine "$TTS_ENGINE"}

args=(--out out/lura.mp4)
if [ $# -ge 1 ]; then
  [ -f "$1" ] || { echo "footage not found: $1"; exit 1; }
  args+=(--footage "$1" --footage-start "${FOOTAGE_START:-0}")
fi
node render.mjs "${args[@]}"
xdg-open out/lura.mp4 >/dev/null 2>&1 || true
