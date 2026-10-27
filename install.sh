#!/usr/bin/env bash
# Run Lura from this checkout on Linux, as a per-user background service.
# (End users install the .deb from the GitHub release instead.)
#
# Safe to re-run: it upgrades in place and leaves your keys and settings alone.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

say() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\n\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "Run this as yourself, not root. It installs into your home."

# ── system packages ─────────────────────────────────────────────────────────
# python3-venv: Ubuntu splits it out. libportaudio2: sounddevice will not
# import without it. libcairo2-dev + python3-dev: pycairo builds from source.
# python3-gi: the GTK overlay. nodejs/npm: npx runs the built-in connectors.
say "Checking system packages"
NEEDED=()
python3 -c 'import venv, ensurepip' 2>/dev/null || NEEDED+=("python3-venv")
ldconfig -p | grep -q libportaudio.so.2 || NEEDED+=("libportaudio2")
dpkg -s libcairo2-dev >/dev/null 2>&1 || NEEDED+=("libcairo2-dev" "pkg-config" "python3-dev")
python3 -c 'import gi' 2>/dev/null || NEEDED+=("python3-gi" "gir1.2-gtk-3.0")
command -v npx >/dev/null || NEEDED+=("nodejs" "npm")
if [ ${#NEEDED[@]} -gt 0 ]; then
  echo "Installing with apt (you may be asked for your password):"
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
# Editable: editing src/ and restarting the service is enough.
"$VENV/bin/pip" install --quiet -e "$HERE[anthropic,live]" pycairo
# The system PyGObject, for the GTK face.
VENV_SP="$("$VENV/bin/python" -c "import site; print(site.getsitepackages()[0])")"
if [ -d /usr/lib/python3/dist-packages/gi ]; then
  ln -sfn /usr/lib/python3/dist-packages/gi "$VENV_SP/gi"
fi
echo "Installed into $VENV"

# ── service ─────────────────────────────────────────────────────────────────
say "Installing the background service"
mkdir -p "$UNIT_DIR"
# Point the unit at wherever this checkout is.
sed -e "s|^ExecStart=.*|ExecStart=$VENV/bin/lura run --no-browser|" \
    "$HERE/packaging/linux/lura.service" > "$UNIT_DIR/lura.service"
systemctl --user daemon-reload
systemctl --user enable lura.service >/dev/null
systemctl --user restart lura.service
sleep 3
systemctl --user --no-pager --lines=5 status lura.service || true

cat <<DONE

Done. Set your keys, brain, voice and language in the dashboard:

  Dashboard:   $VENV/bin/lura dashboard
  Watch it:    journalctl --user -u lura -f
  Restart:     systemctl --user restart lura.service
  Re-check:    $VENV/bin/lura selftest
  Talk now:    $VENV/bin/lura say
DONE
