# Handoff: microphone picker, turn-taking, memory/history, skills

Status (2026-10-28): **design done, no code written yet.** The previous session read
the code and planned the work below, then stopped before implementing it. Start here.

Mohamed's request, in his words: "voice not working with Lura, I mean about my mic,
so in the dashboard I can select the mic… once I'm silent for one second Lura speaks,
once Lura stops I'm back… history between me and Lura… always remember me… context
is 200,000 tokens; once full, summarise the conversation into an MD file / reference…
let users add skills and use them with any model (OpenRouter too)."

Read `CLAUDE.md` and `AGENTS.md` first. The rules that matter here:
- Modules talk only through their package `__init__`; `tests/test_architecture.py` enforces it.
- `bootstrap.py` is the only place that wires modules together.
- Never touch the real keys.

Branch: `claude/vibrant-darwin-cvzalg`. PR #8 (release asset filter) is open on it.
v2.0.2 is released.

---

## 0. Where things are today (the code that was read)

- **`voice/infrastructure/audio.py` → `record_utterance(settings)`**
  - Energy VAD over 250 ms blocks (`BLOCK_FRAMES=4000` at 16 kHz).
  - It measures the noise floor over the **first 0.5 s**, then ends after `silence_ms` (default 900) of quiet.
  - With no speech, it returns `b""` after `idle_timeout`, which defaults to **180 s**.
  - It opens `sd.RawInputStream(samplerate=16000, device=settings.input_device)`.
- **`voice/infrastructure/wake.py` → `WakeListener`**
  - Vosk; it also opens 16 kHz on `settings.input_device`.
  - It is built once in `Assistant.run_forever` and rebuilt only when `wake_word` changes.
- **`assistant/application.py` → `PipelineConversation.run()`**
  - It already loops: record → answer (streamed TTS) → record again, until silence, `end_session` or `max_session_seconds`.
  - The in-session `_history` is discarded when the conversation ends.
  - `domain.trim_history` keeps the last 10 user turns.
  - Audio turns (`Message.audio_wav`) are sent for brains that hear audio; the others get a transcript.
- **Settings** (`settings/domain.py: Settings`) already has these fields:
  - `input_device` / `output_device`: str or None, entered as free text in Advanced.
  - `silence_ms`, `silence_factor`, `idle_timeout`, `gate_mic_while_speaking`.
  
  `settings.json` stores **every** field, so changing a default does not change existing users. Add new fields instead of re-defaulting old ones; unknown keys are ignored on load.
- **Tools** (`tools/application.py: ToolService`) has the built-ins plus a `ConnectorTools` port (MCP).
  - `dispatch()` returns `(text, ends_session)`.
  - `realtime_specs()` declares everything up front for Gemini Live.
- **Dashboard**:
  - `dashboard/api.py` holds the routes. `LIVE = {...}` is the set of settings that apply without a restart.
  - The front end is plain JS: `static/index.html`, `app.js` (one render function per card), `app.css` (neumorphism).
  - Strings live in `shared/i18n/en.json` and `ar.json`, and every key must exist in both.
- **Anthropic adapter**: there is no prompt caching (`cache_control`) yet. That matters once history grows.

## 1. Microphone: picker + "it doesn't hear me" (do this FIRST)

Likely causes of "the mic doesn't work":
1. **The wrong default device.** Fixed by the picker.
2. **The device rejects 16 kHz.** Raw ALSA `hw:` devices do; Pulse/PipeWire "default" resamples, so it works there. The fix: open at the device's `default_samplerate` and resample to 16 kHz (`audio.resample` exists).
3. **Calibration eats speech.** If the user starts talking within the first 0.5 s, the "noise floor" is speech, so speech is never detected and the turn hangs until the 180 s idle timeout.

Plan:
- **`voice/infrastructure/audio.py`:**
  - `list_devices()` returns inputs and outputs: `{name, index, hostapi, channels, default_samplerate, is_default}`.
  - `resolve_device(name, kind)` turns a stored name into an index, preferring the default host API. Store **names**, not indices, because indices change across boots and USB replugs.
  - `MicStream`: opens at the supported rate, resamples to 16 kHz and yields 16 kHz int16 blocks. Use it in **both** `record_utterance` and `WakeListener.listen`.
  - `mic_test(device, seconds=3)` returns `{ok, peak, floor, rate, error}`.
- **VAD:**
  - Use 100 ms blocks.
  - Track the noise floor continuously (a low percentile of recent non-speech blocks) with an absolute minimum, instead of a one-shot 0.5 s calibration.
  - Keep pre-roll audio.
  - Wait about 150 ms after Lura finishes before reopening the mic, so the room's reverb doesn't trigger.
- **Turn-taking settings:**
  - Add `follow_up_seconds: float = 8.0`: how long Lura waits for you to speak after she answers (and after the wake word) before she goes back to waiting for the wake word. Use it instead of `idle_timeout`, and remove `idle_timeout` from the dataclass.
  - Expose `silence_ms` as a dashboard slider, "Pause before Lura answers", 500–2500 ms. Mohamed wants about 1000 ms.
  - Barge-in (talking over Lura) is out of scope: there's no echo cancellation, and `gate_mic_while_speaking` stays on.
- **Live device change:**
  - Rebuild the `WakeListener` when `(wake_word, input_device)` changes.
  - Give `listen()` a `reload` event (set on `SETTINGS_CHANGED` for `input_device`) so it returns without starting a conversation.
  - Add `input_device`, `output_device`, `silence_ms` and `follow_up_seconds` to `LIVE` in `dashboard/api.py`.
