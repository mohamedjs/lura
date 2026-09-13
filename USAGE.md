# Using Lura

Day-to-day guide. For installing it, see [README.md](README.md).

Every command below assumes the `lura` command is on your `PATH`:

```bash
ln -s /var/www/html/lura/.venv/bin/lura ~/.local/bin/lura
```

Without that link, write `/var/www/html/lura/.venv/bin/lura` instead.

---

## 1. Talking to it

Say **"Gemini"** out loud.

1. A **rising beep** — it heard you and is listening.
2. Ask your question.
3. It answers out loud.
4. A **falling beep** — the conversation ended. It goes back to waiting.

The conversation stays open between questions, so you can keep talking without
saying the wake word again. After 12 seconds of quiet it closes.

To talk without waiting for the wake word:

```bash
lura say
```

### The wake word is not the app's name

The app is Lura. The word you *say* is "Gemini". They are separate on purpose:
the wake word is matched by an offline model on your machine that only knows
real English words, and **"lura" is not one of them** — it will never trigger.

`laura` works, if you want it closer:

```bash
lura config wake_word laura
systemctl --user restart lura
```

Tested and working: `gemini`, `laura`. If a word you choose never fires, that is
why — check `journalctl --user -u lura` for
`Ignoring word missing in vocabulary`.

---

## 2. Which service answers you

Two providers. You can keep keys for both and switch whenever.

| | `gemini` | `openrouter` |
|---|---|---|
| Feel | Answers while you are still speaking; you can interrupt it | One turn at a time — speak, pause, it replies |
| Models | The Gemini Live audio models | ~445, including Claude and GPT |
| Speed | Faster | Slower: three calls per turn |
| Arabic | Yes | Yes |

```bash
lura config provider openrouter
systemctl --user restart lura
```

**Why OpenRouter is turn-based:** it has no realtime voice socket. That path
records what you said, transcribes it, sends the text to your chosen model, and
speaks the reply. Three round trips instead of one open stream.

---

## 3. Choosing a model

```bash
lura models                 # all of them
lura models claude          # filter by name
lura models gpt-audio       # the audio-in/audio-out ones
```

Then:

```bash
lura config openrouter_model anthropic/claude-sonnet-4.5
lura config gemini_model gemini-2.5-flash-native-audio-preview-12-2025
systemctl --user restart lura
```

Each provider remembers its own model, so switching back and forth never loses
the other one.

Mistype one and it tells you straight away, with near matches — you will not
find out at the next wake word:

```
$ lura config openrouter_model anthropic/claude-sonnet-9
Warning: no model 'anthropic/claude-sonnet-9' on OpenRouter.
Did you mean:
  anthropic/claude-sonnet-5
  anthropic/claude-sonnet-4.5
```

---

## 4. Keys

```bash
lura login  --provider gemini        # add or replace
lura login  --provider openrouter
lura logout --provider gemini        # forget one
```

Stored in `~/.config/lura/keys.json`, mode `0600`, one entry per provider.
`GEMINI_API_KEY` and `OPENROUTER_API_KEY` in the environment override the file.

Get them at <https://aistudio.google.com/apikey> and
<https://openrouter.ai/keys>.

> **Gemini keys must be newly created.** Keys made before September 2026 are
> the old "standard" type and the API now rejects them. A fresh one is an
> "auth" key and works.
>
> A Google account sign-in on its own **cannot** reach the Live API. There is no
> supported path from a consumer Gemini account to it, so a key is required.

---

## 5. Settings

```bash
lura config                      # show everything
lura config voice                # show one
lura config voice Charon         # change one
```

Changes are written to `~/.config/lura/settings.json`. **Restart after
changing anything:**

```bash
systemctl --user restart lura
```

The ones you are most likely to touch:

| Setting | Default | What it does |
|---|---|---|
| `provider` | `gemini` | Which service answers |
| `wake_word` | `gemini` | Must be a real English word |
| `language` | `en-US` | `ar-XA` for Arabic |
| `voice` | `Puck` | Gemini Live voice |
| `system_instruction` | *(short-answers prompt)* | How it should behave |
| `idle_timeout` | `12.0` | Seconds of quiet before it stops listening |
| `input_device` / `output_device` | `null` | Device numbers from `lura selftest` |

---

## 6. The background service

```bash
systemctl --user status  lura
systemctl --user restart lura
systemctl --user stop    lura
systemctl --user start   lura

journalctl --user -u lura -f      # watch it, live
```

It starts automatically with your desktop session. To keep it running after you
log out:

```bash
sudo loginctl enable-linger $USER
```

---

## 7. When something is wrong

**Always start here:**

```bash
lura selftest
```

It checks each layer in order and stops at the first real problem — audio
devices, microphone level, speaker, wake model, key validity, model existence,
then a real call to the model. Fix the first `FAIL` and run it again.

| What you see | What it means |
|---|---|
| `level: 0.0000` on the mic test | Wrong input device, or the mic is muted. Pick one from the list it printed: `lura config input_device 5` |
| `FAIL: No gemini API key` | `lura login --provider gemini` |
| Gemini: *model not found* | That preview model was retired. `lura config gemini_model <newer id>` |
| OpenRouter: `usage >= limit` | The key is out of credit — not broken |
| OpenRouter: *no such model* | Typo. It prints near matches |
| Service `failed (status=78)` | No key, or no wake model. It deliberately does **not** retry — 78 means "a human has to fix this" |

**It never hears me.** Check the wake word is in the vocabulary (section 1),
then confirm the service is running and the mic level is non-zero in
`selftest`.

**It interrupts itself, or talks in a loop.** The mic is muted while it speaks
to stop exactly that. If you turned that off, turn it back on:

```bash
lura config gate_mic_while_speaking true
```

**It cuts me off mid-sentence** *(OpenRouter only)* — your machine decides when
you stopped talking, and room noise varies a lot:

```bash
lura config silence_ms 1400       # wait longer before deciding you finished
lura config silence_factor 3.0    # raise in a noisy room, e.g. a loud fan
```

**I want to interrupt it** *(Gemini only)*. On headphones, or with PipeWire's
echo cancellation loaded:

```bash
lura config gate_mic_while_speaking false
```
