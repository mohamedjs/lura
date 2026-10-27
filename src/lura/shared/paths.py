"""Where Lura keeps its files, per operating system.

Linux keeps the directories the app has always used (``~/.config/lura`` and
``~/.local/share/lura``): they hold users' keys and a 68 MB wake-word model,
and renaming them would strand both. macOS and Windows get their platform's
usual places.

``LURA_CONFIG_DIR`` / ``LURA_DATA_DIR`` override both, which is what the
checks use to run without touching a real profile.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .platform import FROZEN, IS_MAC, IS_WINDOWS


def config_dir() -> Path:
    if override := os.environ.get("LURA_CONFIG_DIR"):
        return Path(override)
    if IS_WINDOWS:
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "Lura"
    if IS_MAC:
        return Path.home() / "Library/Application Support/Lura"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "lura"


def data_dir() -> Path:
    if override := os.environ.get("LURA_DATA_DIR"):
        return Path(override)
    if IS_WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Lura"
    if IS_MAC:
        return Path.home() / "Library/Application Support/Lura/data"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "lura"


def resource_dir() -> Path:
    """The installed ``lura`` package directory, frozen or not."""
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "lura"
    return Path(__file__).resolve().parents[1]


def write_private(path: Path, text: str) -> None:
    """Write a file only its owner can read, created that way from the start.

    Never widened then narrowed, so a secret is not briefly world-readable.
    On Windows the mode is ignored; the profile directory is already private.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
