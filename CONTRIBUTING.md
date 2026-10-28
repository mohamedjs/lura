# Contributing to Lura

Lura is open source (MIT) and free: there is no paid plan. Issues and pull
requests are welcome, with one rule up front: **the maintainer,
[@mohamedjs](https://github.com/mohamedjs), reviews and approves every change.**
Nothing is merged without that review, and issues are triaged by him too.

## Before you open a pull request

1. Open an issue first for anything bigger than a small fix, so we agree on the
   approach before you spend time on it.
2. Read [`AGENTS.md`](AGENTS.md) (facts established by testing: audio, wake word,
   packaging) and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (modules meet
   only through their package `__init__`).
3. Run the checks; none of them need API keys, a microphone or a display:

   ```bash
   python -m compileall -q src/
   python tests/run_all.py
   cd src && python -m lura.modules.overlay
   ```

4. Keep files under ~500 lines, add a test for new logic, and keep every
   dashboard string in both `src/lura/shared/i18n/en.json` and `ar.json`.

## Reporting a bug

Use the bug template and include the end of the log
(`journalctl --user -u lura -n 200` on Linux, or `lura.log` in the data folder).
**Never paste API keys** — the log does not contain them, but `keys.json` and
`mcp.json` do.

## Security

Report anything that could let a page, a model or a connector run commands the
command guard should block privately, through GitHub's "Report a vulnerability"
on the repository's Security tab, not in a public issue.
