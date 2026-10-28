"""Skills: SKILL.md parsing, zip/paste import, path confinement, the tools, the prompt."""

import _setup  # noqa: F401
import base64
import io
import sys
import tempfile
import zipfile
from pathlib import Path

SKILL = """---
name: Release Notes
description: >
  Write release notes from the git log,
  in the project's style.
license: MIT
---
# Release notes
Run `scripts/log.sh`, then read reference/style.md.
"""


def zipped(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buffer.getvalue()


def check_parse():
    from lura.modules.skills import parse

    skill = parse(SKILL)
    assert skill.name == "release-notes"
    assert skill.description == "Write release notes from the git log, in the project's style."
    assert skill.body.startswith("# Release notes")
    assert parse('---\nname: "x"\ndescription: \'Does x\'\n---\nbody').description == "Does x"
    for bad, why in (("no front matter", "name"), ("---\nname: a\n---\n", "description"),
                     ("---\nname: a\ndescription: b\n", "closing")):
        try:
            parse(bad)
        except ValueError as exc:
            assert why in str(exc), exc
        else:
            raise AssertionError(f"accepted {bad!r}")


def check_import_and_tools():
    from lura.modules.skills import SkillsService
    from lura.modules.skills.infrastructure import SkillRepository

    root = Path(tempfile.mkdtemp())
    svc = SkillsService(SkillRepository(root))
    assert svc.tool_specs() == [] and svc.prompt_block() == "", "no skills, no tools, nothing in the prompt"

    data = zipped({"release-notes/SKILL.md": SKILL, "release-notes/reference/style.md": "Short. Past tense.",
                   "release-notes/scripts/log.sh": "git log --oneline -20\n"})
    assert svc.add("zip", {"data": base64.b64encode(data).decode()}) == "release-notes"
    assert svc.list()[0]["files"] == ["reference/style.md", "scripts/log.sh"]
    assert "- release-notes: Write release notes" in svc.prompt_block()
    assert [t.name for t in svc.tool_specs()] == ["use_skill", "read_skill_file"]
    used = svc.call("use_skill", {"name": "release-notes"})
    assert used.startswith("# Release notes") and "scripts/log.sh" in used and str(root.resolve()) in used   # resolved: macOS /private/var, Windows RUNNER~1
    assert svc.call("read_skill_file", {"name": "release-notes", "path": "reference/style.md"}) == "Short. Past tense."
    for escape in ("../../keys.json", "/etc/passwd", "reference/../../release-notes/../x"):
        assert svc.call("read_skill_file", {"name": "release-notes", "path": escape}).startswith("Error"), escape
    assert svc.call("use_skill", {"name": "nope"}).startswith("No enabled skill")

    evil = zipped({"SKILL.md": "---\nname: evil\ndescription: d\n---\n", "../../outside.txt": "x"})
    try:
        svc.add("zip", {"data": base64.b64encode(evil).decode()})
    except ValueError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("zip-slip accepted")
    assert not (root.parent / "outside.txt").exists() and not (root / "evil").exists()

    assert svc.add("paste", {"text": "---\nname: tea\ndescription: Make tea\n---\nBoil water."}) == "tea"
    svc.set_enabled("release-notes", False)
    assert svc.call("use_skill", {"name": "release-notes"}).startswith("No enabled skill")
    assert "release-notes" not in svc.prompt_block() and "tea" in svc.prompt_block()
    for bad in ({"kind": "url", "url": "http://example.com/SKILL.md"}, {"kind": "zip", "data": "!!"},
                {"kind": "zip", "data": base64.b64encode(b"not a zip").decode()}, {"kind": "what"}):
        try:
            svc.add(bad["kind"], bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted {bad}")
    try:
        svc.set_enabled("../x", True)
    except ValueError:
        pass
    else:
        raise AssertionError("bad name accepted")
    assert svc.remove("tea") and not svc.remove("tea") and [s["name"] for s in svc.list()] == ["release-notes"]


def main() -> int:
    for name, fn in list(globals().items()):
        if name.startswith("check_"):
            fn()
            print(f"  {name[6:]} OK")
    print("skills OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
