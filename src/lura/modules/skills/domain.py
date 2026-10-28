"""Skills in the Agent Skills format: a folder with SKILL.md and optional files.

SKILL.md opens with front matter (``name``, ``description``) and the rest is
instructions. That is only prompt and tools, so a skill works with every
brain: Claude, GPT, Gemini, anything on OpenRouter. Lura sees the one-line
descriptions in her prompt and loads a body only when she uses the skill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_SKILL_MD = 200_000


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str


def slug(text: str) -> str:
    """A name safe as a folder and a URL part: lowercase letters, digits, dashes."""
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]", "-", text.strip().lower())).strip("-")[:64]


def parse(text: str, fallback_name: str = "") -> Skill:
    """SKILL.md → Skill. Raises ValueError with a reason a person can act on.

    The front matter is read by hand (``key: value``, quotes stripped,
    indented lines continue the previous key) rather than with a YAML
    library: those two fields are all Lura needs.
    """
    if len(text) > MAX_SKILL_MD:
        raise ValueError("SKILL.md is too large")
    text = text.lstrip("﻿")
    meta: dict[str, str] = {}
    body = text
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
        if end is None:
            raise ValueError("SKILL.md front matter has no closing ---")
        key = None
        for line in lines[1:end]:
            if key and line[:1] in (" ", "\t") and line.strip():
                meta[key] = f"{meta[key]} {line.strip()}".strip()
            elif ":" in line:
                key, value = line.split(":", 1)
                key = key.strip().lower()
                value = value.strip()
                meta[key] = "" if value in ("|", ">", "|-", ">-") else value.strip("'\"")
        body = "\n".join(lines[end + 1:]).strip()
    name = slug(meta.get("name") or fallback_name)
    if not NAME.match(name):
        raise ValueError("the skill needs a name (front matter `name:`)")
    description = " ".join(meta.get("description", "").split())
    if not description:
        raise ValueError("the skill needs a description (front matter `description:`): "
                         "it is how Lura knows when to use it")
    return Skill(name, description[:1024], body)


def prompt_block(skills: list[Skill]) -> str:
    if not skills:
        return ""
    listing = "\n".join(f"- {s.name}: {s.description}" for s in skills)
    return ("Skills you have (call use_skill with the name to read its instructions before doing "
            f"a task it covers):\n{listing}")
