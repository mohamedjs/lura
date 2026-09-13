#!/usr/bin/env bash
# Installs the Gemini voice assistant as a per-user background service.
#
# Safe to re-run: it upgrades in place and leaves your key and settings alone.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/lura"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
VOSK_URL="https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"

say() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\n\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "Run this as yourself, not root. It installs into your home."

# ── system packages ─────────────────────────────────────────────────────────
# python3-venv because Ubuntu splits it out and PEP 668 leaves no way around a
# venv; libportaudio2 because sounddevice will not import without it.
say "Checking system packages"
NEEDED=()
python3 -c 'import venv, ensurepip' 2>/dev/null || NEEDED+=("python3-venv")
ldconfig -p | grep -q libportaudio.so.2 || NEEDED+=("libportaudio2")
command -v unzip >/dev/null || NEEDED+=("unzip")
command -v curl  >/dev/null || NEEDED+=("curl")

if [ ${#NEEDED[@]} -gt 0 ]; then
  echo "These are missing and need apt (you will be asked for your password):"
  printf '  - %s\n' "${NEEDED[@]}"
  sudo apt-get update
  sudo apt-get install -y "${NEEDED[@]}"
else
  echo "All present."
fi

# ── python environment ──────────────────────────────────────────────────────
say "Building the Python environment"
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$HERE"
echo "Installed into $VENV"

# ── wake-word model ─────────────────────────────────────────────────────────
say "Wake-word model (offline, no account needed)"
if [ -d "$DATA/vosk-model" ]; then
  echo "Already present."
else
  mkdir -p "$DATA"
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  echo "Downloading ~40MB…"
  curl -fL --progress-bar "$VOSK_URL" -o "$tmp/model.zip"
  unzip -q "$tmp/model.zip" -d "$tmp"
  # The zip has one top-level directory whose name carries the version.
  mv "$(find "$tmp" -maxdepth 1 -type d -name 'vosk-model-*' | head -1)" "$DATA/vosk-model"
  echo "Installed to $DATA/vosk-model"
fi

# ── service ─────────────────────────────────────────────────────────────────
say "Installing the background service"
mkdir -p "$UNIT_DIR"
# Point the unit at wherever this checkout actually is, so the service works
# whether it lives in /var/www/html/lura or anywhere else.
sed -e "s|^ExecStart=.*|ExecStart=$VENV/bin/lura run|" \
    -e "s|^Documentation=.*|Documentation=file:$HERE/README.md|" \
    "$HERE/lura.service" > "$UNIT_DIR/lura.service"
systemctl --user daemon-reload
systemctl --user enable lura.service >/dev/null
echo "Enabled — it will start with your desktop session."

# ── key ─────────────────────────────────────────────────────────────────────
KEYS_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/lura/keys.json"
mkdir -p "$(dirname "$KEYS_FILE")"; chmod 700 "$(dirname "$KEYS_FILE")"

if [ -s "$KEYS_FILE" ] && grep -q '"' "$KEYS_FILE" 2>/dev/null; then
  say "A key is already stored — leaving it alone"
else
  say "One-time sign-in"
  echo "Which service should answer you?"
  echo "  1) Gemini      — best voice, replies while you speak, you can cut in"
  echo "  2) OpenRouter  — any of 445 models, but one turn at a time"
  read -rp "Choice [1]: " choice
  if [ "${choice:-1}" = "2" ]; then
    "$VENV/bin/lura" config provider openrouter >/dev/null
    "$VENV/bin/lura" login --provider openrouter || die "No key stored."
  else
    "$VENV/bin/lura" login --provider gemini || die "No key stored."
  fi
fi

# ── done ────────────────────────────────────────────────────────────────────
say "Checking everything works"
"$VENV/bin/lura" selftest || {
  echo
  echo "Something above failed. Fix it, then re-run:"
  echo "  $VENV/bin/lura selftest"
  exit 1
}

say "Starting"
systemctl --user restart lura.service
sleep 2
systemctl --user --no-pager --lines=5 status lura.service || true

cat <<DONE

Done. Say "Gemini" and it will beep, listen, and answer out loud.

  Watch it:    journalctl --user -u lura -f
  Stop it:     systemctl --user stop lura
  Start it:    systemctl --user start lura
  Re-check:    $VENV/bin/lura selftest
  Talk now:    $VENV/bin/lura say
  Settings:    $VENV/bin/lura config
  Switch:      $VENV/bin/lura config provider openrouter
  Pick model:  $VENV/bin/lura models claude

So it keeps running after you close the terminal (and between logins):
  sudo loginctl enable-linger $USER
DONE
