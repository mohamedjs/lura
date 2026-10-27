# CLAUDE.md — Lura

Lura (لورا) is a voice-first desktop assistant: Linux (.deb), macOS (Homebrew cask)
and Windows (installer .exe). Say "Lura", hear a chime, talk; she answers out loud in
Arabic or English and can act on the machine (open apps/URLs, find files, run guarded
commands, briefing, MCP connectors). Any of Gemini / OpenAI / Anthropic / OpenRouter
can be the brain; the voice is always Gemini 3.8 Flash TTS (direct or via OpenRouter).
A floating transparent hologram face shows on every monitor; a local dashboard
(neumorphism + grid, handwritten fonts, AR/EN) configures everything. Free plan +
Pro ($5/month, Lemon Squeezy licence) which unlocks custom connectors. On Mohamed's
machine it runs as a `systemd --user` service from `/var/www/html/lura`.

Repo: `git@github.com:mohamedjs/lura.git`, default branch `main`.

**Read these too:** `AGENTS.md` (hard-won facts: auth, wake word, audio, service —
read before changing code), `README.md` (install + reference), `USAGE.md`
(day-to-day), `video/README.md` (promo video renderer). Where they disagree with
this file, this file is newer.

---

## Working with the user

- Mohamed writes in Egyptian Arabic or English. **Reply in Egyptian Arabic when he
  writes Arabic**, English when he writes English.
- He tests by running the service himself:
  `systemctl --user restart lura.service` then `journalctl --user -u lura -f`.
  Always end with the exact command he should run to see the change.
- Don't commit or push unless asked. When asked, he is usually on `main`.

## Secrets — hard rules

- `env-d.txt` (repo root, git-ignored) and `~/.config/lura/keys.json` hold real
  API keys (Gemini, OpenRouter, Deepgram…). **Never print, copy, commit or use
  them** unless Mohamed explicitly says to use a key.
- `~/.config/lura/mcp.json` holds MCP server tokens (e.g. GitHub PAT). Same rule.
- Never commit `.env`, `keys.json`, `env-d.txt`, `video/node_modules/`,
  `_to_delete/`, rendered videos or test PNGs. Check `git diff --cached --stat`
  before every commit — a past commit (`915e24a`) accidentally included
  `node_modules` and scratch images.

---

## Layout (modular monolith — see docs/ARCHITECTURE.md)

```
src/lura/
  app.py, bootstrap.py, selftest.py   CLI · composition root (only place modules are wired) · selftest
  shared/       paths, http (JSON+SSE), events bus, presence protocol, desktop autostart, errors, i18n/{en,ar}.json
  modules/      each: domain.py (pure) · application.py · infrastructure/ · __init__ = public API
    settings/   Settings, providers, voices, capability rules; settings.json + keys.json (0600)
    brain/      OpenAI-compatible adapter (OpenAI, OpenRouter, Gemini) + Anthropic SDK adapter, streaming
    voice/      SentenceChunker, Gemini TTS direct/OpenRouter, transcriber, audio, Vosk wake, Gemini Live
    tools/      built-in tools + command guard (check_command), per-OS impl, briefing
    connectors/ MCP stdio + Streamable HTTP, mcp.json, 3 built-ins (chrome, web, files), Pro rule
    billing/    Free/Pro, Lemon Squeezy licence API (store.json filled at release)
    assistant/  wake loop, PipelineConversation (streams brain → TTS), realtime, briefing
    overlay/    face_mp.py mesh, render.py (cairo), gtk_backend (Linux source), qt_backend (+tray)
    dashboard/  server.py (127.0.0.1 + token), api.py, static/ (index.html, app.css, app.js, fonts)
tests/          run_all.py → architecture, command guard, domain, integration (mock servers), dashboard
packaging/      pyinstaller spec, linux (deb, unit, .desktop), windows (Inno Setup), homebrew cask, icons
.github/workflows/  ci.yml, release.yml (tag v* → .deb/.tar.gz, .app zips, Setup.exe, release, tap)
install.sh      from-source install on Linux (venv + user service)
video/          Node + Playwright promo-video renderer (separate from the app)
```

Paths (Linux, unchanged from 1.x): config `~/.config/lura/` (settings.json, keys.json,
mcp.json, license.json, dashboard.url), data `~/.local/share/lura/` (vosk-model,
last_briefing, lura.log). macOS/Windows paths: README → Where things live. Venv `.venv/`
is an **editable install**, so editing `src/` + restarting the service is enough.

## Current behaviour (2.0, Oct 2026)

- **Default:** mode `pipeline`, brain `openrouter` / `google/gemini-3.8-flash`, voice
  `auto` (Gemini direct if a Gemini key exists, else OpenRouter), voice `Kore` (female) /
  `Puck` (male), `reasoning_effort` minimal, language `en`.
- **Rule:** any provider thinks; hearing + speaking need a Gemini or OpenRouter key.
  Gemini brains hear the WAV directly; Claude/GPT get a Gemini 3.8 Flash transcript.
- **Streaming:** brain text streams into `SpeechPlayer` → first sentence synthesised
  while the rest is written. TTS falls back to the Lite TTS model on retryable errors only.
- **Anthropic:** official SDK, default `claude-opus-5-5`, `output_config.effort`,
  `fallbacks: "default"`; turns replayed verbatim.
- **Realtime:** Gemini Live `gemini-2.5-flash-native-audio-preview-12-2025`, mic gated.
- **Connectors:** built-ins `chrome` (chrome-devtools-mcp), `web` (mcp-fetch-server),
  `files` (server-filesystem ~) via `npx`; custom ones need Pro. Pipeline mode pulls MCP
  schemas only via `find_tools`.
