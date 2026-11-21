"""Persistent storage for workspace presets at config_dir()/presets.json."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from ....shared.paths import config_dir
from ..domain import Preset, PresetApp, Rect

log = logging.getLogger(__name__)


def _path() -> Path:
    return config_dir() / "presets.json"


def load() -> dict[str, Preset]:
    path = _path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("presets"), dict):
            log.warning("Ignoring presets file with unknown format: %s", path)
            return {}
        out: dict[str, Preset] = {}
        for name, item in raw["presets"].items():
            entries: list[PresetApp] = []
            for a in item["apps"]:
                raw_rect = a.get("rect")
                if raw_rect is None:
                    rect = None
                else:
                    rx, ry, rw, rh = raw_rect
                    rect = Rect(int(rx), int(ry), int(rw), int(rh))
                entries.append(
                    PresetApp(
                        launch=str(a["launch"]),
                        match=str(a["match"]),
                        where=str(a.get("where", "")),
                        rect=rect,
                        monitor=int(a.get("monitor", 0)),
                        workspace=int(a.get("workspace", 0)),
                    )
                )
            out[str(name)] = Preset(
                name=str(name),
                apps=tuple(entries),
                saved_at=str(item["saved_at"]),
            )
        return out
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("Could not read presets file %s: %s", path, exc)
        return {}


def save(presets: dict[str, Preset]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "presets": {
            name: {
                "saved_at": preset.saved_at,
                "apps": [
                    {
                        "launch": a.launch,
                        "match": a.match,
                        "where": a.where,
                        "rect": (
                            [a.rect.x, a.rect.y, a.rect.width, a.rect.height]
                            if a.rect is not None
                            else None
                        ),
                        "monitor": a.monitor,
                        "workspace": a.workspace,
                    }
                    for a in preset.apps
                ],
            }
            for name, preset in presets.items()
        },
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".presets-", suffix=".tmp", dir=path.parent)
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
