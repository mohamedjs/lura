"""Skills: added from the dashboard, listed in the prompt, read on demand."""

from __future__ import annotations

import base64
import logging
from typing import Any

from ..brain import ToolSpec
from .domain import NAME, prompt_block
from .infrastructure import SkillRepository

log = logging.getLogger(__name__)

USE_SKILL = "use_skill"
READ_SKILL_FILE = "read_skill_file"

TOOLS = (
    ToolSpec(USE_SKILL, "Read a skill's instructions (and the list of its files) before doing a task "
             "the skill covers. Follow them; run its scripts with run_command.",
             {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
    ToolSpec(READ_SKILL_FILE, "Read one file that a skill's instructions point to.",
             {"type": "object", "properties": {"name": {"type": "string"}, "path": {"type": "string"}},
              "required": ["name", "path"]}),
)


class SkillsService:
    def __init__(self, repository: SkillRepository | None = None):
        self._repo = repository or SkillRepository()

    def _enabled(self):
        return [skill for skill, on, _ in self._repo.all() if on]

    def list(self) -> list[dict[str, Any]]:
        return [{"name": s.name, "description": s.description, "enabled": on, "files": files}
                for s, on, files in self._repo.all()]

    def add(self, kind: str, body: dict[str, Any]) -> str:
        if kind == "paste":
            return self._repo.add_text(str(body.get("text", "")))
        if kind == "zip":
            try:
                data = base64.b64decode(str(body.get("data", "")), validate=True)
            except ValueError:
                raise ValueError("the zip did not arrive intact") from None
            return self._repo.add_zip(data)
        if kind == "url":
            return self._repo.add_url(str(body.get("url", "")).strip())
        raise ValueError("kind must be paste, zip or url")

    def set_enabled(self, name: str, enabled: bool) -> None:
        self._check(name)
        self._repo.set_enabled(name, enabled)

    def remove(self, name: str) -> bool:
        return self._repo.remove(self._check(name))

    @staticmethod
    def _check(name: str) -> str:
        if not NAME.match(name):
            raise ValueError(f"no skill {name!r}")
        return name

    def prompt_block(self) -> str:
        try:
            return prompt_block(self._enabled())
        except OSError as exc:
            log.warning("Skills unavailable: %s", exc)
            return ""

    # ── tools for the model (the tools module's provider port) ──
    def tool_specs(self) -> list[ToolSpec]:
        return list(TOOLS) if self._enabled() else []

    def owns(self, name: str) -> bool:
        return name in (USE_SKILL, READ_SKILL_FILE)

    def call(self, name: str, args: dict[str, Any]) -> str:
        skill_name = str(args.get("name", "")).strip()
        enabled = {s.name: s for s in self._enabled()}
        if skill_name not in enabled:
            return f"No enabled skill is called {skill_name!r}. Skills: {', '.join(enabled) or 'none'}."
        if name == READ_SKILL_FILE:
            try:
                return self._repo.read_file(skill_name, str(args.get("path", "")))
            except ValueError as exc:
                return f"Error: {exc}"
        files = self._repo.files(skill_name)
        listing = "\n".join(f"- {f}" for f in files) or "(none)"
        return (f"{enabled[skill_name].body}\n\n---\nSkill folder: {self._repo.folder(skill_name)}\n"
                f"Files (read with read_skill_file; run scripts with run_command from that folder):\n{listing}")
