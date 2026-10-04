#!/usr/bin/env bash
# One command: narration → motion graphics → out/lura.mp4
#   TTS_GRADIO_URL=https://xxxx.gradio.live ./make.sh   # voice from your Colab TTS (MSA)
#     + TTS_RECORD_REF=1                         # record the voice to copy from your mic
#     + TTS_REF_WAV=clip.wav TTS_REF_TEXT='…'    # or copy the voice in clip.wav
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

PY=python3
if [ -n "${TTS_GRADIO_URL:-}" ]; then
  # own venv: system pip refuses installs on recent Debian/Ubuntu
  [ -x .venv/bin/python ] || python3 -m venv .venv
  .venv/bin/python -c "import gradio_client" 2>/dev/null || .venv/bin/pip install -q gradio_client
  PY=.venv/bin/python
fi
vargs=()
[ -n "${TTS_ENGINE:-}" ]     && vargs+=(--engine "$TTS_ENGINE")
[ -n "${TTS_REF_WAV:-}" ]    && vargs+=(--ref-wav "$TTS_REF_WAV")
[ -n "${TTS_REF_TEXT:-}" ]   && vargs+=(--ref-text "$TTS_REF_TEXT")
[ -n "${TTS_RECORD_REF:-}" ] && vargs+=(--record-ref)
# TTS_ARGS keeps shell quoting, e.g. TTS_ARGS='--gradio-arg "seed_input=42"'
[ -n "${TTS_ARGS:-}" ] && eval "vargs+=($TTS_ARGS)"
"$PY" voice.py "${vargs[@]}"

args=(--out out/lura.mp4)
if [ $# -ge 1 ]; then
  [ -f "$1" ] || { echo "footage not found: $1"; exit 1; }
  args+=(--footage "$1" --footage-start "${FOOTAGE_START:-0}")
fi
node render.mjs "${args[@]}"
xdg-open out/lura.mp4 >/dev/null 2>&1 || true
