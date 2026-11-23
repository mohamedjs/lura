# CLAUDE.md — Lura

Lura (لورا) is a voice-first desktop assistant: Linux (.deb), macOS (Homebrew cask)
and Windows (installer .exe). Say "Lura", hear a chime, talk; she answers out loud in
Arabic or English and can act on the machine (open apps/URLs, find files, run guarded
commands, briefing, MCP connectors). Any of Gemini / OpenAI / Anthropic / OpenRouter
can be the brain; the voice is Gemini 3.8 Flash TTS (direct or via OpenRouter) by default, or
any OpenRouter speech model (e.g. `heygen/heygen-voice-1-turbo:free`) with its own voices.
A floating transparent hologram face shows on every monitor; a local dashboard
(calm, tabbed, AR/EN) configures everything. Free and open source (MIT), nothing
locked; the maintainer approves every PR (CODEOWNERS). On Mohamed's machine it runs
as a `systemd --user` service — since 2.1 from the installed .deb (`/opt/lura/LuraApp`),
so source changes reach him only through a release.

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
    connectors/ MCP stdio + Streamable HTTP, mcp.json, 3 built-ins (chrome, web, files)
    memory/     history.db (SQLite, LIKE search), memory.md, running summary → summaries/<date>.md
    skills/     <config>/skills/<name>/SKILL.md + skills.json; prompt lists them, use_skill reads one
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
mcp.json, dashboard.url), data `~/.local/share/lura/` (vosk-model,
last_briefing, lura.log). macOS/Windows paths: README → Where things live. Venv `.venv/`
is an **editable install**, so editing `src/` + restarting the service is enough.

## Current behaviour (2.4.3, Nov 2026)

- **Default:** mode `pipeline`, brain `openrouter` / `google/gemini-3.8-flash`, voice
  `auto` (Gemini direct if a Gemini key exists, else OpenRouter), voice `Kore` (female) /
  `Puck` (male), `reasoning_effort` minimal, language `en`.
- **Rule:** any provider thinks; hearing + speaking need a Gemini or OpenRouter key.
  Gemini brains hear the WAV directly; Claude/GPT get a Gemini 3.8 Flash transcript.
- **OpenRouter speech models (2.4):** with voice provider `openrouter`, `openrouter_voice_model`
  picks the model and `openrouter_voice` its voice; the dashboard lists models + voices from
  OpenRouter's public `/models?output_modalities=speech` (`supported_voices`, cached 1 h,
  `GET /api/voice/models`). Non-Gemini models get no style direction and no Lite fallback;
  `voice_gender` is then the user's choice (Arabic forms, the bust's figure).
- **ElevenLabs voice:** `voice/infrastructure/elevenlabs.py`. `KEY_PROVIDERS` = the brains +
  `elevenlabs` (stored, checked with `GET /v2/voices`, never a brain: `PROVIDER_INFO["brain"]=False`);
  it is in `VOICE_PROVIDERS` but `auto` never picks it — saving its key switches `auto` to it.
  Speaks via `POST /v1/text-to-speech/{voice}/stream?output_format=pcm_24000` (`xi-api-key`,
  `elevenlabs_model`, default `eleven_flash_v2_5`), hears via Scribe `scribe_v2` multipart
  (`shared/http.request_json(raw=…)`, `tag_audio_events=false`). Voices from `/v2/voices` with
  their gender label (`/api/voice/elevenlabs`, cached 1 h); `elevenlabs_voice_female/_male`
  keep one per gender, empty → the account's first voice of that gender (what the dashboard
  highlights), George if the list fails. With a Gemini/OpenRouter key too, `_voice_providers`
  = [elevenlabs, that] and the fallbacks use `any_error` (used-up credit is a 401).
