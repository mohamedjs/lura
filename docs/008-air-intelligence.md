# 008 — Lura Air Stage F2: gestures reach Lura's conversation
Status: Ready
Builds on: docs/001-lura-air.md §3, docs/006-air-modes.md (AIR_INTENT), docs/ARCHITECTURE.md

## 1. Goal

Air Intelligence: gestures and voice work together through the **existing** assistant — no
second conversation system.

1. **Point + voice.** The window the user pointed at in the last 15 s is attached to their
   next spoken turn as a short note, so "Lura, explain what's happening here" knows what "here"
   is. For a browser window Lura can read the page with the Chrome connector she already has.
2. **Thumbs up / down answer Lura.** When Lura has asked something and is listening, a thumbs
   up is the user's "yes" and a thumbs down their "no" — exactly as if they had said it. It
   never confirms anything on its own: Lura asked, the user answered.
3. **Talk by gesture (ai mode).** In Air's ai mode a gesture while Lura is idle starts a
   conversation (like the wake word).

## 2. Stack

Nothing new.

## 3. Structure

```
src/lura/modules/assistant/domain.py        EDIT  AIR_NOTE_SECONDS, air_note(), answer_word(), prompt rule
src/lura/modules/assistant/application.py   EDIT  Assistant subscribes AIR_INTENT; PipelineConversation.inject(); notes on turns
tests/test_air_assistant.py                 NEW   (in run_all)
tests/run_all.py                            EDIT  add "test_air_assistant.py"
```
No other file changes (the bus event names exist since Stage E: `events.AIR_INTENT`).

## 4. Rules

- `assistant` imports nothing from `air` (it only reads `AIR_INTENT` payloads from the bus).
- Realtime mode (Gemini Live) is not changed in this stage; intents are ignored there (log debug).
- A thumbs answer is only used while a conversation is **listening for the user** (or within
  `ANSWER_GRACE = 4.0` s before that, while Lura is still finishing her question). Otherwise it
  is ignored (log info "No question to answer with a gesture.").
- Conventions as docs/002 §6; prompt text in the style of `assistant/domain.py`.

## 5. Interfaces (`assistant/domain.py`)

```python
AIR_NOTE_SECONDS = 15.0
ANSWER_GRACE = 4.0

def air_note(app: str, title: str) -> str
    # f"[The user is pointing at a window: \"{title[:80]}\" ({app}). \"This\"/\"here\" means that window.]"
    # empty title → use the app alone; both empty → "".

def answer_word(intent: str, language: str) -> str
    # "confirm" → "نعم" if language == "ar" else "Yes."; "deny" → "لا" / "No." ; else "".
```
`system_prompt(...)` gains one paragraph, always present (byte-stable), appended after the tool
harness paragraph, in English like the rest:
`"Lura Air: the user may also use hand gestures. A note like [The user is pointing at a window:
…] in their message tells you what \"this\" or \"here\" means; for a browser window you can read
the page with the browser tools. A turn that is only \"Yes.\"/\"No.\" may come from a thumbs up
or down."` (exact wording yours, ≤ 60 words).

## 6. Behaviour (`assistant/application.py`)

`Assistant.__init__`: `bus.subscribe(events.AIR_INTENT, self._on_air_intent)`; state
`self._air_point: tuple[str, str, float] | None` (app, title, monotonic t).

