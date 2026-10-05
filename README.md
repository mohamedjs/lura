# Lura

**A voice assistant for your Linux desktop.** Say *"Lura"*, hear a chime, and talk.
She answers out loud, in English or Arabic, and can act on your machine:
open apps, run commands, check how the system is doing, and use any
[MCP](https://modelcontextprotocol.io) server you plug in (GitHub, your files,
a database…).

It runs as a `systemd --user` service, starts with your desktop, and waits
quietly. The wake word is detected **offline** on your machine. Nothing leaves it
until you say the word.

```
you:   "Lura"                          ← detected locally by Vosk
lura:  *chime*
you:   "open Chrome and tell me how much RAM I'm using"
lura:  "Done, Chrome is open. You're using 5.6 of 23 gigabytes."
```

- **Two brains, your choice:** Google **Gemini Live** (real-time, full-duplex
  audio) or **OpenRouter** (any of ~450 models, such as Claude, GPT or Gemini, one turn at a time).
- **Arabic and English**, natively.
- **Morning briefing:** after each boot she tells you the weather, CPU temperature,
  RAM and load, internet latency, and your latest GitHub commit.
- **Tools:** `open_application`, `list_applications`, `run_command`,
  `get_system_briefing`, plus every tool from your MCP servers.
- **Floating overlay** with an animated face while she listens and talks.

> Docs map: this README covers install and reference. **[USAGE.md](USAGE.md)** is the
> day-to-day guide. **[AGENTS.md](AGENTS.md)** is for anyone changing the code.
> **[video/](video/)** is the code that renders the promo videos.

---

## Requirements

