"""Persistent storage for saved window layouts at config_dir()/layouts.json."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from ....shared.paths import config_dir
from ..domain import Layout, LayoutEntry, Rect

log = logging.getLogger(__name__)


def _path() -> Path:
    return config_dir() / "layouts.json"


def load() -> dict[str, Layout]:
    path = _path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("layouts"), dict):
            log.warning("Ignoring layouts file with unknown format: %s", path)
            return {}
        out: dict[str, Layout] = {}
        for name, item in raw["layouts"].items():
            entries: list[LayoutEntry] = []
            for w in item["windows"]:
                rx, ry, rw, rh = w["rect"]
                entries.append(
                    LayoutEntry(
                        app=str(w["app"]),
                        title=str(w["title"]),
                        desktop=int(w["desktop"]),
                        rect=Rect(int(rx), int(ry), int(rw), int(rh)),
                        maximized=bool(w["maximized"]),
                    )
                )
            out[str(name)] = Layout(
                name=str(name),
                saved_at=str(item["saved_at"]),
                windows=tuple(entries),
            )
        return out
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("Could not read layouts file %s: %s", path, exc)
        return {}


def save(layouts: dict[str, Layout]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "layouts": {
            name: {
                "saved_at": lay.saved_at,
                "windows": [
                    {
                        "app": w.app,
                        "title": w.title,
                        "desktop": w.desktop,
                        "rect": [w.rect.x, w.rect.y, w.rect.width, w.rect.height],
                        "maximized": w.maximized,
                    }
                    for w in lay.windows
                ],
            }
            for name, lay in layouts.items()
        },
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".layouts-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
