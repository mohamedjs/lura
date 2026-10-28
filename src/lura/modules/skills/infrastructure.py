"""Skills on disk: ``<config>/skills/<name>/SKILL.md`` plus files, and
``skills.json`` for which are switched on."""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from ...shared import paths
from .domain import MAX_SKILL_MD, Skill, parse

MAX_ZIP = 20_000_000          # uncompressed, all members
MAX_FILES = 500
MAX_DOWNLOAD = 5_000_000


def skills_dir() -> Path:
    return paths.config_dir() / "skills"


def inside(root: Path, rel: str) -> Path:
    """``root / rel``, refusing anything that would land outside ``root``."""
    target = (root / rel).resolve()
    if target != root.resolve() and root.resolve() not in target.parents:
        raise ValueError(f"{rel!r} is outside the skill folder")
    return target


class SkillRepository:
    def __init__(self, root: Path | None = None):
        self.root = root or skills_dir()

    def _flags(self) -> dict[str, bool]:
        try:
            return json.loads((self.root / "skills.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def set_enabled(self, name: str, enabled: bool) -> None:
        flags = self._flags()
        flags[name] = enabled
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "skills.json").write_text(json.dumps(flags, indent=2) + "\n", encoding="utf-8")

    def all(self) -> list[tuple[Skill, bool, list[str]]]:
        """(skill, enabled, its other files) for every readable skill folder."""
        flags = self._flags()
        out = []
        for folder in sorted(self.root.glob("*/SKILL.md")):
            try:
                skill = parse(folder.read_text(encoding="utf-8"), folder.parent.name)
            except (OSError, ValueError, UnicodeDecodeError):
                continue
            out.append((skill, flags.get(folder.parent.name, True), self.files(folder.parent.name)))
        return out

    def get(self, name: str) -> Skill:
        return parse(inside(self.root, f"{name}/SKILL.md").read_text(encoding="utf-8"), name)

    def folder(self, name: str) -> Path:
        return inside(self.root, name)

    def files(self, name: str) -> list[str]:
        base = self.folder(name)
        return sorted(str(p.relative_to(base)) for p in base.rglob("*")
                      if p.is_file() and p.name != "SKILL.md")[:200]

    def read_file(self, name: str, rel: str, limit: int = 60_000) -> str:
        target = inside(self.folder(name), rel)
        if not target.is_file():
            raise ValueError(f"{name} has no file {rel}")
        return target.read_bytes()[:limit].decode("utf-8", errors="replace")

    def remove(self, name: str) -> bool:
        folder = self.folder(name)
        if not folder.is_dir():
            return False
        shutil.rmtree(folder)
        flags = self._flags()
        if flags.pop(name, None) is not None:
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / "skills.json").write_text(json.dumps(flags, indent=2) + "\n", encoding="utf-8")
        return True

    # ── adding ──
    def add_text(self, text: str) -> str:
        skill = parse(text)
        folder = self.folder(skill.name)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(text, encoding="utf-8")
        return skill.name

    def add_zip(self, data: bytes) -> str:
        """A zipped skill folder: SKILL.md at the top, or inside one folder."""
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise ValueError("that is not a zip file") from None
        members = [m for m in archive.infolist() if not m.is_dir()]
        if len(members) > MAX_FILES or sum(m.file_size for m in members) > MAX_ZIP:
            raise ValueError("the zip is too large for a skill")
        tops = sorted((m for m in members if PurePosixPath(m.filename).name == "SKILL.md"),
                      key=lambda m: len(PurePosixPath(m.filename).parts))
        if not tops:
            raise ValueError("the zip has no SKILL.md")
        prefix = PurePosixPath(tops[0].filename).parent
        if tops[0].file_size > MAX_SKILL_MD:
            raise ValueError("SKILL.md is too large")
        skill = parse(archive.read(tops[0]).decode("utf-8", errors="replace"), prefix.name)
        self.root.mkdir(parents=True, exist_ok=True)
        # Unpacked beside the target, then swapped in, so a bad member
        # leaves the old version of the skill untouched.
        with tempfile.TemporaryDirectory(dir=self.root) as tmp:
            staging = Path(tmp) / skill.name
            staging.mkdir()
            for member in members:
                path = PurePosixPath(member.filename)
                if prefix.parts and path.parts[:len(prefix.parts)] != prefix.parts:
                    continue
                rel = PurePosixPath(*path.parts[len(prefix.parts):])
                target = inside(staging, str(rel))   # zip-slip: "../x" or "/etc/x" stops here
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(member))
            final = self.folder(skill.name)
            if final.exists():
                shutil.rmtree(final)
            shutil.move(str(staging), final)
        return skill.name

    def add_url(self, url: str) -> str:
        """A raw SKILL.md or a zip over https."""
        if not url.lower().startswith("https://"):
            raise ValueError("the link must start with https://")
        request = urllib.request.Request(url, headers={"User-Agent": "Lura"})
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read(MAX_DOWNLOAD + 1)
        if len(data) > MAX_DOWNLOAD:
            raise ValueError("that download is too large for a skill")
        if data[:4] == b"PK\x03\x04":
            return self.add_zip(data)
        return self.add_text(data.decode("utf-8", errors="replace"))