- **API:**
  - `GET /api/audio/devices`.
  - `POST /api/audio/test-mic {device}` (runs about 3 s and returns levels).
  - `POST /api/audio/test-speaker {device}` (plays the chime).
- **UI:** a new "Microphone & speaker" card replaces the two free-text fields in Advanced:
  - a `<select>` for each device ("System default" first);
  - a "Test" button with a level meter;
  - the pause slider;
  - the follow-up window.

## 2. Memory: history + "always remembers me" without the user managing context

New module **`modules/memory/`** (domain / application / infrastructure, public API in `__init__`).

- **Store** (`infrastructure`): SQLite at `data_dir()/memory/history.db`, stdlib `sqlite3`.
  - Tables: `conversations(id, started, ended, title)`; `messages(id, conv_id, ts, role, text, model)`; an FTS5 table over the message text for search.
  - Text only, never audio.
- **Durable facts:** `data_dir()/memory/memory.md`, about the user ("Mohamed prefers Egyptian Arabic…").
  - Lura edits it with tools; the user can edit it in the dashboard.
  - Injected into the system prompt, capped at about 3k tokens.
- **One endless thread instead of sessions.** Every new conversation starts with:
  - system prompt + `memory.md` + the **running summary** + the most recent verbatim turns that fit in the budget.
  
  So the user never "starts a new chat".
- **Compaction** (`domain`, pure):
  - Estimate tokens as `len(text)/3`, which is conservative for Arabic.
  - When the thread's estimate exceeds `memory_compact_at` = 80% of `context_budget_tokens` (default **200_000**, as Mohamed asked; dashboard select 32k/64k/128k/200k), summarise everything except the last ~20% with the current brain (one call, no tools).
  - Store the new running summary, and also write it to `data_dir()/memory/summaries/<date>.md`. That is the "MD reference" he asked for.
  - Run it after a conversation ends (background thread); run it synchronously only if a turn would exceed the hard budget.
- **Tools** for the model, via a generic tool-provider port added to `ToolService`, wired in bootstrap:
  - `remember(fact)` and `forget(fact)`, which edit `memory.md`;
  - `search_history(query)`, an FTS search over all past conversations, returning dated snippets;
  - realtime mode gets the same tools.
- **Audio vs text:**
  - Persist the transcript of each user turn. For brains that hear audio, reuse the parallel caption transcription that already exists, and run it whenever memory is on, not only when subtitles are.
  - Within a session, swap older audio turns for their transcripts, so only the newest user turn is audio.
- **Replace** `assistant/domain.trim_history` (10 turns) with token-budget windowing from `memory`.
- **Anthropic:** add `cache_control` on the system prompt and the last stable history block, or long threads get expensive and slow. OpenRouter→Gemini caches implicitly (provider order is already pinned).
- **Settings:** `memory_enabled: bool = True`, `context_budget_tokens: int = 200000`.
- **API and UI:** a new "Memory & history" card:
  - the toggle and the budget select;
  - a `memory.md` editor;
  - the conversation list (by date, with search), a transcript viewer, and delete-one / delete-all;
  - a "Start fresh" button that archives the thread.
  
  Routes: `GET/PUT /api/memory`, `GET /api/history?q=`, `GET/DELETE /api/history/<id>`, `POST /api/memory/reset`.
- **Privacy:** everything stays local. Say so in the card hint.

## 3. Skills (works with every provider, including OpenRouter)

Use the Agent Skills format (a folder with `SKILL.md`: YAML front matter `name` and `description`, a Markdown body, and optional extra files). It's model-agnostic because it's only prompt plus tools, which is how to "send skills" through OpenRouter, OpenAI, Gemini or Claude alike.

- New module **`modules/skills/`**. Storage: `config_dir()/skills/<name>/SKILL.md` (+ files) and `skills.json` (enabled flags).
- **Progressive disclosure:**
  - the system prompt lists each enabled skill as `- name: description` (cheap);
  - the tool `use_skill(name)` returns the `SKILL.md` body plus a list of its files and the folder path;
  - the tool `read_skill_file(name, path)` returns one file, confined to the skill folder;
  - scripts run only through the existing guarded `run_command`, so the command guard still applies.
- **Adding skills from the dashboard:** paste a `SKILL.md`, upload a `.zip` folder, or import from a URL (a raw `SKILL.md` or a zip). Also enable/disable and delete.
  - Warn in the UI that a skill is instructions Lura will follow; commands still pass the guard.
  - Decide with Mohamed whether skills are Free or Pro. The default assumption is Free.
- **API:** `GET /api/skills`, `POST /api/skills` (`{kind: paste|zip|url}`), `PATCH`/`DELETE /api/skills/<name>`.

## 4. Order of work and verification

1. Mic (section 1); tests for `resolve_device`, the VAD on synthetic PCM (speech that starts in the first 0.5 s must still be caught) and resampling.
2. Memory (section 2); tests for compaction windowing, the store, the tools and the API.
3. Skills (section 3); tests for parsing, path confinement and the API.
4. Add the new strings to `en.json` and `ar.json` (Egyptian Arabic), and the new cards to the dashboard.
5. Run `python3 tests/run_all.py`, `python3 -m compileall -q src/` and `cd src && python3 -m lura.modules.overlay`.
6. Real audio can only be verified on Mohamed's machine. End with:
   `systemctl --user restart lura.service && journalctl --user -u lura -f`