- Linux with a desktop session (tested on Ubuntu/Debian; uses `apt` for missing packages)
- Python **3.10+**, a microphone and speakers
- An API key for **Gemini** *or* **OpenRouter** (see [API keys](#api-keys))
- Optional: `node`/`npx` or `docker`, to run MCP servers

## Install

```bash
git clone https://github.com/mohamedjs/lura.git
cd lura
./install.sh
```

Run it as yourself, not root. The installer is safe to re-run. It:

1. installs missing system packages (`python3-venv`, `libportaudio2`, `unzip`, `curl`),
2. builds a virtualenv in `.venv/` and installs Lura into it,
3. downloads the offline wake-word model (~40 MB) to `~/.local/share/lura/`,
4. asks for your API key and stores it (once),
5. runs `lura selftest`,
6. installs and starts the `lura` user service.

To keep Lura running after you close the terminal and across logins:

```bash
sudo loginctl enable-linger $USER
```

The venv bakes in absolute paths. If you move the folder, run `./install.sh` again
from its new place.

## API keys

You need one key, for whichever provider you use. You can store both.

| Provider | Get a key | Env var (overrides the stored key) |
|---|---|---|
| `gemini` (default) | <https://aistudio.google.com/apikey> | `GEMINI_API_KEY` or `GOOGLE_API_KEY` |
| `openrouter` | <https://openrouter.ai/keys> | `OPENROUTER_API_KEY` |

```bash
lura login                          # store a key for the active provider (prompts, hidden input)
lura login --provider openrouter    # store the other one
lura logout --provider gemini       # forget one
```

Keys are kept in `~/.config/lura/keys.json` with mode `0600`.

**Gemini keys must be new.** Keys created before September 2026 are the old
"standard" type and the API rejects them with an error that looks like a bug.
Create a fresh key in AI Studio. Signing in with a Google account alone can't reach
the Live API; you need a key.

**OpenRouter:** `lura selftest` tells an *invalid* key apart from one that's *out of
credit*, because the fixes are different.

## Using it

Say **"Lura"** (it also accepts "Laura"), wait for the chime, and speak.

```bash
lura say          # talk now, skipping the wake word
lura briefing     # hear the morning briefing now
lura selftest     # check mic, speaker, wake model, key, model, and a real API call
lura run          # run in the foreground (what the service runs), handy with -v
```

For Arabic answers:

```bash
lura config language ar-XA
systemctl --user restart lura
```

### Choosing the provider and model

```bash
lura config provider openrouter          # or: gemini
lura models claude                       # search OpenRouter's models
lura config openrouter_model anthropic/claude-sonnet-4.5
lura config gemini_model gemini-2.5-flash-native-audio-preview-12-2025
systemctl --user restart lura
```

| | `gemini` | `openrouter` |
|---|---|---|
| How it talks | Streams both ways; answers while you're still finishing | One turn: you speak, then it transcribes, thinks, and speaks |
| Models | Gemini Live audio models | Any OpenRouter model |
| Built-in tools + MCP | ✅ | ❌ conversation only, for now |
| Turn detection | Server-side | Local; tune `silence_ms` / `silence_factor` |

Preview model IDs get retired. If `selftest` says *model not found*, set a current one.

## MCP: give Lura more tools

Lura is an MCP **client**. Every server you list is started when Lura starts, and
its tools are offered to the model as `mcp_<server>_<tool>`. MCP tools work with the
**`gemini`** provider.

**1. Find the config file**

```bash
lura mcp path        # → ~/.config/lura/mcp.json
```

**2. Add servers.** It's the same `mcpServers` format Claude Desktop and Cursor use:

```json
{
  "mcpServers": {
    "files": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/you/Documents"]
    },
    "github": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-e", "GITHUB_PERSONAL_ACCESS_TOKEN", "ghcr.io/github/github-mcp-server"],
      "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_your_token_here" }
    }
  }
}
```

- `command` + `args` start the server. It talks JSON-RPC over stdin/stdout.
- `env` is added to the server's environment. Put that server's tokens here, not in
  your shell profile. The file holds secrets, so `chmod 600 ~/.config/lura/mcp.json`.

**3. Check it, then restart**

```bash
lura mcp                      # starts each server and lists the tools Lura will see
systemctl --user restart lura
```

Example output:

```
  • files: npx -y @modelcontextprotocol/server-filesystem /tmp
Available tools (14):
  ✓ mcp_files_read_text_file: [files] Read the complete contents of a file …
  ✓ mcp_files_list_directory: [files] Get a detailed listing of all files …
  …
```

**4. Just ask.** For example: *"Lura, what's in my Documents folder?"* or
*"Lura, show my open pull requests."* The model decides which tool to call.

**The GitHub line in the morning briefing** uses a server named exactly `github`
(the tools `search_repositories` and `list_commits`). It currently searches
`user:mohamedjs`; change that in `src/lura/briefing.py` for your own account.

## Settings

Stored in `~/.config/lura/settings.json`. Change them with `lura config`, then restart.

```bash
lura config                    # show all (★ = the ones you'll most likely change)
lura config voice              # show one
lura config voice Charon       # change one
```

| Key | Default | Notes |
|---|---|---|
| `provider` | `gemini` | `gemini` or `openrouter` |
| `gemini_model` | `gemini-2.5-flash-native-audio-preview-12-2025` | Gemini Live model |
| `openrouter_model` | `google/gemini-2.5-flash` | Anything from `lura models` |
| `openrouter_stt_model` | `openai/whisper-1` | Speech-to-text (also `openai/gpt-4o-mini-transcribe`) |
| `openrouter_tts_model` / `_voice` | `openai/gpt-4o-mini-tts` / `alloy` | Speaks the reply |
| `voice` | `Puck` | Gemini Live voice |
| `language` | `en-US` | `ar-XA` for Arabic |
| `wake_word` | `lura` | Must be a word in the offline English model. `lura` is mapped to `laura`. |
| `silence_ms` / `silence_factor` | `900` / `2.0` | OpenRouter only: when your turn ends |
| `idle_timeout` | `180` | Seconds of quiet before the conversation closes |
| `gate_mic_while_speaking` | `true` | Mutes the mic while she talks, so she can't hear and interrupt herself |
| `input_device` / `output_device` | `null` | Device indices printed by `selftest` |

## The service

```bash
systemctl --user status  lura
systemctl --user restart lura
journalctl --user -u lura -f          # watch it work
```

Exit code **78** means *a human has to fix something* (no key, no wake model), so
systemd won't restart it in a loop. Run `lura selftest` to see what's missing.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Nothing happens when you say the word | `journalctl --user -u lura`. *"Ignoring word missing in vocabulary"* means the wake word isn't in the model, so pick another. |
| Wake word works, but she doesn't hear you | `lura selftest` shows the mic level. Set `input_device` to the right index. |
| *"model not found"* | The preview model was retired. Set a current `gemini_model` / `openrouter_model`. |
| Gemini key error that looks like a bug | The key is older than Sept 2026. Create a new one. |
| She cuts you off / never stops listening (OpenRouter) | Raise `silence_ms` / `silence_factor`. |
| No sound devices under systemd, but works in a terminal | Re-run `./install.sh`. The unit needs `XDG_RUNTIME_DIR`. |
| An MCP server's tools don't show up | Run `lura mcp`. It starts each server in the foreground and logs why one fails. |

## ⚠️ Security: `run_command` runs real commands

`run_command` executes whatever shell command the model chooses, as **your user**,
with a 15-second timeout and **no allow-list or confirmation**. Treat Lura like
handing your keyboard to the model: don't run it on a machine where a wrong
`rm` would hurt, and be careful which MCP servers (and which tokens) you give it.

## Project layout

```
src/lura/
  app.py         CLI, provider routing, selftest
  config.py      settings + per-provider keys (0600 JSON)
  wake.py        offline wake word (Vosk), chime, recorder
  live.py        Gemini Live: full-duplex streaming audio + tools
  openrouter.py  OpenRouter: speech-to-text → chat → text-to-speech
  tools.py       built-in tools (apps, commands, system briefing)
  mcp_client.py  MCP client (stdio JSON-RPC)
  briefing.py    morning briefing
  overlay.py     floating face overlay
install.sh       idempotent installer
lura.service     systemd user unit
video/           promo video renderer (see video/README.md)
```

Open source on GitHub: **[mohamedjs/lura](https://github.com/mohamedjs/lura)**. Issues and PRs are welcome.
