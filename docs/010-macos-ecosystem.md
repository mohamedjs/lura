# 010 — Lura on macOS: her own Node.js for connectors, Lura Air on a Mac
Status: Implemented (2026-11-21, Gemini 4 Neon high, 2 rounds; reviewed by Claude)     Builds on: docs/ARCHITECTURE.md, docs/002 (computer backend port), docs/003 (camera), docs/009 (Air engine)

## 1. Goal

Release 2.5.0 = Lura Air (hand control, already on this branch, Linux/X11 only) + two things a
Mac needs:

1. **Lura ships her own Node.js runtime for MCP connectors.** On macOS a GUI app does not get
   the login shell's PATH, so `npx` installed through nvm/Homebrew is not found or is broken.
   Lura downloads the official Node.js LTS build once into `<data>/node/current/` (all OSes),
   verifies it against nodejs.org's `SHASUMS256.txt`, and runs every connector whose command
   is `npx`, `npm` or `node` (built-in or user-added) with that runtime, its own npm cache and
   a PATH that puts her `node` first. System Node is used only until her own is installed.
2. **Lura Air works on macOS.** A `MacBackend` for the computer port (CoreGraphics through
   `ctypes` for mouse/keyboard/displays/windows, AppleScript System Events for window moves,
   `osascript`/`pbcopy`/`pmset` for system controls), camera capture through AVFoundation,
   the `NSCameraUsageDescription` Info.plist key (without it macOS kills the app on camera
   access), and the two privacy permissions shown + requestable from the dashboard's Hands tab:
   Camera and Accessibility (posting mouse/keyboard events and moving windows need it).

## 2. Stack and packages

No new Python packages. Python 3.12 stdlib only (`ctypes`, `urllib.request`, `hashlib`,
`tarfile`, `zipfile`, `json`, `platform`). Node.js: official binaries from
`https://nodejs.org/dist/`, major **22** (LTS), newest 22.x LTS from `index.json`, fallback
pinned `v22.12.0`. Archives: `node-<v>-darwin-arm64.tar.xz`, `node-<v>-darwin-x64.tar.xz`,
`node-<v>-linux-x64.tar.xz`, `node-<v>-linux-arm64.tar.xz`, `node-<v>-win-x64.zip`,
`node-<v>-win-arm64.zip`. macOS frameworks loaded with `ctypes.cdll.LoadLibrary` by full path:
`/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics`,
`/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation`,
`/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices`,
`/System/Library/Frameworks/AVFoundation.framework/AVFoundation`, `/usr/lib/libobjc.A.dylib`.

## 3. Structure

```
src/lura/modules/connectors/infrastructure/node.py        NEW  Lura's own Node.js runtime
src/lura/modules/connectors/infrastructure/clients.py     CHANGE StdioClient uses node.resolve/env
src/lura/modules/connectors/application.py                CHANGE install check + ensure_runtime/runtime_status
src/lura/modules/computer/infrastructure/quartz.py        NEW  ctypes bindings: CoreGraphics, CoreFoundation, AX
src/lura/modules/computer/infrastructure/macos_pure.py    NEW  pure helpers for the Mac backend (testable anywhere)
src/lura/modules/computer/infrastructure/macos.py         NEW  MacBackend
src/lura/modules/computer/infrastructure/__init__.py      CHANGE IS_MAC → MacBackend
src/lura/modules/computer/application.py                  CHANGE backend name in capabilities; request_permissions()
src/lura/modules/computer/__init__.py                     CHANGE export accessibility_trusted
src/lura/modules/air/infrastructure/macos_camera.py       NEW  AVFoundation camera authorization status
src/lura/modules/air/infrastructure/cameras.py            CHANGE AVFoundation backend + permission wait on macOS
src/lura/modules/air/__init__.py                          CHANGE export camera_authorization
src/lura/shared/macos_privacy.py                          NEW  open a System Settings privacy pane
src/lura/modules/dashboard/api.py                         CHANGE POST /api/air/permission
src/lura/modules/dashboard/static/index.html              CHANGE #air-perms container
src/lura/modules/dashboard/static/cards.js                CHANGE render the permission rows
src/lura/modules/dashboard/static/app.css                 CHANGE .air-perm row style
src/lura/shared/i18n/en.json, ar.json                     CHANGE air.perm.* keys
src/lura/app.py                                           CHANGE runtime thread, air permissions, `lura mcp runtime`
packaging/pyinstaller/lura.spec                           CHANGE NSCameraUsageDescription
.github/workflows/release.yml                             CHANGE runtime check step, mac probe step, release body
tests/test_node_runtime.py                                NEW
tests/test_macos.py                                       NEW
tests/run_all.py                                          CHANGE add the two new checks
README.md                                                 CHANGE Node no longer required; Mac permissions
```

Nothing else changes. Do not touch `docs/`, version numbers, CLAUDE.md, AGENTS.md.

## 4. File responsibilities

- **connectors/infrastructure/node.py** — find/install/use Lura's Node.js. Exports
  `NODE_MAJOR`, `FALLBACK_VERSION`, `home`, `platform_triplet`, `pick_version`,
  `parse_shasums`, `node_exe`, `npm_script`, `ready`, `ensure`, `resolve`, `env`, `status`,
  `is_node_command`. Imports: stdlib, `....shared.paths`, `....shared.platform`. Must not
  import any other module, must never raise out of `ensure`/`status`/`resolve`/`env`.
- **connectors/infrastructure/clients.py** — only `StdioClient.__init__` changes (§8).
- **connectors/application.py** — `_start` install check (§8) and two new public methods
  `ensure_runtime()`, `runtime_status()`.
- **computer/infrastructure/quartz.py** — the only file that touches macOS C APIs for the
  computer module. Loads frameworks lazily on first use (importing the file on Linux/Windows
  must work and do nothing). Exports `available`, `cursor`, `post_mouse`, `post_scroll`,
  `post_key`, `displays`, `window_list`, `ax_trusted`.