- **Offline voice (Piper):** `voice/infrastructure/piper.py`. `synthesizer()` wraps every
  chain in `_OfflineFallback` — any failure before audio (429, 402, 401, no network) →
  Piper, one process per sentence, `ar_JO-kareem-medium` if the sentence has Arabic script
  else `en_US-lessac-medium`, `--output_raw` resampled to 24 kHz. Dashboard preview and
  selftest pass `offline=False` so they test the real voice. Program: `piper/` next to the
  executable (release.yml bundles it for Linux + Windows and runs one sentence), else
  `<data>/piper/piper/` (downloaded by `ensure()` at daemon start and by install.sh; macOS
  signs it ad hoc), else `piper-tts` on PATH — never `piper` (apt's is a mouse tool).
  Voices are always downloaded, not shipped (dataset licences). A free OpenRouter speech
  model that hits its daily limit falls back to Gemini 3.8 Flash TTS until `X-RateLimit-Reset`.
  - **Offline Arabic voice (tiny-voca-arabic):** `voice/infrastructure/offline.py` (`OfflineTTS`)
    routes sentences containing Arabic script to `voice/infrastructure/arabic_voice.py`
    (`TinyVocaArabicTTS`, MSA female ONNX via `onnxruntime`, vendored front end + CATT in
    `voice/infrastructure/tiny_voca/`, Apache-2.0; ~114 MB downloaded once into
    `<data>/arabic-voice/` with pinned SHA-256) and keeps Piper for English and as Arabic
    backup on errors or when unavailable; Latin-script words in Arabic sentences are skipped.
- **2.7 — offline Arabic voice + tashkeel:** tiny-voca-arabic (above) speaks Arabic when the
  online voices fail. `tiny_voca/arabic_frontend.merge_tashkeel` keeps the brain's own marks letter
  by letter (CATT only fills bare letters) and gives a bare last letter a sukun, not CATT's MSA case
  ending (Egyptian reading). The prompt's closing language rule asks for full tashkeel, and
  `assistant/domain.with_tashkeel_note` adds `[Reply with full tashkeel on every Arabic letter.]` to
  the newest user turn of each request (not stored): with old untashkeeled replies in the history,
  deepseek-v4-flash copied them (3% marked) until the note (81–98%). `LURA_OFFLINE_VOICE=1` makes
  `_OfflineFallback` always speak offline, to hear the offline voices.
- **2.6 — Air gestures v4 (`air/engine.py`):** cursor follows the index knuckle (INDEX_MCP), not the
  tip, with pointer acceleration (`accel_gain`: ×0.6 slow … ×1.6 fast); finger-gun click = cocked
  thumb (`thumb_reach` > 0.65 for 0.2 s) tapped down (< 0.5) with the knuckle still (`THUMB_STILL`),
  held `THUMB_DRAG_HOLD` 0.35 s = drag; V + thumb tap = `right_click`; the v3 pinch still clicks;
  flick scroll glides with momentum (`GLIDE_*`, scroll Commands with `text="glide"`, coalesced in the
  controller queue), a fist stops it. `air_thumb_click` (live) switches the thumb tap off. Thresholds
  are not measured on camera yet (the session recordings are not on this machine).
- **2.4.3 (from Mohamed's logs):** `stream_sse` gives up (408) after `timeout` s of keep-alives
  only (a stalled OpenRouter stream hung her 8+ min); MCP `CALL_TIMEOUT` 30 s (a Chrome
  screenshot of a background tab hung); `MAX_TOOL_ROUNDS` 10 (Docs typing ran out at 6);
  no Lite TTS retry on 402 (credit is per account); `_OfflineFallback` skips the online voice
  120 s after a 402/429/no network; non-Gemini OpenRouter speech models fall back to Gemini on
  any error (heygen 400s on Arabic); `check_command` refuses anything touching Lura's config
  folder / keys.json / mcp.json (she once ran `cat ~/.config/lura/*`).
- **Streaming:** brain text streams into `SpeechPlayer` → first sentence synthesised
  while the rest is written. TTS falls back to the Lite TTS model on retryable errors only.
- **2.3 — faster and interruptible (pipeline):**
  - TTS audio streams too (`SpeechSynthesizer.stream`; `GeminiTTS` via the Interactions API;
    OpenRouter is read as it arrives but buffers server-side). A refused streaming request
    (400/404) latches whole clips for the session (`tts.StreamSupport`, held by
    `VoiceService`); failures before any audio fall back, after some audio they are raised.
    The playback queue is bounded by seconds of audio (30 s), not items.
  - `SpeechPlayer.interrupt()` stops within one 50 ms slice and **aborts** the device buffer;
    `wait()` joins only playback, so a stalled TTS call never keeps her deaf. `heard` =
    pieces whose playback started; on a cut, history, memory row and subtitle keep only
    that + " …", no further tool round runs, and the conversation goes back to listening
    (`PipelineConversation._cut_short`). A briefing that is interrupted keeps listening.
  - Tap the face (Qt + GTK: press without moving > 4 px, fired after the double-click
    interval): idle → talk; in a conversation → `INTERRUPT_REQUESTED` → `interrupt()`.
  - Voice barge-in (`barge_in`, default on, live): `voice/infrastructure/barge_in.py` opens
    the mic when a reply starts, probes her echo for 1.2 s, and on speakers stands down per
    reply (AGENTS.md → Audio has the measurements). On a barge-in the open mic and the last
    3 s go to `record_utterance(carry=…)`, so no syllable is lost. `SpeechPlayer.wait()`
    waits (≤0.3 s) for the listener to let the mic go before the recorder opens it.
  - A cut reply is rewritten with `brain.retext()`: the neutral text **and** the provider's
    replayed copy (`Message.native`), keeping thinking/signatures/tool calls untouched —
    otherwise the brain is still sent the whole answer.
- **Anthropic:** official SDK, default `claude-opus-5-5`, `output_config.effort`,
  `fallbacks: "default"`; turns replayed verbatim.
- **Realtime:** Gemini Live `gemini-2.5-flash-native-audio-preview-12-2025`, mic gated.
- **Chrome (2.4.1):** `connectors/infrastructure/chrome.endpoint()` finds the user's Chrome in the
  usual profile folders, in any `--user-data-dir`/`--remote-debugging-port` on a running Chrome's
  command line (Mohamed's `~/.local/bin/google-chrome` starts `~/.config/google-chrome-debug` on
  9222), then port 9222, via `/json/version`. The built-in `chrome` connector is handed that
  address (`--wsEndpoint`), not `--autoConnect` (default profile only). The dashboard's Chrome
  "Set up" button opens `chrome://inspect/#remote-debugging` in the user's Chrome.
- **Connectors:** built-ins `chrome` (chrome-devtools-mcp `--wsEndpoint`), `playwright`
  (@playwright/mcp `--cdp-endpoint` from Chrome's `DevToolsActivePort`), `web`
  (mcp-fetch-server), `files` (server-filesystem ~) via `npx`; anyone can add more.
  Both browser ones drive the user's **own** running Chrome (profile, sign-ins), never a
  fresh guest one: the user switches on `chrome://inspect/#remote-debugging` once; a call
  that finds Chrome closed starts it and re-attaches (`connectors/infrastructure/chrome.py`).
  Pipeline mode pulls MCP schemas only via `find_tools`.
- **Dashboard key fields:** an `on…` handler must never return `false` (it cancels the
  keystroke — the old `e.key === "Enter" && …` blocked all typing and pasting). Key rows
  are built once and updated, so polls never wipe what is being typed.
- **TLS in frozen builds:** `shared/http.use_bundled_certificates()` points SSL_CERT_FILE at
  certifi when the OS bundle is unreachable (the macOS build could not verify any HTTPS).
- **Settings migration:** a 1.x settings.json (`provider`, `openrouter_model`, …) is
  converted on load (`settings/domain.py: migrate_legacy`).
- **Command guard** also blocks Windows/macOS destructive commands. Test:
  `python3 tests/test_command_guard.py` → `command guard OK (32 blocked, 19 allowed)`.
- **Dashboard window:** native — pywebview (WebView2 / WKWebView) on Windows/macOS, Qt
  WebEngine on Linux (bundled in the Linux build only), in a separate `lura window` process.
  If that process dies, it is reaped and the page opens in a browser. The Linux bundle
  leaves libstdc++/libgbm/glib & co. to the host (`HOST_LIBS` in lura.spec, #6).
- **Model picker:** `/api/catalog` lists brain-capable models (chat + tools) of every keyed
  provider (`brain/domain.py: brain_candidate`). Modes: "My model" / "Gemini Realtime".
- **Restart button** appears after settings/key changes (except live ones: subtitles,
  ui_language); daemon exits 75 under systemd, else re-execs. Dashboard token is stable
  (`dashboard.token`) so the open window reconnects.
- **Subtitles** under the face (`overlay/captions.py`, drawn by Qt/Pango so Arabic shapes) in a
  small dark-glass box with a glowing border, centred under Lura, `caption_width(size)` =
  1.3× her size (260–480 px), text in FSI/PDI isolates so Arabic after "You:" keeps its
  direction. GTK lays it out from the face's position/size only — measuring the window
  (which includes the offset) grew it every frame into a full-screen banner:
  what you said + Lura's streamed reply; toggle live from dashboard, tray, or right-click on
  the face. When the brain hears audio directly, a parallel transcription fills "You".
- **Microphone & speaker card:** pick devices by name (`/api/audio/devices`), test mic
  (level meter) and speaker; "pause before Lura answers" = `silence_ms` (500–2500);
  `follow_up_seconds` (default 8, replaced `idle_timeout`) = how long she waits for your
  next sentence. Device/turn settings are live (no restart); the wake listener is rebuilt
  when the mic changes. `tests/test_audio.py` fakes a 48 kHz-stereo-only USB mic.
- **Memory:** one endless thread. Each conversation starts with memory.md + running summary
  + the newest turns that fit 60 % of `context_budget_tokens` (default 200k). When they no
  longer fit, the older part is summarised by the current brain after the conversation (background) and
  written to `<data>/memory/summaries/<date>.md`. Tools: remember / forget / search_history.
  Only text is stored; for brains that hear audio the caption transcription now runs
  whenever memory is on. Anthropic gets cache breakpoints on system + newest turn.
- **Skills** (Free): paste / zip / https URL in the dashboard; listed in the prompt, body via
  `use_skill`, files via `read_skill_file` (confined to the folder); scripts only through
  the guarded `run_command`.
- **Daily briefing** (`daily_briefing`: `first_wake` default / `boot` / `off`; a 2.2
  `briefing_on_boot: false` migrates to `off`): the first time she is called each day
  she picks the connected work services (`tools/domain.py: briefing_sources` — code,
  tasks, calendar, notes, messages, or "other" custom ones; read tools only, ≤8 each,
  ≤28 total), pre-loads those tools into the conversation, greets while she gathers
  yesterday + today, sums up, suggests where to start, ends on a question and keeps
  listening (`run(..., then_listen=True)`). No work services → she says they can be
  connected.
- **Updates from inside the app** (`modules/updates/`): the daemon asks GitHub's
  `releases/latest` a minute after start and every 6 h (`check_updates`); the dashboard
  shows a banner with "Update now". The file for this install (Setup.exe / macOS zip /
  .deb / tar.gz) is downloaded only from this repo's release URLs and checked against
  the release's `SHA256SUMS.txt`, then: Windows runs the Inno installer silently (lura.iss
  closes the running Lura and starts the new one), macOS swaps Lura.app after exit and
  reopens it, .deb goes through `pkexec apt-get install` then a restart, tar.gz is
  swapped in place. Installs from source only get the notice. Versions before 2.2.1
  have no updater, so 2.2.1 must be installed by hand once.
- **macOS install:** `Casks/lura.rb` in this repo (no separate tap) installs the latest
  release from the fixed-name zips `Lura-macos-<arch>.zip` and clears the quarantine
  flag (not notarised). Landing page `#mac-first` explains "Lura" Not Opened → Open Anyway.
- **2.2 (Oct 2026):**
  - Voice falls back Gemini ⇄ OpenRouter on 429/5xx/timeouts (hearing gives up after
    20 s) and skips an exhausted provider for as long as its "retry in 4h…" says.
  - System prompt built in `assistant/domain.py: system_prompt` (date/time, imperfect
    transcripts, ask-don't-guess, `[[quiet]]` for noise, memory/tool use, rename note).
    The stored 1.x default persona is ignored; a custom one is appended.
  - Noise: end of turn measures the 150–4000 Hz band (`audio.speech_level`); the
    transcriber returns nothing for background/unclear audio; `[[quiet]]` turns are
    dropped from history and memory; 2 in a row close the session.
  - Live captions (`LiveCaption`): partial transcription every ~1.5 s of speech,
    display only, max 8 per turn (`live_captions` setting).
  - Overlay follows screens added later (screenAdded / monitor-added), renders once
    per frame for all screens (half rate idle), sticky on X11 and all Spaces on macOS
    (`overlay/workspaces.py`).
  - A dead assistant/face loop exits 1 (systemd restarts it); tray Quit is logged.
  - Wake word checked against the Vosk vocabulary on save; renaming moves it along.
  - Language: `settings.language` is only the default; she answers in the language the
    user speaks (Arabic → Egyptian). `user_gender` (male/female/unset) sets how she
    addresses the user in Arabic; her own forms follow `voice_gender`.
  - Tool harness: the prompt lists connected MCP servers with a sample of their tool
    names (`ToolService.services`) and how to reach them (find_tools, retry, fallback),
    plus when to ask before acting (sending, posting, buying, deleting).

## The overlay face (`modules/overlay/`)

- Look Mohamed approved: the promo video's face — MediaPipe mesh as **indigo
  wireframe over translucent lavender facets**, teal blinking eyes, teal lips and
  jaw lines, cyan light band sweeping down. (The dotted orbit ring was removed
  at Mohamed's request, Nov 2026.)
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
- **Looks (since 2.4):** `overlay/looks.py` — styles `face` / `orb` / `bust` / `ring`,
  themes `lura` (default, the approved palette) `aurora` `rose` `gold` `ice` or `custom`
  (`overlay_colors`: wire/fill/accent/glow hex), `overlay_scale` 0.6–2.0. All live
  (`OverlayState.look`, read every frame); dashboard "Look" tab + Qt tray submenu. The
  bust (Mohamed's reference images): **pre-rendered in Blender**, not drawn as a mesh —
  cairo triangles always showed facets and rings at eyes/mouth/ears. `tools/holo_bust.py`
  builds the MPFB (MakeHuman, CC0) base mesh (female and male), cuts head-to-chest with
  statue-like arm cuts, subdivides, and renders in Cycles (GPU) with a grey hologram
  material (soft light, Fresnel rim, contour lines; only the nearest surface shows, or
  the mouth/eyeballs stack into bright patches) at 9 turns × 3 mouth openings, then packs
  luma + alpha into `overlay/holo/{female,male}.npz` (~1.7 MB each, shipped via
  pyproject package-data + lura.spec datas). At run time `_holo_frames` tints them with
  the theme once, `_figure` cross-fades the two nearest turns/mouths; around it: HUD base
  rings + sound-wave bars, slanted orbit rings with nodes, diamond core, sparkle points
  sampled from each frame, drift, ear waveforms, light streaks rising off the shoulders. The figure follows `voice_gender`
  (`Look.figure`). ~6 ms/frame. The face style is untouched.
  **Tool badges:** `ConnectorService.call` publishes `events.TOOL_USED`; `app.py` calls
  `OverlayState.set_tool(connector)`; every style draws a flowing line to a circle with
  the connector's first two letters ("Gi", "No") for 5 s (`TOOL_LINGER`).
  **Dragging (GTK):** by hand in press/motion handlers — window managers ignore
  `begin_move_drag` on DOCK windows, so the face could not be moved.
  **Click-through:** only cells with drawn pixels take clicks (`render.hit_rects`, gaps up
  to 4 cells filled so the whole figure is one handle) —
  GTK via XShape (`workspaces.x11_input_shape`; the venv's PyGObject has no cairo
  bindings), Qt via `setMask` grown one cell so the glow isn't clipped. Both backends render **once per frame for
  all monitors** — per-window rendering froze GTK on two screens at 150 %.
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
MCP server works as a normal connector (no plan any more).

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
