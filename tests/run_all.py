"""Run every check. No pytest on purpose: each file is a plain script."""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECKS = ["test_architecture.py", "test_command_guard.py", "test_domain.py",
          "test_integration.py", "test_dashboard.py"]


def main() -> int:
    # Windows consoles are not UTF-8 by default; the checks print "──" and Arabic.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    failed = []
    for name in CHECKS:
        print(f"── {name}")
        if subprocess.run([sys.executable, str(HERE / name)], cwd=HERE.parent, env=env).returncode != 0:
            failed.append(name)
    print("\n" + ("All checks passed." if not failed else f"FAILED: {', '.join(failed)}"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
