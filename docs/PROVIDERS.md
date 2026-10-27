# Providers: what each one can do, and why Lura is built this way

Researched October 2026 against the live OpenRouter catalogue and each
provider's API. Re-check before relying on a detail: preview ids churn.

## Capability matrix

| | Brain (tools, streaming) | Hears audio | Speaks | Realtime voice |
|---|---|---|---|---|
| **Google Gemini** | ✓ `gemini-3.8-flash` via the OpenAI-compatible endpoint | ✓ natively (`input_audio`) | ✓ `gemini-3.8-flash-tts`, 30 voices | ✓ Gemini Live |
| **OpenRouter** | ✓ every hosted model | ✓ for Gemini and `openai/gpt-audio*` models | ✓ `google/gemini-3.8-flash-tts` (+ Lite) | ✗ |
| **OpenAI** | ✓ GPT-5.x via chat completions | GPT-5.x: ✗ (the separate transcription and gpt-audio models do) | has its own TTS, not used: Lura's voice is Gemini | has a Realtime API, not used |
| **Anthropic** | ✓ Claude via the Messages API (official SDK) | ✗ | ✗ | ✗ |

**Hence the rule in `settings/domain.py`:** any provider can think; hearing and
speaking come from Gemini 3.8, directly or through OpenRouter. A user with only
OpenAI or only Anthropic gets a clear "add a Gemini or OpenRouter key" in the
dashboard rather than a silent assistant.

OpenAI *could* supply a voice (`gpt-4o-mini-tts`, `gpt-4o-transcribe`, Realtime).
It is deliberately not wired in: one voice everywhere keeps Lura sounding like
Lura, Gemini 3.8 Flash TTS ranks first on OpenRouter's text-to-speech arena, and
it speaks Egyptian Arabic. Adding OpenAI speech later means one new class
implementing `SpeechSynthesizer` in `voice/infrastructure/tts.py`.

## How each adapter talks

- **OpenAI-compatible** (`brain/infrastructure/openai_compat.py`): one class for
  OpenAI (`api.openai.com/v1`), OpenRouter (`openrouter.ai/api/v1`) and Gemini
  (`generativelanguage.googleapis.com/v1beta/openai`). Streams SSE, assembles tool
  calls from fragments, and keeps the provider's raw assistant turn
  (`reasoning_details`, Gemini thought signatures) to send back unchanged.
  Effort: OpenRouter `reasoning.effort`; OpenAI/Gemini `reasoning_effort`
  (Gemini has no "minimal", so it maps to "low").
- **Anthropic** (`brain/infrastructure/anthropic_chat.py`): the official `anthropic`
  SDK, `messages.stream`. Default model `claude-opus-5-5`; the dashboard offers
  Sonnet 5.5, Haiku 5.5 and Fable 5.1. `output_config.effort` (thinking is
  adaptive and cannot be disabled on these models); server-side refusal fallback
  (`fallbacks: "default"`) on the models that support it. Claude's turns, including
  thinking blocks, are replayed exactly as returned. All tool results of one turn
  go back in a single user message.
- **Gemini TTS direct** (`voice/infrastructure/tts.py`): `models/<tts>:generateContent`
  with `responseModalities: ["AUDIO"]`, and the reply is 24 kHz PCM.
- **OpenRouter TTS**: `POST /audio/speech`, asking for raw PCM (no decoder needed on
  Windows) and falling back to MP3 + ffmpeg if a route refuses PCM.
- Both TTS routes fall back to the **Lite** TTS model on rate limits, credit
  exhaustion, timeouts and 5xx, but never on a 400.

## Latency budget (pipeline mode)

record end-of-turn (≈0.9 s of silence) → brain time-to-first-sentence → one
sentence of TTS → audio. Measured on the 1.x OpenRouter path: `reasoning: minimal`
cut a 3.8-flash turn from 9.3 s to 7.4 s. The streaming pipeline in 2.0 starts
speech after the *first sentence* instead of the whole reply. That has not been
re-measured with real keys yet. Realtime mode remains the fastest.
