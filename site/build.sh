#!/usr/bin/env bash
# Assemble the landing page into _site/ (what GitHub Pages serves).
#   site/build.sh            uses video/out/intro-{en,ar}.{mp4,jpg} if rendered
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/_site"
rm -rf "$OUT"
mkdir -p "$OUT/assets/shots" "$OUT/fonts"
cp "$ROOT"/site/index.html "$ROOT"/site/site.css "$ROOT"/site/site.js "$OUT/"
cp "$ROOT"/src/lura/modules/dashboard/static/fonts/{caveat-latin,marhey-arabic}.woff2 \
   "$ROOT"/src/lura/modules/dashboard/static/fonts/OFL.txt "$OUT/fonts/"
cp "$ROOT"/src/lura/modules/dashboard/static/favicon.svg "$ROOT"/packaging/icons/lura.png \
   "$ROOT"/site/mac-not-opened.png "$OUT/assets/"
cp "$ROOT"/video/face.js "$ROOT"/video/face_mesh.js "$OUT/assets/"
cp "$ROOT"/docs/screenshots/windows/{windows-desktop-dashboard,dashboard-en-light,dashboard-ar-dark}.png \
   "$ROOT"/docs/screenshots/macos-arm64/macos-desktop-face.png "$OUT/assets/shots/"
for lang in en ar; do
  for ext in mp4 jpg; do
    f="$ROOT/video/out/intro-$lang.$ext"
    [ -f "$f" ] && cp "$f" "$OUT/assets/" || echo "warning: no $f (run: cd video && node intro.mjs --lang $lang)"
  done
done
# The latest release, so download buttons work without calling the GitHub API.
if command -v curl >/dev/null; then
  auth=()
  [ -n "${GITHUB_TOKEN:-}" ] && auth=(-H "Authorization: Bearer $GITHUB_TOKEN")
  curl -fsSL "${auth[@]}" "https://api.github.com/repos/mohamedjs/lura/releases/latest" -o "$OUT/release.json" \
    || echo "warning: no release.json; the page will ask the GitHub API"
fi
touch "$OUT/.nojekyll"
echo "built $OUT ($(du -sh "$OUT" | cut -f1))"
