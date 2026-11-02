#!/usr/bin/env bash
# Build dist/gppcc.app (macOS menu-bar app).
#   ./build_macos.sh            build only
#   ./build_macos.sh --install  build, copy to /Applications, reset its stale Accessibility grant
# Optional: GPPCC_SIGN_IDENTITY="Developer ID Application: ..." to sign with a real certificate.
set -euo pipefail
cd "$(dirname "$0")"

if [ "$(uname)" != "Darwin" ]; then
  echo "error: build_macos.sh must run on macOS." >&2; exit 1
fi

# venv + runtime deps (same logic as run.sh)
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --quiet -r requirements.txt pyinstaller

echo "→ icon"
mkdir -p build/gppcc.iconset
.venv/bin/python packaging/make_icon.py build/icon-1024.png
for s in 16 32 128 256 512; do
  sips -z $s $s build/icon-1024.png --out build/gppcc.iconset/icon_${s}x${s}.png >/dev/null
  sips -z $((s*2)) $((s*2)) build/icon-1024.png --out build/gppcc.iconset/icon_${s}x${s}@2x.png >/dev/null
done
iconutil -c icns build/gppcc.iconset -o build/gppcc.icns

echo "→ pyinstaller"
.venv/bin/pyinstaller --noconfirm --clean --log-level WARN \
  --distpath dist --workpath build/pyinstaller packaging/gppcc.spec

echo "✓ built dist/gppcc.app"

if [ "${1:-}" = "--install" ]; then
  # Quit a running copy first so the bundle can be replaced.
  pkill -f "/Applications/gppcc.app/Contents/MacOS/gppcc" 2>/dev/null || true
  rm -rf /Applications/gppcc.app
  ditto dist/gppcc.app /Applications/gppcc.app
  # Every rebuild has a new ad-hoc signature, which silently invalidates the old
  # Accessibility grant. Clear it so macOS asks again cleanly.
  tccutil reset Accessibility com.gppcc.app >/dev/null 2>&1 || true
  echo "✓ installed /Applications/gppcc.app — open it and allow it under"
  echo "  System Settings → Privacy & Security → Accessibility"
fi
