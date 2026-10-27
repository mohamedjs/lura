# CLAUDE.md — Lura

Lura (لورا) is Mohamed's voice assistant for his Linux desktop. Say "Lura", hear a
chime, talk; she answers out loud in Arabic or English and can act on the machine
(open apps, run commands, system briefing, MCP tools). It runs as a
`systemd --user` service from `/var/www/html/lura`. A floating, transparent
overlay window shows an animated 3D face while she listens, thinks and speaks.

Repo: `git@github.com:mohamedjs/lura.git`, default branch `main`.

**Read these too:** `AGENTS.md` (hard-won facts: auth, wake word, audio, service —
read before changing code), `README.md` (install + reference), `USAGE.md`
(day-to-day), `video/README.md` (promo video renderer). Where they disagree with
this file, this file is newer — see "Stale docs" below.

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

## Layout

```
src/lura/
  app.py         CLI (run, login, logout, config, models, selftest, say, briefing, mcp)
  config.py      Settings dataclass → ~/.config/lura/settings.json; keys → keys.json (0600)
  wake.py        offline wake word (Vosk), chime, utterance recorder  [no network]
  live.py        provider "gemini": Gemini Live, full-duplex streaming audio + tools
  openrouter.py  provider "openrouter": turn-based, audio straight into the chat model, TTS back
  tools.py       built-in tools + command guard (check_command)
  mcp_client.py  MCP client (stdio JSON-RPC), config ~/.config/lura/mcp.json
  briefing.py    Arabic morning briefing (weather, CPU temp, RAM/load, ping, last GitHub commit)
  overlay.py     GTK3 + cairo floating face (top-right, every monitor)
  face_mp.py     MediaPipe canonical face mesh, 468 verts / 898 tris (used by overlay)
  face_mesh.py   older 153-vertex mesh — no longer used by overlay.py
test_command_guard.py   assert-based test for the destructive-command guard
install.sh / lura.service   idempotent installer / systemd user unit
video/          Node + Playwright promo-video renderer (separate from the app)
```

Paths: config `~/.config/lura/`, data `~/.local/share/lura/` (Vosk model,
`last_briefing` flag). Venv `.venv/` is an **editable install**
(`pip install -e .`), so editing `src/` + restarting the service is enough.

## Current behaviour (as of Oct 2026)

- **Default provider: `openrouter`** with `google/gemini-3.8-flash`
  (`openrouter_provider_order` = google-vertex, google-ai-studio;
  `openrouter_reasoning_effort` = minimal). No transcription step: the recorded
  WAV goes to the chat model as `input_audio`.
- **TTS:** `deepgram/flux-tts:free` (voice `flux-alexis-en`), fallback
  `deepgram/aura-2` (`aura-2-thalia-en`). Fallback only on errors a different
  model could survive — never on HTTP 400.
- **`reply_language` = "English"** forces one reply language (the English TTS
  voices read Arabic as noise). The rule is repeated at the end of the machine
  context on purpose.
- **Gemini Live** (`provider gemini`): `gemini-2.5-flash-native-audio-preview-12-2025`,
  voice `Puck`; 16 kHz in / 24 kHz out; mic gated while speaking.
- **Wake word `lura`**: not in the Vosk English vocabulary, so `wake.py` maps it
  to `laura` and accepts both.
- **Tools:** `run_command`, `open_application`, `list_applications`,
  `get_system_briefing`, `end_session`, plus MCP. On OpenRouter, MCP schemas are
  *not* sent every turn (~12k tokens, 2.7 s latency); the model pulls them in via
  `find_tools` only when needed.
- **Command guard** (`tools.check_command`): destructive commands (rm, overwrite,
  `$(…)`, backticks, `${…}`…) are refused with a spoken explanation. Test:
  `python3 test_command_guard.py` → `Command guard OK (22 blocked, 15 allowed)`.
- **Briefing** runs once per boot, in Arabic; GitHub line uses the MCP server
  named exactly `github` and searches `user:mohamedjs`.

## The overlay face (`overlay.py`)

- Look Mohamed approved: the promo video's face — MediaPipe mesh as **indigo
  wireframe over translucent lavender facets**, teal blinking eyes, teal lips and
  jaw lines, dotted cyan orbit ring, cyan light band sweeping down.
  **Fully transparent background** — no disc, halo or rings behind it (he asked
  for "only the face").
- Palette constants: `INDIGO`, `LAVENDER`, `TEAL`, `CYAN` near the top of the
  renderer; brightened so it reads on dark *and* light wallpapers.
- States via `StateBlender`: listening (calm, dimmer), thinking (more turn,
  faster band), speaking (jaw opens with `OverlayState.amplitude`, mouth glows).
- `OVERLAY_SIZE = 260`, 30 fps (`GLib.timeout_add(33, …)`), one window per
  monitor, draggable.
- Performance: ~9 ms draw + ~2 ms conversion per frame. Frames go to GdkPixbuf as
  raw RGBA via `_surface_rgba()` (un-premultiply BGRA → RGBA). **Don't go back to
  a PNG round-trip** — it cost ~15 ms/frame for this detailed mesh. Keep cairo
  calls batched by alpha bucket (`_bucket_stroke`) and iterate `.tolist()`, not
  numpy scalars.
- Self-check (no display needed): `cd src && python3 -m lura.overlay` →
  `overlay self-check OK`.

## Verify before claiming it works

```bash
.venv/bin/python -m compileall -q src/
cd src && ../.venv/bin/python -m lura.overlay      # overlay self-check
.venv/bin/python test_command_guard.py
.venv/bin/lura selftest                           # devices, key, model, real API call
systemctl --user restart lura.service && journalctl --user -u lura -n 50
```

State what you ran and what it printed; say plainly what wasn't verified
(anything needing a real key, audio device or the desktop session).

## Stale docs (fix when touching them)

- `README.md` settings table still shows old defaults (`provider gemini`,
  `openrouter_model google/gemini-2.5-flash`, `openrouter_stt_model`,
  `gpt-4o-mini-tts`) and says MCP/tools don't work on OpenRouter. All outdated —
  see "Current behaviour".
- `AGENTS.md` says `lura` can't be a wake word (now mapped to `laura`) and that
  the OpenRouter path transcribes via `/audio/transcriptions` (now audio goes
  straight to the chat model). `openrouter_stt_model` remains in config but the
  app path no longer uses it.

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