- **computer/infrastructure/macos_pure.py** — pure functions/constants, no ctypes, no
  subprocess: key-combo translation, `osascript` output parsing, CG window dicts → `Window`,
  AppleScript text builders.
- **computer/infrastructure/macos.py** — `MacBackend`, implements every method of the
  `Backend` protocol in `computer/application.py` using `quartz`, `macos_pure` and
  `runner.run`.
- **air/infrastructure/macos_camera.py** — `authorization() -> str` via the Objective-C
  runtime; returns `"authorized"` on non-macOS. Lazy loading like quartz.
- **shared/macos_privacy.py** — `open_settings(pane) -> bool`. Stdlib only.
- **dashboard** — show the two permissions on macOS, a button to open each pane. The API calls
  an injected callable; it imports nothing from air/computer.
- **app.py** — wiring only (§8).

## 5. Layering rules

Follow tests/test_architecture.py: from outside a module, import only `lura.modules.<x>`
(its `__init__`), never `<x>.infrastructure`. So app.py gets `accessibility_trusted` from
`lura.modules.computer` and `camera_authorization` from `lura.modules.air`. `shared/` imports
no module. The dashboard receives callables from app.py. Side effects (downloads, ctypes,
subprocess) live only in `infrastructure/` files and `shared/macos_privacy.py`.

## 6. Coding conventions (match the repo)

`from __future__ import annotations`, module docstring first line says what the file is,
type hints everywhere, `log = logging.getLogger(__name__)`, private helpers `_snake`,
constants `UPPER`. Comments explain *why*, short. Errors: backend operations raise
`RuntimeError(msg)` for failures and `NotImplementedError(msg)` for "not possible on this OS"
(as `UnsupportedBackend` does); `node.ensure` logs a warning and returns False. User-facing
English strings are short sentences. i18n: every new UI string in both `en.json` and `ar.json`
(Egyptian-friendly MSA, like the existing `air.*` Arabic). No print() outside app.py CLI code.
Line length ≤ 110.

## 7. Interfaces

```python
# node.py
NODE_MAJOR = 22
FALLBACK_VERSION = "v22.12.0"
DIST = "https://nodejs.org/dist/"
def home() -> Path                       # paths.data_dir() / "node"
def platform_triplet(system: str | None = None, machine: str | None = None) -> tuple[str, str, str] | None
    # (os, arch, ext): ("darwin"|"linux"|"win", "arm64"|"x64", "tar.xz"|"zip"); None if unsupported
def pick_version(index: list[dict], major: int = NODE_MAJOR) -> str | None
def parse_shasums(text: str, filename: str) -> str | None   # lowercase hex or None
def node_exe() -> Path | None
def npm_script(name: str) -> Path | None # name "npx" | "npm" → .../npm/bin/<name>-cli.js
def ready() -> bool
def ensure(progress: Callable[[str], None] | None = None) -> bool
def is_node_command(command: str) -> bool
def resolve(command: str, args: list[str]) -> list[str] | None
def env(base: dict[str, str]) -> dict[str, str]
def status() -> dict   # {"ready": bool, "version": str, "installing": bool, "error": str, "path": str}

# macos_pure.py
FLAG_SHIFT, FLAG_CONTROL, FLAG_OPTION, FLAG_COMMAND = 0x20000, 0x40000, 0x80000, 0x100000
KEYCODES: dict[str, int]
MAC_EQUIVALENTS: dict[str, str]
@dataclass(frozen=True)
class MacCombo:
    keycode: int | None
    flags: int
    special: str = ""      # "" or "mission_control"
def parse_combo(combo: str) -> MacCombo            # raises ValueError("Unknown key: <k>")
def parse_volume_settings(text: str) -> tuple[float, bool] | None
def parse_int(text: str) -> int | None
SKIP_OWNERS: frozenset[str]
def windows_from_cg(entries: list[dict], own_pids: set[int]) -> list[Window]
def window_script(pid: int, rect: Rect, body: str) -> str

# quartz.py
def available() -> bool
def cursor() -> tuple[int, int]
def post_mouse(kind: str, button: int, x: int, y: int, click_state: int = 1) -> None
    # kind: "move" | "down" | "up" | "drag"; button: 0 left, 1 right, 2 middle
def post_scroll(dy: int, dx: int) -> None          # lines; +dy = up, +dx = left
def post_key(keycode: int, down: bool, flags: int) -> None
def displays() -> list[tuple[int, int, int, int, int, bool]]   # (id, x, y, w, h, is_main), main first
def window_list() -> list[dict]   # each: number, pid, owner, name, layer, x, y, width, height, onscreen
def ax_trusted(prompt: bool = False) -> bool

# macos_camera.py
def authorization() -> str  # "authorized"|"denied"|"restricted"|"not_determined"|"unknown"

# shared/macos_privacy.py
PANES = {"camera": "Privacy_Camera", "accessibility": "Privacy_Accessibility",
         "microphone": "Privacy_Microphone"}
def open_settings(pane: str) -> bool
```

HTTP: `POST /api/air/permission` body `{"pane": "camera"|"accessibility"}` → returns the same
dict as `GET /api/air`. Unknown pane → `{"error": "unknown pane"}` with the API's usual error
handling (raise `ValueError("unknown pane")`, which the api layer already turns into a 400 —
check how other handlers signal bad input in api.py and do the same).

`GET /api/air` gains `"permissions"`: on macOS `{"camera": <authorization()>,
"accessibility": <bool>}`, elsewhere `{}`.

## 8. Functions

### node.py

- `home()`: `paths.data_dir() / "node"`.
- `platform_triplet(system, machine)`: defaults `platform.system()`, `platform.machine()`.
  system lower: `"darwin"`→`"darwin"`, `"linux"`→`"linux"`, `"windows"`→`"win"`, else None.
  machine lower: `"arm64"`/`"aarch64"`→`"arm64"`; `"x86_64"`/`"amd64"`/`"x64"`→`"x64"`; else
  None. ext = `"zip"` for win else `"tar.xz"`.
