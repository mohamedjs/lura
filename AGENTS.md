# AGENTS.md — working on Lura

A wake-word voice assistant that runs as a `systemd --user` service on Linux.
Say a word, it listens, it answers out loud.

**Read this before changing anything.** Most of it is facts that were
established by testing, not by reading docs — several of them contradict what
the docs imply, and re-deriving them costs an hour each.

---

## Layout

```
/var/www/html/lura/
├── src/lura/
│   ├── config.py       Settings dataclass, per-provider keys (0600 JSON)
│   ├── wake.py         Vosk wake word, chime, utterance recorder  [no network]
│   ├── live.py         Gemini Live: full-duplex streaming audio
│   ├── openrouter.py   OpenRouter: turn-based STT → chat → TTS
│   └── app.py          CLI + provider routing + selftest
├── lura.service        systemd user unit
├── install.sh          idempotent installer
├── README.md           install + reference
└── USAGE.md            day-to-day guide
```

Rules that already apply here: keep files under 500 lines, validate at
boundaries, don't add a dependency for what a few lines do.

---

## The two providers are not symmetrical

| | `gemini` (`live.py`) | `openrouter` (`openrouter.py`) |
|---|---|---|
| Transport | One WebSocket, audio both ways | Three HTTP calls per turn |
| End of turn | Server decides | **We** decide, locally |
| SDK | `google-genai` | `urllib`, no SDK |

Do not try to unify them behind one abstraction. They differ in the thing that
matters — who owns turn detection — and a shared interface would hide it.

---

## Verified facts — do not re-litigate

These were each established by running something. The test is named so you can
re-run it if you doubt it.

### Auth

- **There is no consumer-Google-account path to the Gemini Live API.** OAuth for
  the Gemini API needs a Cloud project *plus* desktop OAuth client credentials —
  more setup than a key, not less — and is not documented for Live. Gemini CLI's
  "Login with Google" reaches Code Assist, not Live. Automating
  gemini.google.com is ToS-violating and breaks constantly. **If a user asks for
  "just log in with Google", the honest answer is no.** Don't build a scraper.
- **Gemini keys created before September 2026 are rejected.** They are the old
  "standard" type; new AI Studio keys default to "auth" keys. A user pasting an
  old key from a note gets an error that looks like a code bug. `cmd_login` says
  this out loud — keep it there.
- OpenRouter `GET /api/v1/models` needs **no** auth. `GET /api/v1/key` exists and
  returns `label` / `usage` / `limit`, which is what separates "invalid key"
  from "out of credit" — two very different fixes for the user.

### Wake word

- The wake word is matched **locally** by Vosk with a two-entry grammar
  (`["<word>", "[unk]"]`). Open-vocabulary transcription of a room all day
  would cost far more CPU and accept far more rubbish.
- **The word must be in the model's vocabulary.** It is compiled into the FST,
  so there is no `words.txt` to grep. Vosk logs
  `Ignoring word missing in vocabulary` and then simply never fires.
- Tested: `gemini` ✅, `laura` ✅, **`lura` ❌ — not in vocabulary.** That is why
  the app is named Lura but the wake word is not.
- To test a candidate word, synthesise it and feed it through:

  ```python
  # pip install gTTS; ffmpeg to 16k mono s16le; then:
  r = KaldiRecognizer(model, 16000, json.dumps([word, "[unk]"]))
  # feed 8000-byte blocks, check Result/PartialResult/FinalResult
  ```

  Always check a negative too ("the weather is nice today" must *not* fire).

### Audio

- Gemini Live: input **16 kHz** mono s16le, output **24 kHz** mono s16le. Two
  rates. Separate streams — do not use one duplex stream and resample.
- The mic is muted while the assistant speaks (`gate_mic_while_speaking`).
  Without it the mic hears the assistant, barge-in detection fires, and it
  interrupts itself in a loop. The cost is no barge-in. This is a deliberate
  trade, marked `ponytail:` in `live.py`.
- OpenRouter path owns end-of-turn detection. The noise floor is **measured**
  over the first ~0.5s of each utterance, never hardcoded: a quiet room and a
  laptop fan differ by more than 10×. `silence_factor` / `silence_ms` are the
  calibration knobs. Leave them adjustable.
- **Transcription for the OpenRouter path must not use the local Vosk model.**
  It is `small-en-us` — English only. Arabic would return confident nonsense
  that the LLM then answers, with nothing to show the user why. Use OpenRouter's
  `/audio/transcriptions`.

### Service

- Exit **78** (`EX_CONFIG`) means "no key / no wake model — a human must fix
  this", and `RestartPreventExitStatus=78` stops systemd respawning forever.
  Any new unfixable-by-retry condition should use 78 too.
- The unit needs `Environment=XDG_RUNTIME_DIR=%t` or `sounddevice` sees no
  devices at all — even though a terminal run works fine.
- `WantedBy=graphical-session.target`, not `default.target`: it needs the audio
  session, not just a login.
- `ProtectHome=read-only` plus `ReadWritePaths` for the config and data dirs.
  **Those directories must exist** or the unit fails to set up its mount
  namespace.

### Moving the project

The venv bakes in absolute paths. Relocating the directory means **rebuilding
the venv** (`python3 -m venv .venv && pip install -e .`) and rewriting
`ExecStart`. `install.sh` rewrites the unit's paths from its own location, so it
is safe to re-run from anywhere — keep that property.

---

## How to verify a change

There is no test framework here, on purpose. The check that earns its keep is:

```bash
.venv/bin/lura selftest
```

It walks every layer in order — devices, mic level, speaker, wake model, key,
model existence, real API round-trip — and stops at the first real failure. It
is the equivalent of rendering the page rather than reasoning about the CSS.

**Rules for it:** surface API errors **verbatim**. A retired preview model must
read as "model not found", not as a broken app. Never swallow an exception into
a generic message.

For code that does not touch audio or the network, a small `assert`-based check
run directly with the venv's Python is enough. Don't add pytest.

Before claiming anything works:

```bash
.venv/bin/python -m compileall -q src/     # it at least parses
.venv/bin/lura --help                      # the CLI still wires up
.venv/bin/lura run                         # expect exit 78 with no key
```

---

## Things that will tempt you, and shouldn't

- **Don't hold a Live session open between wake words.** Sessions are capped and
  an idle socket for hours is a reconnect storm. "Running all day" is the wake
  loop, not the session.
- **Don't move keys into the login keyring.** libsecret needs an *unlocked*
  keyring; this service starts with the graphical session, which is exactly when
  it may still be locked. The 0600 file is the boring correct choice.
- **Don't rename the config/data dirs** without migrating them — `~/.config/lura`
  and `~/.local/share/lura` hold the user's keys and a 68 MB model.
- **Don't pin a preview model as if it were stable.** They churn. Keep it in
  settings with a default and let the error be legible.
- **Don't add a provider abstraction layer** until there is a third provider.

---

## Reporting back

State what you actually ran and what it printed. If a step was not verified —
anything needing a real API key, for instance — say so plainly rather than
implying it was tested. Several bugs in this project's history were found only
by running the thing; none were found by reasoning about it.