- **Settings migration:** a 1.x settings.json (`provider`, `openrouter_model`, …) is
  converted on load (`settings/domain.py: migrate_legacy`).
- **Command guard** also blocks Windows/macOS destructive commands. Test:
  `python3 tests/test_command_guard.py` → `command guard OK (29 blocked, 19 allowed)`.
- **Dashboard window:** native — pywebview (WebView2 / WKWebView) on Windows/macOS, Qt
  WebEngine on Linux (bundled in the Linux build only), in a separate `lura window` process.
- **Model picker:** `/api/catalog` lists brain-capable models (chat + tools) of every keyed
  provider (`brain/domain.py: brain_candidate`). Modes: "My model" / "Gemini Realtime".
- **Restart button** appears after settings/key changes (except live ones: subtitles,
  ui_language); daemon exits 75 under systemd, else re-execs. Dashboard token is stable
  (`dashboard.token`) so the open window reconnects.
- **Subtitles** under the face (`overlay/captions.py`, drawn by Qt/Pango so Arabic shapes):
  what you said + Lura's streamed reply; toggle live from dashboard, tray, or right-click on
  the face. When the brain hears audio directly, a parallel transcription fills "You".
- **Briefing** once per boot, in the conversation language; GitHub line only if a
  connector named `github` is connected (it is custom → needs Pro).

## The overlay face (`modules/overlay/`)

- Look Mohamed approved: the promo video's face — MediaPipe mesh as **indigo
  wireframe over translucent lavender facets**, teal blinking eyes, teal lips and
  jaw lines, dotted cyan orbit ring, cyan light band sweeping down.
  **Fully transparent background** — no disc, halo or rings behind it (he asked
  for "only the face").
- Palette constants: `INDIGO`, `LAVENDER`, `TEAL`, `CYAN` near the top of the
  renderer; brightened so it reads on dark *and* light wallpapers.
- States via `StateBlender`: listening (calm, dimmer), thinking (more turn,
  faster band), speaking (jaw opens with `OverlayState.amplitude`, mouth glows).
- `OVERLAY_SIZE = 260`, 30 fps, one window per monitor, draggable, double-click opens
  the dashboard. GTK backend when running from source on Linux; Qt (+ tray icon)
  on macOS/Windows and in packaged builds.
- Performance: ~9 ms draw + ~2 ms conversion per frame. Frames go to GdkPixbuf as
  raw RGBA via `surface_rgba()` (Qt takes the cairo buffer directly) (un-premultiply BGRA → RGBA). **Don't go back to
  a PNG round-trip** — it cost ~15 ms/frame for this detailed mesh. Keep cairo
  calls batched by alpha bucket (`_bucket_stroke`) and iterate `.tolist()`, not
  numpy scalars.
- Self-check (no display needed): `cd src && python3 -m lura.modules.overlay` →
  `overlay self-check OK`.

## Verify before claiming it works

```bash
.venv/bin/python -m compileall -q src/
.venv/bin/python tests/run_all.py                  # all checks, no keys/audio needed
cd src && ../.venv/bin/python -m lura.modules.overlay
.venv/bin/lura selftest                            # devices, keys, real brain + TTS call
systemctl --user restart lura.service && journalctl --user -u lura -n 50
```

State what you ran and what it printed; say plainly what wasn't verified
(anything needing a real key, audio device or the desktop session).

## Mohamed's existing install after upgrading to 2.0

His old unit (`ExecStart=/var/www/html/lura/.venv/bin/lura run`) keeps working; keys,
settings (migrated), mcp.json and the Vosk model stay where they are. Re-run
`./install.sh` once to get the new unit (`--no-browser`) and `anthropic`. His `github`
MCP server is a custom connector → locked until Pro is activated.

## Git history worth knowing

- The real face (MediaPipe mesh) and the OpenRouter/Deepgram work lived on
  `feat/openrouter-voice-provider`; merged into `main` in `916269c`
  (Oct 2026). Conflicts resolved: video files from `main`, `openrouter.py` from the
  branch.
- `claude/elegant-gates-n1ou55` = promo-video work, merged via PR #1. If the face
  ever looks like the old 67/153-vertex wireframe, check which branch is checked
  out (`git branch`, `git reflog`).

## Promo video (`video/`)

Separate Node project (Playwright + ffmpeg): `make.sh`, `ad.mjs` (9:16 ad),
`combo.mjs` (9:16 + 16:9, dark "Neon HUD" / light "Daylight" themes),
`face.js` + `face_mesh.js` (same MediaPipe head as the overlay, canvas version),
`voice.py` (narration via any Gradio TTS app, e.g. Habibi-TTS on Colab, MSA by
default), `transcribe.py` (verify narration via OpenRouter). Outputs go to
`video/out/` (ignored). Details in `video/README.md`. Don't mention Linux in
promo copy, and don't show the GitHub repo link in videos.

## Environment notes for Claude sessions

- In a Cowork session the repo is mounted at `$HOME/mnt/lura`; `systemctl --user`
  and the desktop aren't reachable there — ask Mohamed to restart the service.
  The `.venv` is built for his machine and won't import there; use
  `pip install --target` into a scratch dir for checks, stub `sounddevice`.
- Deleting files in the mounted folder needs his approval; without it git leaves
  `.git/*.lock` / `tmp_obj_*` behind and plain `git checkout` can't replace files.
  Clean stale locks after any git write.
- Pushing over SSH only works from his machine (`git push origin main`).

## Gemini TTS for voice-overs

Lura's stored Gemini key (`~/.config/lura/keys.json` → `"gemini"`) is also the key used to generate
voice-overs with `gemini-3.8-flash-tts` (voice `Mako`). The full reference script and transcript format
live in `/var/www/html/old/mindspace/CLAUDE.md` → "Voice-over generation (Gemini TTS)".
