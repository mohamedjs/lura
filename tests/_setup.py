"""Shared by the checks: import from src/, and never touch a real profile."""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_tmp = tempfile.mkdtemp(prefix="lura-test-")
os.environ["LURA_CONFIG_DIR"] = str(Path(_tmp) / "config")
os.environ["LURA_DATA_DIR"] = str(Path(_tmp) / "data")
for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
    os.environ.pop(var, None)
