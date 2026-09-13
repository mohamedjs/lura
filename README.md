# Lura

Say **"Gemini"** out loud. It beeps, listens, and answers you in a voice.
Runs in the background from the moment your desktop starts.

The app is called Lura; the word you *say* is the wake word, and that is a
separate thing — see [Changing the wake word](#changing-the-wake-word).

**[USAGE.md](USAGE.md)** is the day-to-day guide. This file covers installing
and the full reference. **[AGENTS.md](AGENTS.md)** is for anyone (or anything)
changing the code. Repository: [mohamedjs/lura](https://github.com/mohamedjs/lura).

## Install

```bash
cd /var/www/html/lura
./install.sh
```

It installs any missing system packages, builds a Python environment, downloads
the offline wake-word model (~40MB), asks for your API key once, runs a
self-check, and starts the service.

To keep it running after you close the terminal and between logins:

```bash
sudo loginctl enable-linger $USER
```

## Two providers

| | `gemini` | `openrouter` |
|---|---|---|
| How it talks | Streams both ways — it answers while you speak, and you can cut it off | One turn at a time: you speak, it thinks, it replies |
| Models | The Gemini Live audio models | Any of ~445, including Claude, GPT and Gemini |
| Speed | Faster | Slower — three calls per turn |
| Languages | Native, including Arabic | Native, including Arabic |

Switch whenever you like; both keys can be stored at once.

```bash
lura config provider openrouter
lura config provider gemini
systemctl --user restart lura
```

**The wake word is always local.** Vosk listens on your machine whichever
provider answers — it is not a Google service and costs nothing. It stays
"Gemini" even on OpenRouter; that is the name of the button, not the model.

### Changing the wake word

The offline model only knows real English words, so the wake word has to be one
of them. **"lura" is not in its vocabulary and will never trigger** — it was
tested. "laura" is, and is the closest thing to the app's name:

```bash
lura config wake_word laura
systemctl --user restart lura
```

Verified working: `gemini` (the default), `laura`. If a word you pick never
fires, that is why — `journalctl --user -u lura` shows
`Ignoring word missing in vocabulary` at startup.

## Keys

Stored in `~/.config/lura/keys.json`, mode `0600`, one entry per
provider.

```bash
lura login --provider gemini        # replace the Gemini key
lura login --provider openrouter    # add an OpenRouter key
lura logout --provider gemini       # forget one
```

`GEMINI_API_KEY` / `OPENROUTER_API_KEY` in the environment override the stored
ones, which is handy for testing without touching the file.

Get them at <https://aistudio.google.com/apikey> and
<https://openrouter.ai/keys>.

**Gemini keys must be new.** Keys created before September 2026 are the old
"standard" type and are now rejected by the API. Create a fresh one — new keys
are "auth" keys and work. A Google account sign-in on its own cannot reach the
Live API; there is no supported path from a consumer Gemini account to it.

## Choosing a model

```bash
lura models                  # everything on OpenRouter
lura models claude           # filter
lura models gpt-audio        # the audio-in/audio-out ones

lura config openrouter_model anthropic/claude-sonnet-4.5
lura config gemini_model gemini-2.5-flash-native-audio-preview-12-2025
```

A model per provider is remembered, so switching back and forth keeps both.
Mistype one and it tells you, with near matches, instead of failing later at
the wake word.

## Service

```bash
systemctl --user status  lura
systemctl --user restart lura
systemctl --user stop    lura
journalctl --user -u lura -f     # watch it work
```

## Settings

`~/.config/lura/settings.json` — created on first change, or write it
yourself. Restart the service afterwards.

```bash
lura config                       # show everything
lura config voice                 # show one
lura config voice Charon          # change one
```

| Key | Default | Notes |
|---|---|---|
| `provider` | `gemini` | `gemini` or `openrouter` |
| `gemini_model` | `gemini-2.5-flash-native-audio-preview-12-2025` | Preview ids get retired; if `selftest` says the model was not found, put a current one here |
| `openrouter_model` | `google/gemini-2.5-flash` | Anything from `lura models` |
| `openrouter_stt_model` | `openai/whisper-1` | Turns your speech into text |
| `openrouter_tts_model` | `openai/gpt-4o-mini-tts` | Speaks the reply |
| `openrouter_tts_voice` | `alloy` | |
| `voice` | `Puck` | Gemini Live only |
| `wake_word` | `gemini` | Must be a word the offline model knows |
| `language` | `en-US` | `ar-XA` for Arabic |
| `silence_ms` | `900` | OpenRouter only: quiet this long ends your turn |
| `silence_factor` | `2.0` | OpenRouter only: raise it in a noisy room |
| `idle_timeout` | `12.0` | Seconds of quiet before the conversation closes |
| `gate_mic_while_speaking` | `true` | Gemini only — see below |
| `input_device` / `output_device` | `null` | Indices from `selftest` |

### If it cuts you off, or never stops listening

Only on OpenRouter: your machine has to decide when you stopped talking, and
room noise varies enormously. The noise floor is measured at the start of each
utterance, and these scale it — raise `silence_factor` if a fan keeps the turn
open, raise `silence_ms` if it cuts in while you are still thinking.

### Talking over it

By default the microphone is muted while the assistant speaks, so it cannot
hear itself, interrupt itself, and spiral. The cost is that you cannot cut it
off mid-answer.

On headphones, or with PipeWire's echo cancellation loaded, set
`gate_mic_while_speaking` to `false` and interrupting starts working.

## When it doesn't work

Run `lura selftest`. It checks each layer in order — devices,
microphone level, speaker, wake model, key, then a real call to the model — and
the first `FAIL` is the thing to fix. A silent microphone level means the wrong
input device is the default; pick one from the list it prints and set
`input_device`.
