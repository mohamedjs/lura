# Releasing

## Cut a release

1. Bump `__version__` in `src/lura/__init__.py` and `version` in `pyproject.toml`.
2. `python tests/run_all.py` must pass.
3. Tag and push: `git tag v2.0.1 && git push origin v2.0.1`.

`.github/workflows/release.yml` then:

| Job | Runner | Produces |
|---|---|---|
| linux | ubuntu-22.04 (oldest glibc we target) | `lura_<v>_amd64.deb`, `lura-<v>-linux-x86_64.tar.gz` |
| macos-arm64 | macos-15 | `Lura-<v>-macos-arm64.zip` (Lura.app) |
| macos-x86_64 | macos-15-intel | `Lura-<v>-macos-x86_64.zip` |
| windows | windows-latest | `Lura-Setup-<v>.exe` (Inno Setup), portable zip |

It publishes a GitHub Release with `SHA256SUMS.txt`, then updates the Homebrew
cask. The tag must match `__version__`, or the run stops.

## One-time setup

### Homebrew tap
Create the public repo **`mohamedjs/homebrew-lura`** (empty is fine). Make a
fine-grained token with *Contents: read & write* on that repo only, and save it in
this repo as the secret **`HOMEBREW_TAP_TOKEN`**. Users then run
`brew install --cask mohamedjs/lura/lura`.

### Pro ($5/month) through Lemon Squeezy
1. Create a store and a **subscription product** at $5/month with **licence keys
   enabled** (one activation per customer device is a sensible default; 3 lets
   people use a laptop and a desktop).
2. Set these repository **variables**: `LS_STORE_ID`, `LS_PRODUCT_ID`, `CHECKOUT_URL`
   (the product's checkout link). The release build writes them into
   `src/lura/modules/billing/store.json`.
   Without them any Lemon Squeezy key unlocks Pro, which is fine for testing and
   not for production.
3. Lemon Squeezy is the merchant of record: it charges customers, handles VAT and
   sales tax, and expires the licence key when a subscription lapses. Lura
   re-validates daily and keeps Pro for 7 days offline.

The check runs on the user's machine and can be patched out of an open-source
client. If that matters later, move connector management behind a small server
that issues signed entitlements.

### Code signing (recommended before a public launch)
- **macOS:** unsigned apps are quarantined by Gatekeeper. The cask's `postflight`
  clears the quarantine flag for Homebrew installs. For a proper launch, get an
  Apple Developer ID ($99/year), sign with `codesign --deep --options runtime`,
  and notarise with `xcrun notarytool`. Add both as a step after "Build with
  PyInstaller".
- **Windows:** unsigned installers trigger SmartScreen. An OV/EV code-signing
  certificate plus `signtool sign` on `LuraApp.exe`, `lura.exe` and the setup
  exe removes the warning over time.

## Build locally

```bash
pip install ".[all]" pyinstaller pillow
pyinstaller --noconfirm packaging/pyinstaller/lura.spec      # → dist/lura (dist/Lura.app on macOS)
packaging/linux/build-deb.sh 2.0.0                           # → dist/lura_2.0.0_amd64.deb
```

On Linux, the frozen build uses Qt for the face. Qt's X11 plugin needs
`libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-xkb1 libxkbcommon-x11-0`
(the .deb depends on them). If they are missing, Qt would abort the process, so
`overlay/qt_backend.py` checks first and runs headless instead.
