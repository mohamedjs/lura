#!/usr/bin/env bash
# Install the Linux build on a distro newer than the Ubuntu 22.04 it was built
# on and open the native dashboard there, under Xvfb with GLX (#6). The build
# fails if the window crashes, logs one of the known GPU-stack failures, or
# draws nothing. Runs as root in a container (see distro-test.yml):
#   packaging/ci/distro-test.sh <folder with lura_*_amd64.deb / lura-*-linux-x86_64.tar.gz> [shots dir]
set -uo pipefail
IN="$(cd "$1" && pwd)"
SHOTS="${2:-$PWD/distro-shots}"
mkdir -p "$SHOTS"
. /etc/os-release
echo "== $PRETTY_NAME"

# What the bundle leaves to the host (lura.spec: HOST_LIBS). On Ubuntu/Debian
# the .deb's Depends must pull all of it in by itself.
case "$ID" in
  ubuntu|debian)
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq xvfb imagemagick x11-utils >/dev/null
    apt-get install -y -qq "$(ls "$IN"/lura_*_amd64.deb | head -1)" >/dev/null || exit 1
    APP=/opt/lura
    dpkg-query -W -f '${Package} ${Version}\n' libgl1-mesa-dri libgbm1 libstdc++6 2>/dev/null ;;
  fedora)
    dnf install -y -q --setopt=install_weak_deps=False tar gzip findutils xorg-x11-server-Xvfb ImageMagick \
      xwininfo mesa-dri-drivers mesa-libGL mesa-libEGL mesa-libgbm libglvnd-glx xcb-util-cursor xcb-util-wm \
      xcb-util-keysyms xcb-util-renderutil xcb-util-image libxkbcommon-x11 fontconfig dbus-libs nss nspr \
      libXcomposite libXdamage libXrandr libXtst libxkbfile alsa-lib portaudio libwayland-client \
      libwayland-server libwayland-egl libwayland-cursor glib2 libstdc++ harfbuzz fribidi freetype \
      libX11-xcb pipewire-jack-audio-connection-kit-libs || exit 1
    rpm -q mesa-dri-drivers libstdc++ ;;
  arch|archlinux)
    pacman -Syu --noconfirm --needed xorg-server-xvfb imagemagick xorg-xwininfo mesa libglvnd nss nspr \
      libxcomposite libxdamage libxrandr libxtst libxkbfile alsa-lib portaudio jack2 xcb-util-cursor \
      xcb-util-wm xcb-util-keysyms xcb-util-renderutil xcb-util-image libxkbcommon-x11 fontconfig wayland \
      glib2 harfbuzz fribidi freetype2 dbus nss >/dev/null || exit 1
    pacman -Q mesa gcc-libs ;;
  *) echo "unsupported distro $ID"; exit 2 ;;
esac
if [ -z "${APP:-}" ]; then
  mkdir -p /opt && tar -C /opt -xzf "$(ls "$IN"/lura-*-linux-x86_64.tar.gz | head -1)" || exit 1
  APP=/opt/lura
fi
"$APP/lura" --version || exit 1

echo "== bundled libraries that do not resolve on this host"
find "$APP" -name '*.so*' -type f | while read -r f; do
  ldd "$f" 2>/dev/null | grep 'not found' | sed "s|^|  ${f#$APP/}:|"
done | sort -u | head -40

Xvfb :99 -screen 0 1440x900x24 +extension GLX >/dev/null 2>&1 &
export DISPLAY=:99 LANG=C.UTF-8
export LURA_CONFIG_DIR=/tmp/lura-test/config LURA_DATA_DIR=/tmp/lura-test/data
mkdir -p "$LURA_CONFIG_DIR"
echo '{"onboarded": true, "start_at_login": false, "briefing_on_boot": false}' > "$LURA_CONFIG_DIR/settings.json"
sleep 2

# The way the app menu opens the settings; no --disable-gpu, as on a desktop.
LOG="$SHOTS/dashboard-$ID.log"
"$APP/LuraApp" dashboard >"$LOG" 2>&1 &
PID=$!
sleep 30
FAIL=0
if ! kill -0 "$PID" 2>/dev/null; then
  wait "$PID"; echo "FAIL: 'LuraApp dashboard' exited with code $?"; FAIL=1
fi
BAD='Could not initialize GLX|DRI_Mesa|GBM: Failed|null texture|dumped core|dashboard window exited with code'
if grep -Eq "$BAD" "$LOG"; then
  echo "FAIL: known window failures in the output:"; grep -E "$BAD" "$LOG" | sort | uniq -c | head; FAIL=1
fi
# The biggest top-level window titled "Lura" must show the page, not a blank.
WID="$(xwininfo -root -tree | awk '/"Lura"/ { split($0, g, /\)  /); split(g[2], s, /[x+]/); print s[1]*s[2], $1 }' \
       | sort -rn | awk 'NR==1 {print $2}')"
import -window root "$SHOTS/desktop-$ID.png"
if [ -z "$WID" ]; then
  echo "FAIL: no window titled Lura"; FAIL=1
else
  import -window "$WID" "$SHOTS/dashboard-$ID.png"
  read -r COLORS SD < <(convert "$SHOTS/dashboard-$ID.png" -format '%k %[fx:standard_deviation]\n' info:)
  echo "window $WID: $COLORS colours, stddev $SD"
  if [ "$COLORS" -lt 64 ] || awk "BEGIN { exit !($SD < 0.02) }"; then
    echo "FAIL: the dashboard window is blank"; FAIL=1
  fi
fi
kill "$PID" 2>/dev/null
echo "== output"; cat "$LOG"
[ "$FAIL" = 0 ] && echo "PASS: the native dashboard renders on $PRETTY_NAME"
exit "$FAIL"
