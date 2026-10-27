#!/usr/bin/env bash
# Package the PyInstaller folder (dist/lura) as a .deb for Debian/Ubuntu.
#   packaging/linux/build-deb.sh 2.0.0 [amd64]
set -euo pipefail
VERSION="${1:?version}"
ARCH="${2:-amd64}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DIST="$ROOT/dist/lura"
[ -x "$DIST/lura" ] || { echo "Build first: pyinstaller packaging/pyinstaller/lura.spec" >&2; exit 1; }

PKG="$(mktemp -d)/lura_${VERSION}_${ARCH}"
mkdir -p "$PKG/DEBIAN" "$PKG/opt" "$PKG/usr/bin" "$PKG/usr/share/applications" \
         "$PKG/usr/share/icons/hicolor/512x512/apps" "$PKG/usr/lib/systemd/user" \
         "$PKG/usr/share/doc/lura"
cp -a "$DIST" "$PKG/opt/lura"
ln -s /opt/lura/lura "$PKG/usr/bin/lura"
install -m644 "$HERE/lura.desktop" "$PKG/usr/share/applications/lura.desktop"
install -m644 "$HERE/lura.service" "$PKG/usr/lib/systemd/user/lura.service"
install -m644 "$ROOT/packaging/icons/lura.png" "$PKG/usr/share/icons/hicolor/512x512/apps/lura.png"
install -m644 "$ROOT/src/lura/modules/dashboard/static/fonts/OFL.txt" "$PKG/usr/share/doc/lura/fonts-OFL.txt"

SIZE="$(du -sk "$PKG" | cut -f1)"
cat > "$PKG/DEBIAN/control" <<CONTROL
Package: lura
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Installed-Size: $SIZE
Maintainer: Mohamed <mohamed@tripklik.com>
Homepage: https://github.com/mohamedjs/lura
Depends: libportaudio2, libxcb-cursor0, libxcb-icccm4, libxcb-keysyms1, libxcb-xkb1, libxcb-shape0, libxcb-randr0, libxcb-render-util0, libxcb-image0, libxkbcommon-x11-0, libegl1, libgl1, libfontconfig1, libdbus-1-3
Recommends: nodejs, npm, ffmpeg
Suggests: google-chrome-stable | chromium
Description: Voice-first desktop assistant
 Say "Lura", talk, and she answers out loud in Arabic or English. Any of
 Gemini, OpenAI, Claude or OpenRouter can be the brain; Gemini 3.8 speaks.
 She opens apps, runs safe commands, drives Chrome and uses MCP connectors.
CONTROL

cat > "$PKG/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q /usr/share/icons/hicolor || true
echo "Lura is installed. Open it from your applications menu, or run: lura"
POSTINST
cat > "$PKG/DEBIAN/prerm" <<'PRERM'
#!/bin/sh
# Per-user services are stopped by each user's session; nothing system-wide to stop.
exit 0
PRERM
chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/prerm"

mkdir -p "$ROOT/dist"
dpkg-deb --build --root-owner-group "$PKG" "$ROOT/dist/lura_${VERSION}_${ARCH}.deb"
echo "Built dist/lura_${VERSION}_${ARCH}.deb"
