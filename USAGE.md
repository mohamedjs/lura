# Using Lura

Day-to-day guide. Installing: [README.md](README.md).

## Talking

Say **"Lura"**. You hear a **rising beep**, then ask. She answers out loud. Keep
talking without the wake word; when you stop for a while, or say goodbye, you hear
a **falling beep** and she goes back to waiting.

No wake word: press the big button in the dashboard, pick **Talk now** from the
tray icon, or run `lura say`.

The face top-right shows what she is doing: calm while listening, turning while
thinking, the mouth moving while speaking. Drag it anywhere. **Double-click it to
open the dashboard.**

## The dashboard

`lura dashboard` (or double-click the face, or use the tray menu). Everything is here:

- **API keys:** paste, *Test & save*. Each key is checked before it is stored.
- **Brain:** *Any model* (pipeline) or *Realtime* (Gemini Live). Pick the provider
  and model; *Thinking effort* trades speed for depth (minimal is fastest).
- **Voice:** Gemini directly or through OpenRouter, female or male, 30 voices.
  Press ▶ to hear one.
- **Language & you:** conversation language (Arabic answers in Egyptian Arabic),
  your name, the assistant's name and the wake word.
- **Microphone:** pick the microphone and speaker, test them, set the pause before
  she answers and how long she waits for your next sentence.
- **Memory:** what she knows about you (`memory.md`), past conversations, search,
  start fresh.
- **Skills** and **Connectors:** add your own (below); built-in Chrome, web and files.
- **Settings:** morning briefing, start at login.

## Things to ask

- "Open Spotify." · "What's running?" · "Find my invoice PDF."
- "Open YouTube and play lo-fi." · "Open the news on Chrome and read me the headlines."
- "How's the computer doing?" (CPU temperature, memory, load, internet)
- "What's in my Downloads folder?"
- Anything your connectors do: "list my GitHub pull requests".

She will not delete or overwrite anything; that is blocked in code. Ask her to
look, then do the deleting yourself.

## Adding a connector

In **Connectors → Add a connector**, pick one of:

1. **URL + token:** a remote MCP server, e.g. GitHub's
   `https://api.githubcopilot.com/mcp/` with a personal access token.
2. **mcp.json:** paste the JSON from the server's README:
   ```json
   {"mcpServers": {"github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
     "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_…"}}}}
   ```
3. **Command:** `npx -y <package>` plus environment lines.

From the terminal: `lura mcp add-url <name> <url>`, `lura mcp import <file.json>`,
`lura mcp list`.

## When something is wrong

```bash
lura selftest          # walks every layer and stops at the first real failure
```

Logs: `journalctl --user -u lura -f` (Linux service), or `lura.log` in the data
folder (see README → Where things live).

| Symptom | Fix |
|---|---|
| Never wakes | `lura selftest` mic check; set the microphone in Advanced |
| Cuts you off / never stops listening | Advanced → turn-end pause, speech sensitivity |
| "Needs setup" | add a Gemini or OpenRouter key; voice needs one of them |
| Connector "failed" | install Node.js (npx); for URL connectors check the token |
| Answers in the wrong language | Language & you → conversation language |
