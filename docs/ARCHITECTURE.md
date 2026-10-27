# Architecture

Lura is a **modular monolith**: one process, one install, split into bounded
contexts that meet only through their public package. Inside each module the
code is layered domain-driven style.

```
src/lura/
├── app.py            CLI: presentation for the terminal
├── bootstrap.py      composition root: the ONLY file that builds and wires modules
├── selftest.py       `lura selftest`
├── shared/           kernel every module may use, none owns
│   ├── paths.py        per-OS config/data dirs (Linux keeps ~/.config/lura)
│   ├── http.py         JSON + SSE over urllib, HttpError with .retryable
│   ├── events.py       in-process event bus (dashboard ⇄ assistant)
│   ├── presence.py     Presence protocol: what the face shows
│   ├── desktop.py      start at login: systemd / XDG / LaunchAgent / Run key
│   ├── errors.py       NeedsSetup (exit 78), ProRequired
│   └── i18n/           en.json, ar.json + t()
└── modules/
    ├── settings/     Settings, providers, voices, capability rules; JSON + 0600 key file
    ├── brain/        ChatModel port; OpenAI-compatible adapter (OpenAI, OpenRouter, Gemini), Anthropic adapter
    ├── voice/        sentence chunker, Gemini TTS (direct / OpenRouter), transcriber, mic/speaker, Vosk wake word, Gemini Live
    ├── tools/        built-in tools + command guard; per-OS implementations; briefing
    ├── connectors/   MCP: stdio and Streamable HTTP clients, mcp.json repository, plan rule
    ├── billing/      Free/Pro, licence entitlement rule, Lemon Squeezy licence API
    ├── assistant/    orchestration: wake loop, pipeline conversation, realtime, briefing
    ├── overlay/      presentation: the hologram face (cairo), GTK and Qt backends, tray
    └── dashboard/    presentation: local HTTP API + static neumorphic page
```

## The rules

1. **A module is imported only as a package.** `from ..brain import BrainService`
   is fine; `from ..voice.infrastructure.audio import …` from another module is not.
   `tests/test_architecture.py` enforces this on every run.
2. **Layers inside a module.** `domain.py` is pure: no I/O, no network, so the rules
   are testable on their own (`tests/test_domain.py`). `application.py` holds the use
   cases and talks to infrastructure. `infrastructure/` holds HTTP, files, audio and
   processes. Each `__init__.py` is the module's public interface.
3. **Dependencies flow inward.** Presentation (CLI, dashboard, overlay) →
   assistant → brain/voice/tools/connectors → settings → shared. A lower module
   never imports a higher one. Where a lower module needs something from a higher
   one, it declares a `Protocol` port and bootstrap injects it:
   `tools.ConnectorTools` (implemented by `ConnectorService`), `Presence`
   (implemented by `OverlayState`), and `key_for` callables for brain and voice.
4. **Cross-module notifications go through the event bus**, e.g. "talk now" from the
   dashboard or the tray, and "plan changed" → connectors refresh.

## A conversation, pipeline mode

```
wake word (Vosk, offline) ─► record until silence (noise floor measured per turn)
   │
   ├─ brain hears audio? (Gemini, OpenRouter+Gemini) ─► WAV goes to the brain as input_audio
   └─ otherwise (Claude, GPT) ─► Gemini 3.8 Flash transcribes ─► text to the brain
   │
brain.stream(system, history, tools) ──TextDelta──► SpeechPlayer.feed()
   │                                                 │ SentenceChunker: first sentence out alone
   │                                                 ▼
   │                                       TTS thread (Gemini 3.8 TTS) ─► playback thread ─► face mouth
   ▼
Done(message with tool_calls) ─► ToolService.dispatch (guarded) ─► results ─► next round
```

Three stages run concurrently: while sentence one plays, sentence two is being
synthesised and sentence three is still being written by the model. That overlap
is the "sync" between the brain and the voice.

`find_tools` keeps MCP schemas out of the prompt until the model asks for them:
a hundred schemas are ~12k tokens and measured 2.7 s of added latency per turn.

## Realtime mode

Gemini Live owns hearing, thinking, turn detection and the voice; Lura feeds it
the mic, plays its audio, and answers its tool calls through the same
`ToolService.dispatch` and guard. Live has no second round, so all connector
tools are declared up front.

## Threads

- **Main thread:** the UI loop. Qt must own it (macOS insists). GTK runs in its own thread.
- **assistant:** the wake loop and conversations.
- **tts / playback:** per reply, inside `SpeechPlayer`.
- **mcp-<name>:** one starter per connector, plus a reader thread per stdio server.
- **dashboard:** `ThreadingHTTPServer`.