- `pick_version(index, major)`: first entry (index.json is newest-first) whose `version`
  starts with `f"v{major}."` and whose `lts` is truthy (a string like `"Jod"`); None if none.
- `parse_shasums(text, filename)`: for each line `"<hex>  <name>"` (split on whitespace, 2
  parts), return hex lowercased when name == filename.
- Layout: `home()/"current"/` holds the unpacked dist folder's contents; `home()/"version.txt"`
  holds e.g. `v22.12.0`. Module state: `_lock = threading.Lock()`, `_state = {"installing":
  False, "error": ""}`.
- `node_exe()`: win: `current/"node.exe"`, else `current/"bin"/"node"`; return it if it is a
  file, else None.
- `npm_script(name)`: win: `current/"node_modules"/"npm"/"bin"/f"{name}-cli.js"`, else
  `current/"lib"/"node_modules"/"npm"/"bin"/f"{name}-cli.js"`; Path if file else None.
- `ready()`: `node_exe() is not None and npm_script("npx") is not None`.
- `ensure(progress)`: with `_lock`: if `ready()` return True. triplet None → set error
  `"No Node.js build for this computer."`, return False. Set `installing=True`. Try:
  1. version: GET `DIST + "index.json"` (urllib, timeout 20, header `User-Agent: Lura`),
     `pick_version`; any failure → `FALLBACK_VERSION`.
  2. `name = f"node-{v}-{os}-{arch}"`, `filename = f"{name}.{ext}"`; progress(f"Downloading
     Node.js {v}…") if given; log.info same.
  3. GET `DIST + f"{v}/SHASUMS256.txt"`; `expected = parse_shasums(...)`; None → error.
  4. Download archive to `tempfile.TemporaryDirectory(dir=home())` (mkdir home first) in
     64 KiB chunks while hashing sha256; mismatch → `RuntimeError("Node.js download is
     corrupt (checksum mismatch).")`.
  5. Extract: zip → `ZipFile.extractall(tmp)`; tar.xz → `tarfile.open(path, "r:xz")`,
     `extractall(tmp, filter="data")` with the same `TypeError` fallback as piper.py.
     (`filter="data"` keeps exec bits and relative symlinks such as bin/npx.)
  6. If `home()/"current"` exists, `shutil.rmtree`; `shutil.move(tmp/name, home()/"current")`;
     write version.txt.
  7. On non-Windows, `os.chmod(node_exe(), 0o755)` (defensive).
  8. Verify: `subprocess.run([node_exe(), "--version"], capture_output=True, timeout=20)` must
     return 0, else RuntimeError(f"Node.js does not run: {stderr[:200]}").
  Success: error = "", log.info(f"Node.js {v} ready in {home()}"), return True.
  Any exception: error = str(exc), log.warning("Could not install Node.js: %s", exc), return
  False. Finally installing=False.
- `is_node_command(command)`: `Path(command).name.lower()`, strip one suffix of `.cmd`,
  `.exe`, `.ps1`; result in `{"node", "npx", "npm"}`.
- `resolve(command, args)`: if not `is_node_command(command)` or not `ready()` → None. base
  name `node` → `[str(node_exe()), *args]`; `npx`/`npm` → `[str(node_exe()),
  str(npm_script(base)), *args]`. Never raises (wrap in try, return None).
- `env(base)`: copy of base; if not ready return the copy unchanged. Otherwise
  `PATH = str(node_exe().parent) + os.pathsep + base.get("PATH", "")`,
  `npm_config_cache = str(home()/"npm-cache")`, `npm_config_update_notifier = "false"`,
  `npm_config_fund = "false"`, `npm_config_audit = "false"`. On Windows also set `Path` key
  removed if present (Windows env is case-insensitive; keep only `PATH`).
- `status()`: `{"ready": ready(), "version": version.txt text or "", "installing":
  _state["installing"], "error": _state["error"], "path": str(home())}`.

### clients.py — `StdioClient.__init__`

```python
from . import node
...
own = node.resolve(command, args)
if own is not None:
    self._argv = own
    self._env = node.env({**os.environ, **env})
else:
    self._argv = [self._resolve(command), *args]
    self._env = {**os.environ, **env}
```

### connectors/application.py

- In `_start`, replace the not-installed check with:
  ```python
  from .infrastructure import node
  if not (node.is_node_command(c.command) and node.ready()) \
          and not shutil.which(c.command) and not shutil.which(c.command + ".cmd"):
      if node.is_node_command(c.command):
          raise RuntimeError("Lura is installing her own Node.js for this connector; "
                             "it starts by itself when that is done.")
      raise RuntimeError(f"{c.command!r} is not installed.")
  ```
- `def ensure_runtime(self) -> bool`: `ok = node.ensure()`; if ok: for every connector
  that is enabled, has `transport != HTTP`, `node.is_node_command(c.command)` and whose
  `_running` entry has `status == "error"` (or no entry) → `self.start_one(name)`. Return ok.
  Read `_running` under `self._lock`, call `start_one` outside it.
- `def runtime_status(self) -> dict`: `return node.status()`.

### quartz.py

Lazy loader `_lib()` returns a namespace with `cg`, `cf`, `ax` (ApplicationServices) loaded
once (`functools.lru_cache`), or raises `OSError` off macOS. Declare `restype`/`argtypes` for
every function used (CGPoint = Structure of two `c_double`; CGRect = origin CGPoint + size
CGSize(two c_double)). **No variadic C functions** (ctypes passes variadic args wrongly on
Apple arm64): use `CGEventCreateScrollWheelEvent2`, not `CGEventCreateScrollWheelEvent`.

- `available()`: try `_lib()`; True, or False on any exception.
- `cursor()`: `e = CGEventCreate(None)`; `p = CGEventGetLocation(e)`; `CFRelease(e)`;
  `(round(p.x), round(p.y))`.
- `post_mouse(kind, button, x, y, click_state)`: event type table:
  left `{"down": 1, "up": 2, "move": 5, "drag": 6}`, right `{"down": 3, "up": 4, "move": 5,
  "drag": 7}`, middle `{"down": 25, "up": 26, "move": 5, "drag": 27}`.
  `e = CGEventCreateMouseEvent(None, type, CGPoint(x, y), button)`; for down/up
  `CGEventSetIntegerValueField(e, 1, click_state)` (kCGMouseEventClickState = 1);
  `CGEventPost(0, e)` (kCGHIDEventTap = 0); `CFRelease(e)`.
- `post_scroll(dy, dx)`: `CGEventCreateScrollWheelEvent2(None, 1, 2, dy, dx, 0)` (unit line
  = 1; argtypes `c_void_p, c_uint32, c_uint32, c_int32, c_int32, c_int32`), post, release.
- `post_key(keycode, down, flags)`: `CGEventCreateKeyboardEvent(None, keycode, down)`;
  `CGEventSetFlags(e, flags)` (c_uint64); post; release.
- `displays()`: `CGGetActiveDisplayList(16, arr, byref(count))`; `CGMainDisplayID()`; for
  each id `CGDisplayBounds(id)` → tuple; sort so the main display is first.
- `window_list()`: `CGWindowListCopyWindowInfo(1 | 16, 0)` (OnScreenOnly | ExcludeDesktop
  Elements). Iterate `CFArrayGetCount` / `CFArrayGetValueAtIndex`; read each dict with
  `CFDictionaryGetValue(d, key)` where keys are CFStrings made once with
  `CFStringCreateWithCString(None, b"kCGWindowNumber", 0x08000100)` (UTF-8), etc.:
  `kCGWindowNumber`, `kCGWindowOwnerPID`, `kCGWindowLayer` (CFNumber →
  `CFNumberGetValue(n, 4, byref(c_int64))`), `kCGWindowOwnerName`, `kCGWindowName` (CFString →
  `CFStringGetCString(s, buf, 1024, 0x08000100)`; missing → ""), `kCGWindowIsOnscreen`
  (`CFBooleanGetValue`; missing → False), `kCGWindowBounds` (CFDictionary →
  `CGRectMakeWithDictionaryRepresentation(d, byref(CGRect))`). `CFRelease` the array.
  Return list of dicts with keys `number, pid, owner, name, layer, x, y, width, height,
  onscreen` (ints rounded, strings, bool).
- `ax_trusted(prompt)`: `prompt=False` → `bool(AXIsProcessTrusted())`. `prompt=True` → build a
  CFDictionary `{kAXTrustedCheckOptionPrompt: kCFBooleanTrue}` with `CFDictionaryCreate` using
  `kCFTypeDictionaryKeyCallBacks` / `kCFTypeDictionaryValueCallBacks` (`addressof` the
  `in_dll` structs — pass their addresses), key from `c_void_p.in_dll(ax,
  "kAXTrustedCheckOptionPrompt")`, value `c_void_p.in_dll(cf, "kCFBooleanTrue")`; call
  `AXIsProcessTrustedWithOptions(dict)`, release dict. Any exception → False.

### macos_pure.py

- `KEYCODES` (macOS virtual key codes), keys lowercase:
  letters a 0x00, s 0x01, d 0x02, f 0x03, h 0x04, g 0x05, z 0x06, x 0x07, c 0x08, v 0x09,
  b 0x0B, q 0x0C, w 0x0D, e 0x0E, r 0x0F, y 0x10, t 0x11, o 0x1F, u 0x20, i 0x22, p 0x23,
  l 0x25, j 0x26, k 0x28, n 0x2D, m 0x2E; digits 1 0x12, 2 0x13, 3 0x14, 4 0x15, 6 0x16,
  5 0x17, 9 0x19, 7 0x1A, 8 0x1C, 0 0x1D; equal 0x18, minus 0x1B, bracketright 0x1E,
  bracketleft 0x21, apostrophe 0x27, semicolon 0x29, backslash 0x2A, comma 0x2B, slash 0x2C,
  period 0x2F, grave 0x32; return/enter 0x24, tab 0x30, space 0x31, backspace 0x33,
  escape/esc 0x35, delete 0x75, home 0x73, end 0x77, page_up/prior 0x74,
  page_down/next 0x79, left 0x7B, right 0x7C, down 0x7D, up 0x7E; f1 0x7A, f2 0x78, f3 0x63,
  f4 0x76, f5 0x60, f6 0x61, f7 0x62, f8 0x64, f9 0x65, f10 0x6D, f11 0x67, f12 0x6F.
- `MAC_EQUIVALENTS` (exact lowercase combo → mac combo; Linux shortcuts the computer module
  sends, translated to what a Mac app expects):
  `ctrl+c→cmd+c, ctrl+x→cmd+x, ctrl+v→cmd+v, ctrl+a→cmd+a, ctrl+z→cmd+z,
  ctrl+shift+z→cmd+shift+z, ctrl+y→cmd+shift+z, ctrl+s→cmd+s, ctrl+f→cmd+f, ctrl+t→cmd+t,
  ctrl+w→cmd+w, ctrl+l→cmd+l, ctrl+r→cmd+r, ctrl+n→cmd+n, ctrl+q→cmd+q,
  alt+left→cmd+bracketleft, alt+right→cmd+bracketright,
  ctrl+page_up→cmd+shift+bracketleft, ctrl+page_down→cmd+shift+bracketright,
  alt+tab→cmd+tab, alt+f4→cmd+w, super→mission_control`.
- `parse_combo(combo)`: `c = combo.strip().lower()`; empty → ValueError("Empty key combo.").
  `c = MAC_EQUIVALENTS.get(c, c)`; `"mission_control"` → `MacCombo(None, 0,
  "mission_control")`. Split on `+`; all but last are modifiers: `ctrl`/`control`→CONTROL,
  `shift`→SHIFT, `alt`/`option`/`opt`→OPTION, `super`/`cmd`/`command`/`meta`/`win`→COMMAND;
  unknown modifier → ValueError(f"Unknown modifier: {m}"). Last part: if it is a modifier
  name itself (e.g. lone "cmd") → ValueError. Key lookup in KEYCODES (also accept xdotool
  names with `_` removed: `pageup`, `pagedown`); missing → ValueError(f"Unknown key: {k}").
- `parse_volume_settings(text)`: expects `"<int>,<true|false>"` (whitespace allowed);
  returns `(int/100 clamped 0..1, muted)`; anything else → None.
- `parse_int(text)`: int of stripped text or None (also "missing value" → None).
- `SKIP_OWNERS = frozenset({"Window Server", "Dock", "Control Center", "Notification Center",
  "Spotlight", "SystemUIServer", "WindowManager", "Screenshot"})`.
- `windows_from_cg(entries, own_pids)`: keep entries with `layer == 0`, `pid not in own_pids`,
  `owner not in SKIP_OWNERS`, `width >= 40 and height >= 40`. Map to `Window(id=number,
  title=name or owner, app=owner, pid=pid, desktop=0, rect=Rect(x, y, width, height),
  maximized=False, minimized=False, kind="normal")`. Order preserved (CG returns front to
  back, so index 0 is the frontmost window).
- `window_script(pid, rect, body)`: returns AppleScript that finds the AX window of process
  `pid` whose position/size is within 6 points of `rect` (fallback `window 1`), binds it to
  `w`, then runs `body` (which may use `w` and `p`):
  ```applescript
  tell application "System Events"
    set p to first application process whose unix id is {pid}
    set w to missing value
    repeat with cand in windows of p
      set {cx, cy} to position of cand
      set {cw, ch} to size of cand
      set dx to cx - {x}
      set dy to cy - {y}
      set dw to cw - {width}
      set dh to ch - {height}
      if dx < 7 and dx > -7 and dy < 7 and dy > -7 and dw < 7 and dw > -7 and dh < 7 and dh > -7 then
        set w to cand
        exit repeat
      end if
    end repeat
    if w is missing value then set w to window 1 of p
    {body}
  end tell
  ```

### macos.py — `MacBackend`

Attributes: `name = "macos"`; `self._probe_cache: dict | None = None`; `self._held: int | None
= None` (button held for drags, 1/2/3); `self._showing = False`; `self._mic_level = 75`.
Helpers: `_osa(script, timeout=4.0) -> str` → `runner.run(["osascript", "-e", script],
timeout)`; not ok → `RuntimeError(f"osascript failed: {detail[:200]}")`. `_cg_windows()` →
`windows_from_cg(quartz.window_list(), self.own_pids())`. `_ax_body(wid, body)`: find the
window (`self.window(wid)`), None → `RuntimeError(f"No window {wid}.")`, run
`window_script(w.pid, w.rect, body)`.

- `unsupported_reason()`: `""` if `quartz.available()` else `"macOS CoreGraphics could not be
  loaded"`.
- `probe()`: return cache if set. `ax = quartz.ax_trusted(False)`; caps = `{"x11": True,
  "xprop": ax, "xwininfo": True, "xrandr": True, "xdotool": ax, "wpctl": True, "gsd_power":
  False, "playerctl": False, "loginctl": True, "xclip": True, "gnome": True}`. (The
  computer domain's `requires` tokens are named after Linux tools; on a Mac they mean the
  same capability: `xdotool` = input events, `xprop` = window control — both need
  Accessibility. Add exactly this comment.) Cache only when `ax` is True, so granting the
  permission takes effect without a restart.
- `request_permissions() -> dict`: `{"accessibility": quartz.ax_trusted(True)}`; clear
  `_probe_cache`.
- `own_pids()`: `{os.getpid(), os.getppid()}`.
- `window(wid)`: first of `_cg_windows()` with `id == wid`, else None. `windows()`:
  `_cg_windows()` with `maximized` set True when the rect is within 8 pt of `workarea()`'s
  monitor-clipped area (use `dataclasses.replace`). `active_window()`: id of `_cg_windows()[0]`
  or None.
- `monitors()`: from `quartz.displays()`: `Monitor(index=i, name=f"Display {i + 1}",
  rect=Rect(x, y, w, h), primary=is_main)`.
- `workarea()`: main monitor rect with `MENU_BAR = 25` points removed from the top
  (`Rect(x, y + 25, w, h - 25)`); None if no displays.
- `desktops()`: always `(1, 3)`. `switch_desktop(desktop)`: `steps = desktop - 1`; for each
  step run `_osa('tell application "System Events" to key code 124 using control down')`
  (124 = right, steps > 0; 123 = left, steps < 0), 0.35 s apart — Mission Control's default
  Space shortcuts; a synthetic CGEvent Control+arrow may not switch Spaces, System Events does. Comment: macOS has no public API for
  Spaces, so Lura only moves relative to the current one — `desktops()` reports the current
  Space as the middle of three so "next"/"prev" resolve to ±1.
- `set_window_desktop(...)`: `NotImplementedError("macOS has no way for apps to move a window
  to another Space.")`.
- `showing_desktop()`: `self._showing`. `set_showing_desktop(on)`: if `on != self._showing`,
  post F11 (keycode 0x67, flags 0) and flip `_showing`.
- `overview()`: `runner.run(["open", "-a", "Mission Control"])`; not ok → RuntimeError.
- `activate(wid)`: `_ax_body(wid, 'set frontmost of p to true\n    perform action "AXRaise" of w')`.
- `move_resize(wid, frame)`: body `set position of w to {x, y}` + `set size of w to {w, h}`.
- `set_maximized(wid, on)`: on → `move_resize(wid, workarea of the window's monitor)` (use
  `monitor_for` from `..domain` and clip the main monitor's menu bar only for the primary);
  off → no-op (macOS has no un-maximize state to restore).
- `minimize(wid)`: body `set value of attribute "AXMinimized" of w to true`.
- `close(wid)`: body `click (first button of w whose subrole is "AXCloseButton")`.
- `pointer()`: `quartz.cursor()`.
- `pointer_move(x, y)`: `quartz.post_mouse("drag" if self._held else "move", btn, x, y)` with
  `btn = {1: 0, 3: 1, 2: 2}[self._held or 1]`.
- `pointer_button(button, action, count=1)`: buttons 4/5/6/7 = scroll: `(dy, dx) = {4: (1, 0),
  5: (-1, 0), 6: (0, 1), 7: (0, -1)}`; `action == "click"` → post `count` scroll events 15 ms
  apart; other actions on scroll buttons → ignore. Buttons 1/2/3: `x, y = quartz.cursor()`;
  `click`: for i in 1..count: down(click_state=i), up(click_state=i), 60 ms apart; `down`:
  post down, `self._held = button`; `up`: post up, `self._held = None`. Unknown action →
  `RuntimeError(f"Unknown pointer button action: {action}")`.
- `key(combo)`: `mc = parse_combo(combo)` (ValueError → RuntimeError(str)); special
  `mission_control` → `overview()`; else `_post_combo(mc)`: `post_key(mc.keycode, True,
  mc.flags)`, `post_key(mc.keycode, False, mc.flags)`.
- `volume()`: `parse_volume_settings(_osa('set s to get volume settings\nreturn (output volume
  of s as text) & "," & (output muted of s as text)'))`; None → RuntimeError.
- `set_volume(fraction)`: `_osa(f"set volume output volume {round(fraction * 100)}")`.
- `change_volume(delta_percent)`: read `volume()`, clamp `0..100`, `set_volume`.
- `toggle_mute()`: `_osa("set volume output muted not (output muted of (get volume settings))")`.
- `mic_muted()`: `parse_int(_osa("input volume of (get volume settings)")) == 0`.
- `toggle_mic()`: level = that int; if level > 0: `self._mic_level = level`, set
  `input volume 0`; else set `input volume {self._mic_level}`.
- `brightness()` / `set_brightness()`: `NotImplementedError("Brightness control is not
  available on macOS yet.")`. `media()`: `NotImplementedError("Media keys are not available on
  macOS yet.")`.
- `lock()`: `runner.run(["pmset", "displaysleepnow"])`, not ok → RuntimeError.
- `clipboard_get()`: `runner.run(["pbpaste"])` out. `clipboard_set(text)`:
  `runner.run(["pbcopy"], input_text=text)`.

### computer/infrastructure/__init__.py

`if IS_MAC: from .macos import MacBackend; return MacBackend()` (import inside the branch).

### computer/application.py

- Capabilities data line: `"backend": reason or getattr(self._backend, "name", "x11")`.
- `def request_permissions(self) -> dict`: `fn = getattr(self._backend, "request_permissions",
  None)`; `return fn() if fn else {}`; clear `self._probe = None` after.
- `computer/__init__.py`: export `accessibility_trusted(prompt: bool = False) -> bool`, a
  function defined in `__init__` that returns True off macOS and
  `quartz.ax_trusted(prompt)` on macOS.

### air/infrastructure/macos_camera.py

`authorization()`: off macOS → `"authorized"`. On macOS (lazy, cached libs): load AVFoundation
(so the class is registered) and libobjc; `objc_getClass(b"AVCaptureDevice")`,
`sel_registerName(b"authorizationStatusForMediaType:")`; media type
`c_void_p.in_dll(avf, "AVMediaTypeVideo")`; call `objc_msgSend` cast with
`CFUNCTYPE(c_long, c_void_p, c_void_p, c_void_p)` (exact argtypes — never variadic). Map
`0→not_determined, 1→restricted, 2→denied, 3→authorized`. Any exception → `"unknown"`.
`air/__init__.py` exports it as `camera_authorization`.

### cameras.py — `open()`

```python
if IS_MAC:
    status = camera_authorization()
    if status in ("denied", "restricted"):
        raise RuntimeError("Camera access is off for Lura. Turn it on in System Settings → "
                           "Privacy & Security → Camera, then switch Lura Air off and on.")
backend = cv2.CAP_V4L2 if IS_LINUX else (cv2.CAP_AVFOUNDATION if IS_MAC else cv2.CAP_ANY)
cap = cv2.VideoCapture(self._info.index, backend)
if IS_MAC and (not cap or not cap.isOpened()) and status == "not_determined":
    # OpenCV asks macOS for the camera and fails this first open; wait for the user's answer.
    if cap is not None: cap.release()
    for _ in range(120):                 # up to 60 s
        time.sleep(0.5)
        if camera_authorization() != "not_determined": break
    if camera_authorization() == "authorized":
        cap = cv2.VideoCapture(self._info.index, backend)
```
then the existing not-opened check. Skip the `FOURCC` MJPG line on macOS (AVFoundation ignores
or rejects it). Import `IS_MAC` and `time`; import `authorization as camera_authorization` from
`.macos_camera`.

### shared/macos_privacy.py

`open_settings(pane)`: pane not in PANES → False. Not macOS → False. Run `["open",
f"x-apple.systempreferences:com.apple.preference.security?{PANES[pane]}"]` with
`subprocess.run(..., capture_output=True, timeout=10)`; return returncode == 0 (exceptions →
False).

### dashboard

- `api.py`: new dataclass field `air_permission: Callable[[str], dict] | None = None`; route
  `r("POST", "/api/air/permission", lambda b, m: self.air_permission_open(b))`;
  `air_permission_open(body)`: pane = `str((body or {}).get("pane", ""))`; not in
  `("camera", "accessibility")` → bad input as other handlers do; `self.air_permission is None`
  → `self.air_status()`; else `return {**self.air_permission(pane), "running": True}`.
  `air_status()` default dict (standalone) gets `"permissions": {}`.
- `index.html`: inside `#air-card`, right after `<p class="pill" id="air-status" hidden></p>`:
  `<div id="air-perms" hidden></div>`.
- `cards.js` in `renderAir()` after the status box: `renderAirPerms(on ? airStatus : null)`.
  New function `renderAirPerms(a)`: `const box = $("#air-perms")`; if `!a || !a.permissions ||
  !Object.keys(a.permissions).length` → `box.hidden = true; return`. Build (replace
  `box.innerHTML` only when the computed state string changed, keep it in a module-level
  `lastPerms`, so polling does not rebuild buttons under the cursor) two rows
  `<div class="air-perm"><span>{label}</span><b>{state}</b>[<button class="btn small"
  data-pane="{pane}">{open}</button>]</div>`: camera ok = `permissions.camera ===
  "authorized"`, state `t("air.perm.ok")`, `"not_determined"` → `t("air.perm.ask")`, else
  `t("air.perm.needed")`; accessibility ok = `permissions.accessibility === true`. Button only
  when not ok. Button onclick → `airStatus = await api("POST", "/api/air/permission", {pane});
  renderAir();`. Escape text through the existing helper the file uses for text (use
  `textContent` assignments if no helper exists). `box.hidden = false`.
- `app.css`: `.air-perm { display: flex; align-items: center; gap: 10px; margin-top: 6px; }`
  `.air-perm b { font-weight: 600; }` `.air-perm button { margin-inline-start: auto; }`.
- i18n (en / ar):
  - `air.perm.camera`: "Camera" / "الكاميرا"
  - `air.perm.accessibility`: "Accessibility (move the mouse and windows)" / "تسهيلات الاستخدام (تحريك الماوس والنوافذ)"
  - `air.perm.ok`: "Allowed" / "مسموح"
  - `air.perm.needed`: "Not allowed yet" / "لسه مش مسموح"
  - `air.perm.ask`: "macOS will ask when the camera starts" / "macOS هيسألك أول ما الكاميرا تشتغل"
  - `air.perm.open`: "Open settings" / "افتح الإعدادات"

### app.py

- Import `IS_MAC` from `.shared.platform` if not already imported.
- `air_status()` adds `"permissions": _mac_permissions()` where
  `_mac_permissions()` returns `{}` off macOS, else `{"camera": camera_authorization(),
  "accessibility": accessibility_trusted(False)}` (imports from `.modules.air` /
  `.modules.computer`, inside the function, wrapped in try → `{}`).
- `def air_permission(pane: str) -> dict`: if pane == "accessibility":
  `c.computer.request_permissions()`; `open_settings(pane)` (from `.shared.macos_privacy`);
  return `air_status()`. Pass `air_permission=air_permission` to `DashboardApi(...)`.
- `start_air()`: after `c.air.start()` and before `c.air_control.start()`: `if IS_MAC and not
  accessibility_trusted(False): c.computer.request_permissions()` (shows macOS's own
  Accessibility prompt once).
- Replace `c.connectors.start_all()` with:
  ```python
  c.connectors.start_all()
  threading.Thread(target=c.connectors.ensure_runtime, daemon=True, name="node-runtime").start()
  ```
- `mcp` CLI: add `"runtime"` to the choices and to the help string. In `cmd_mcp`,
  `elif args.action == "runtime": ok, msg = c.connectors.runtime_check(); print(msg);
  return 0 if ok else 1` (placed so the function's normal `finally`/cleanup still runs —
  follow how the other branches return). app.py must not import connectors infrastructure,
  so add to `ConnectorService` (connectors/application.py) `def runtime_check(self) ->
  tuple[bool, str]`:
  ensure (`node.ensure(progress=log.info)`), then run `node.resolve("npx", ["--version"])`
  with `subprocess.run(argv, env=node.env(dict(os.environ)), capture_output=True, text=True,
  timeout=120)`; return `(returncode == 0, f"Node.js {status()['version']} in
  {status()['path']}, npx {stdout.strip()}")` or `(False, error text)`; exceptions →
  `(False, str(exc))`. (Do not start connectors in this CLI path.)

### packaging/pyinstaller/lura.spec

In `info_plist` add after `NSMicrophoneUsageDescription`:
`"NSCameraUsageDescription": "Lura Air watches your hand through the camera to move the mouse
and follow your gestures. Frames stay on this Mac.",`

### .github/workflows/release.yml

- After the `Smoke test the build` step (the frozen app, so lzma/HTTPS inside the bundle are
  proven too), new step (all OSes):
  ```yaml
      # Lura installs her own Node.js for the connectors; prove the shipped app can.
      - name: Node runtime for connectors
        shell: bash
        env: { LURA_DATA_DIR: "${{ runner.temp }}/lura-data" }
        run: |
          if [ "$RUNNER_OS" = "macOS" ]; then dist/Lura.app/Contents/MacOS/lura mcp runtime
          elif [ "$RUNNER_OS" = "Windows" ]; then dist/lura/lura.exe mcp runtime
          else dist/lura/lura mcp runtime; fi
  ```
- After it, macOS only, non-blocking:
  ```yaml
      - name: Desktop control probe (macOS)
        if: runner.os == 'macOS'
        continue-on-error: true
        run: python -m lura.app desktop capabilities
  ```
- Release body: replace the line `The built-in Chrome, web and files connectors need Node.js
  (npx).` with:
  ```
  Lura installs her own Node.js for the connectors the first time she starts (about 30 MB), so
  nothing else is needed. On a Mac, Lura Air asks for Camera and Accessibility access; the
  Hands tab shows both and opens the right System Settings page.
  ```

### README.md

Where it says connectors need Node.js/npx, say Lura downloads her own Node.js into the data
folder (`<data>/node`) on first start. In "Where things live" add the `node/` folder. Add a
short "Lura Air on macOS" note: Camera + Accessibility permissions, the Hands tab buttons,
Spaces move one at a time with Control+←/→ (Mission Control shortcuts must be on), brightness
and media keys are not supported on macOS yet. Lura is signed ad hoc, so macOS may ask for
Accessibility again after an update (remove Lura from the list and add it back).

### tests

`tests/test_node_runtime.py` (plain script like the other tests: asserts, prints
`node runtime OK` at the end, `sys.path.insert(0, str(SRC))` as the others do):
- `platform_triplet("Darwin", "arm64") == ("darwin", "arm64", "tar.xz")`,
  `("Darwin","x86_64")→("darwin","x64","tar.xz")`, `("Windows","AMD64")→("win","x64","zip")`,
  `("Linux","aarch64")→("linux","arm64","tar.xz")`, `("SunOS","sparc") is None`.
- `pick_version([{"version":"v23.1.0","lts":False},{"version":"v22.12.0","lts":"Jod"},
  {"version":"v22.11.0","lts":"Jod"}]) == "v22.12.0"`; empty list → None.
- `parse_shasums("abc  node-v22.12.0-darwin-arm64.tar.xz\nDEF  other.zip\n",
  "other.zip") == "def"`; missing → None.
- Fake install: set `LURA_DATA_DIR` to a temp dir **before** importing; build
  `node/current/bin/node` (or `node.exe` on Windows) and the npm `bin/npx-cli.js` /
  `npm-cli.js` files at the paths in §8; then `ready()` True;
  `resolve("npx", ["-y", "pkg"]) == [node, npx_cli, "-y", "pkg"]`; `resolve("npx.cmd", [])`
  works; `resolve("/usr/bin/node", ["a"]) == [node, "a"]`; `resolve("python", []) is None`;
  `env({"PATH": "/usr/bin"})["PATH"].startswith(str(bin_dir) + os.pathsep)`; `npm_config_cache`
  ends with `npm-cache`; `is_node_command("NPX.CMD")` True.
- `StdioClient("npx", ["-y", "x"], {"A": "1"})._argv[0] == str(node)` and `_env["A"] == "1"`.

`tests/test_macos.py` (prints `macos OK`):
- `parse_combo("ctrl+c") == MacCombo(0x08, FLAG_COMMAND)`; `parse_combo("alt+Left")` →
  keycode 0x21, COMMAND; `parse_combo("super").special == "mission_control"`;
  `parse_combo("ctrl+shift+Tab")` → 0x30, CONTROL|SHIFT; `parse_combo("F5").keycode == 0x60`;
  `parse_combo("ctrl+Page_Down")` → 0x1E, COMMAND|SHIFT; `parse_combo("cmd+space")` → 0x31;
  `parse_combo("ctrl+Right")` → 0x7C, CONTROL; `parse_combo("foo")` and `parse_combo("hyper+a")`
  raise ValueError.
- `parse_volume_settings("50,false") == (0.5, False)`; `"100, true"` → `(1.0, True)`;
  `"x"` → None. `parse_int("missing value") is None`; `parse_int(" 42\n") == 42`.
- `windows_from_cg`: entries for Dock (skipped), layer 25 (skipped), own pid (skipped), a
  30×30 window (skipped), and two normal windows → two `Window`s in order, title falls back to
  owner when name is "".
- `window_script(123, Rect(10, 20, 300, 400), "perform action \"AXRaise\" of w")` contains
  `unix id is 123`, `cx - 10`, `cy - 20`, `cw - 300`, `ch - 400` and the body.
- `quartz.available()` is False off macOS (only assert when `sys.platform != "darwin"`);
  `macos_camera.authorization() == "authorized"` off macOS; `macos_privacy.open_settings
  ("camera") is False` off macOS; `open_settings("nope") is False` everywhere.
- On macOS only (`sys.platform == "darwin"`): `MacBackend().unsupported_reason() == ""`,
  `monitors()` non-empty, `pointer()` is a 2-tuple of ints. (CI runs this on the Mac runners.)

`tests/run_all.py`: append `"test_node_runtime.py", "test_macos.py"` to `CHECKS`.

## 9. Tasks

1. node.py + clients.py + connectors/application.py changes + test_node_runtime.py.
2. macos_pure.py + quartz.py + macos.py + infrastructure/__init__.py + computer
   application/`__init__` changes.
3. macos_camera.py + cameras.py + air `__init__` export.
4. shared/macos_privacy.py, dashboard API/HTML/JS/CSS, i18n.
5. app.py wiring + `lura mcp runtime`.
6. lura.spec, release.yml, README.md.
7. test_macos.py, run_all.py; run §10.

## 10. Acceptance checks

Run from the repo root (`/var/www/html/old/lura-mac`), with
`PY=/var/www/html/old/lura-air/.venv/bin/python` and `PYTHONPATH=src`:

```bash
$PY -m compileall -q src/                          # no output
PYTHONPATH=src $PY tests/run_all.py                # ends with "All checks passed."
PYTHONPATH=src $PY tests/test_node_runtime.py      # "node runtime OK"
PYTHONPATH=src $PY tests/test_macos.py             # "macos OK"
PYTHONPATH=src $PY tests/test_architecture.py      # "architecture OK (...)"
cd src && ../../lura-air/.venv/bin/python -m lura.modules.overlay   # "overlay self-check OK"
cd .. && LURA_DATA_DIR=$(mktemp -d) PYTHONPATH=src $PY -m lura.app mcp runtime
   # downloads Node 22 (network), prints "Node.js v22.… in …/node, npx 10.…", exit 0
python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/release.yml'))" 2>/dev/null || true
```

## 11. Out of scope

Do not change version numbers, docs/, CLAUDE.md, AGENTS.md, the Linux X11 backend, the Air
engine/gesture logic, the overlay renderer or the landing site. No Windows desktop backend.
No notarisation or Developer ID signing. Do not bundle Node inside the PyInstaller build. Do
not add Python dependencies. **Do not commit, push or tag.**

## 12. Decisions

(appended as questions come in)
- D1 (kind): `windows_from_cg()` sets `kind="NORMAL"` (domain `_TARGET_KINDS` is upper-case). domain.py untouched.
- D2 (desktop): `windows_from_cg()` sets `desktop=1`, matching `desktops() == (1, 3)`.
- D3 (order): `windows_from_cg()` stays front-to-back (index 0 = frontmost, used by
  `active_window()` and the tests); `MacBackend.windows()` returns the list **reversed**
  (bottom-to-top, like X11's stacking list) — `window()` and `active_window()` keep using the
  front-to-back list.