`_on_air_intent(payload)` (runs on the publisher's thread — must be quick, never block):
- intent "point" → `self._air_point = (payload.get("app",""), payload.get("title",""), time.monotonic())`.
- intent "confirm"/"deny" → `conv = self._conversation`; if conv is a PipelineConversation:
  `conv.inject(answer_word(intent, self._settings.current.language), source=intent)`; else log info.
- intent "talk" → if `self.status == "ready"` (idle, not talking) → `self._talk.set()` (the same
  path as TALK_REQUESTED).
- anything else → ignore.
`Assistant` passes a callable `air_point=lambda: self._take_air_point()` to each
`PipelineConversation`; `_take_air_point()` returns `(app, title)` and clears it if it is younger
than AIR_NOTE_SECONDS, else None.

`PipelineConversation`:
- `__init__(..., air_point=lambda: None)`; state `self._inject_text: str = ""`,
  `self._inject_t = 0.0`, `self._inject_evt = threading.Event()`, `self._listening = False`.
- `inject(text: str, source: str = "") -> None` — store text + time, set the event (thread-safe;
  no other work).
- In `_converse`'s loop, before recording: `self._listening = True`; a pending injection younger
  than ANSWER_GRACE is consumed immediately (no recording). Otherwise record with
  `should_stop = lambda: self._stopped.is_set() or self._inject_evt.is_set()`. After `record`
  returns: `self._listening = False`; if the event is set → consume the injection: clear the
  event, `heard = text`; caption "you" = `"👍 " + text` / `"👎 " + text` (by source), append
  `Message("user", heard)`, store it in memory like a heard turn, `_answer()`; continue the loop.
  (Any audio recorded before the gesture is dropped.)
- Injections older than ANSWER_GRACE when consumed are dropped (log debug).
- Air note: in both turn branches, `note = air_note(*p) if (p := self._air_point()) else ""`.
  Text branch: `Message("user", f"{note}\n{heard}" if note else heard)` (memory keeps the plain
  `heard`). Audio branch: `Message("user", text=note, audio_wav=wav)`, and `_caption_heard`
  gets the note: when the transcript arrives, `message.text = f"{note}\n{heard}" if note else heard`.
  Log info `"Air note attached: <app>"`.

## 7. Tasks

1. domain: constants, air_note, answer_word, prompt paragraph.
2. application: Assistant subscription + `_take_air_point`; PipelineConversation inject + notes.
3. tests + CHECKS.

## 8. Acceptance checks

`tests/test_air_assistant.py` (prints `air assistant OK`), using the mock-server / fake patterns of
`tests/test_integration.py` (read it first; reuse its fakes for brain, voice and tools rather
than inventing new ones; record() is faked):
- `air_note` / `answer_word` values (en, ar); the system prompt contains the Air paragraph and is
  identical across two builds (byte-stable).
- A conversation listening (fake record blocks until `should_stop()` is true): publishing
  `AIR_INTENT confirm` on the bus stops the recording; the next brain request's last user message
  is "Yes." (or "نعم" for language ar); the "you" caption starts with 👍.
- An intent while no conversation is running → nothing happens, no exception.
- An injection arriving while Lura is still speaking (not listening yet) and consumed within 4 s
  → used; older than 4 s → dropped and the user is recorded normally.
- Point then speak within 15 s: the user message text starts with the air note; after 16 s: no
  note; the note is used once (the following turn has none). Audio branch: a brain that hears
  audio gets `text=note` with the WAV, and after the caption transcript arrives the text is
  `note + "\n" + heard`.
- `talk` intent while status ready → `_talk` set; while talking → not set.

```bash
.venv/bin/python -m compileall -q src/
.venv/bin/python tests/test_air_assistant.py   # → air assistant OK
.venv/bin/python tests/run_all.py              # → All checks passed.
```
No live runs, no packages, no commits, no docs/ edits.

## 9. Out of scope

Screenshots / reading non-browser window contents (needs an image-capable turn; later),
realtime mode, spoken confirmations of every Air action, dashboard UI.

## 10. Decisions

- 2026-11-20: a thumbs up is turned into the user's spoken answer ("Yes.") inside the existing
  conversation, so the brain's ask-before-acting rule stays the only gate: nothing is confirmed
  without Lura having asked.
- 2026-11-20: pointing context is a short text note on the next user turn (not the system prompt,
  which must stay byte-stable for caching), valid 15 s and used once.
- Implemented by Gemini 4 Neon in one round. Accepted deviations: `fit_history` keeps the Air note
  line with the newest audio turn (and with older audio turns that have no transcript yet), so
  the note is not stripped before the brain sees it; the prompt paragraph uses
  `f"{name} Air"` so a renamed assistant never says "Lura". Claude added
  `PipelineConversation.spoken_language()`: the thumbs answer word follows the language of the
  user's last words in the conversation (settings.language is only the default; an English
  "Yes." would have switched an Arabic conversation to English).
- Not verified live yet: needs the daemon run from source with Air enabled.
