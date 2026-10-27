# AGENTS.md — working on Lura

A voice-first desktop assistant for Linux, macOS and Windows. Say a word, it
listens, it answers out loud. **Read this before changing anything.** Most of it
is facts established by testing, not by reading docs. Several contradict what
the docs imply, and re-deriving each one costs an hour.

Structure and layering: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.
Providers: **[docs/PROVIDERS.md](docs/PROVIDERS.md)**. Rules that already apply:
keep files under ~500 lines, validate at boundaries, don't add a dependency for
what a few lines do, and never import another module's internals
(`tests/test_architecture.py` fails if you do).

---

## Pipeline vs realtime are not symmetrical

| | pipeline (`assistant/application.py`) | realtime (`voice/infrastructure/gemini_live.py`) |
|---|---|---|
| Transport | HTTP: brain stream + TTS calls | One WebSocket, audio both ways |
| End of turn | **We** decide, locally | Server decides |
| Brain | any of 4 providers | Gemini only |

Do not unify them. They differ in who owns turn detection, and a shared
interface would hide exactly that.

## Verified facts — do not re-litigate

### Auth
- **There is no consumer-Google-account path to the Gemini APIs.** "Just log in with
  Google" is not possible honestly. Don't build a scraper.
- **Gemini keys created before September 2026 are rejected** (old "standard" keys).
  `lura login` says so; keep it there.
- OpenRouter `GET /models` needs no auth; `GET /key` returns `label`/`usage`/`limit`,
  which separates "invalid key" from "out of credit".
- Keys are tested before they are saved (dashboard and `lura login`).

### Voice
- Gemini 3.8 Flash TTS exists both directly (`gemini-3.8-flash-tts`) and on OpenRouter
  (`google/gemini-3.8-flash-tts`), with the same 30 voices, plus `…-flash-lite-tts`.
- Audio straight into the brain works for Gemini models (direct and via OpenRouter):
  no transcription step. Claude and GPT-5.x cannot hear, so `ChatTranscriber`
  (Gemini 3.8 Flash) writes the turn down first.
- **Never transcribe with the local Vosk model.** It is `small-en-us`, English only:
  Arabic comes back as confident nonsense that the brain then answers.
- The English-only voices from 1.x (Deepgram) read Arabic as noise. Gemini TTS speaks
  Egyptian Arabic, which is why `language: ar` is now a first-class setting.

### Wake word
- Matched **locally** by Vosk with a two-entry grammar (`[word, "[unk]"]`).
- **The word must be in the model's vocabulary.** It is compiled into the FST; Vosk
  logs `Ignoring word missing in vocabulary` once and then never fires.
- `lura` is not in the vocabulary; `wake.py` listens for `laura` and accepts both.
  `gemini` and `laura` were tested ✅. Always test a negative too.
- The model (~40 MB) downloads on first run into the data dir.

### Audio
- Gemini Live: input **16 kHz**, output **24 kHz**, as separate streams.
- The mic is gated while Lura speaks in realtime mode. Without it she hears herself and
  interrupts herself in a loop. The cost is no barge-in.
- Pipeline end-of-turn: the noise floor is **measured** over the first ~0.5 s of each
  utterance, never hardcoded (a quiet room vs a laptop fan differ by >10×).
- One output stream per reply, written in 50 ms slices. Opening per sentence clicks on
  PipeWire and clips sentence tails.

### Desktop / packaging
- Qt must run on the main thread (macOS). The assistant runs on a worker thread.
- **Qt aborts the whole process** if its platform plugin cannot load (missing
  `libxcb-cursor0` etc. on X11). `qt_backend.available()` checks those libraries first.
- A frozen Python cannot import the system's PyGObject, so packaged Linux builds use Qt;
  running from source on Linux keeps the tuned GTK face.
- PyInstaller executables are `lura` (console CLI) and `LuraApp` (windowed daemon).
  Not "Lura": macOS and Windows filesystems are case-insensitive.
- Windows ships `npx` as `npx.cmd`; `Popen` without a shell won't find it unless resolved.

### Service (Linux)
- Exit **78** (`EX_CONFIG`) means a human must fix something; `RestartPreventExitStatus=78`.
  The daemon itself no longer exits for a missing key: it waits, and the dashboard fixes it.
- The unit needs `Environment=XDG_RUNTIME_DIR=%t` or sounddevice sees no devices.
- `WantedBy=graphical-session.target`: it needs the audio session, not just a login.

## How to verify a change

```bash
python -m compileall -q src/
python tests/run_all.py                 # architecture, guard, domain, integration (mock servers), dashboard
cd src && python -m lura.modules.overlay # renders every face state headlessly
lura --help
lura selftest                           # devices, keys, real brain + TTS round trip
```

`selftest` surfaces API errors **verbatim**: a retired preview model must read as
"model not found", not as a broken app. No pytest, on purpose: each test is a plain
script.

## Things that will tempt you, and shouldn't

- **Don't hold a Live session open between wake words.** Sessions are capped.
- **Don't move keys into the login keyring.** It may be locked when the session starts.
- **Don't rename the Linux config/data dirs** without migrating: they hold keys and the model.
- **Don't pin a preview model as if stable.** Keep it a setting with a legible error.
- **Don't send every MCP schema every turn** in pipeline mode: `find_tools` exists because
  ~12k tokens of schemas cost 2.7 s per turn.
- **Don't add an OAuth "connect with…" flow for connectors.** URL + token or mcp.json only.

## Reporting back

State what you ran and what it printed. If a step was not verified (anything needing
a real key, a microphone or a desktop session), say so plainly.
