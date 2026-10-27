"""The modular monolith's one rule: modules meet only through their packages.

Inside ``lura.modules.<x>`` anything goes. From outside, ``<x>`` may be
imported only as the package (its ``__init__``), never ``<x>.infrastructure``
or ``<x>.application`` — that is what keeps a module replaceable. The
composition root (bootstrap) and the CLI follow the same rule.
"""

import ast
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "lura"
MODULES = sorted(p.name for p in (SRC / "modules").iterdir() if (p / "__init__.py").exists())


def owner(path: Path) -> str | None:
    parts = path.relative_to(SRC).parts
    return parts[1] if parts[0] == "modules" and len(parts) > 2 else None


def resolve(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package = list(path.relative_to(SRC.parent).with_suffix("").parts)[:-1]
    if path.name == "__init__.py":
        package = list(path.relative_to(SRC.parent).parts)[:-1]
    base = package[: len(package) - node.level + 1]
    return ".".join(base + ([node.module] if node.module else []))


def main() -> int:
    bad = []
    for path in SRC.rglob("*.py"):
        mine = owner(path)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ImportFrom):
                continue
            target = resolve(path, node).split(".")
            if len(target) >= 3 and target[:2] == ["lura", "modules"] and target[2] in MODULES:
                other = target[2]
                if other != mine and len(target) > 3:
                    bad.append(f"{path.relative_to(SRC)}:{node.lineno} reaches into {'.'.join(target)}")
            if mine and target[:1] == ["lura"] and len(target) > 1 and target[1] in ("app", "bootstrap", "selftest"):
                bad.append(f"{path.relative_to(SRC)}:{node.lineno} imports the composition root")
    if bad:
        print("architecture FAILED:\n  " + "\n  ".join(bad))
        return 1
    print(f"architecture OK ({len(MODULES)} modules: {', '.join(MODULES)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
