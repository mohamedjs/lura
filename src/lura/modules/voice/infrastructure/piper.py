"""Piper: an offline voice for when every online one has failed.

Free, no key, no daily limit. One Arabic voice (Modern Standard, male; Piper
has no other) and one English voice, picked per sentence by script. It
sounds flatter than Gemini, so it is only ever the last resort.

The Piper program ships inside the Linux and Windows builds (``piper/`` next
to the executable); anywhere else, and for the voices, it is downloaded once
into ``<data>/piper``. The voices are fetched from Hugging Face rather than
shipped: their datasets carry their own licences.
"""

from __future__ import annotations

import json
import logging
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.request
import zipfile
from pathlib import Path

from ....shared import paths
from ....shared.platform import FROZEN, IS_MAC, IS_WINDOWS
from .audio import resample

log = logging.getLogger(__name__)

RELEASE = "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/"
VOICES_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/"
VOICES = {"ar": "ar/ar_JO/kareem/medium/ar_JO-kareem-medium",
          "en": "en/en_US/lessac/medium/en_US-lessac-medium"}
_ARABIC = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
_EXE = "piper.exe" if IS_WINDOWS else "piper"
_install_lock = threading.Lock()


def home() -> Path:
    return paths.data_dir() / "piper"


def _asset() -> str:
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if IS_WINDOWS:
        return "piper_windows_amd64.zip"
    if IS_MAC:
        return "piper_macos_aarch64.tar.gz" if arm else "piper_macos_x64.tar.gz"
    return "piper_linux_aarch64.tar.gz" if arm else "piper_linux_x86_64.tar.gz"


def program() -> Path | None:
    """The Piper executable: bundled with Lura, downloaded before, or on PATH."""
    places = [home() / "piper" / _EXE]
    if FROZEN:
        places.insert(0, Path(sys.executable).parent / "piper" / _EXE)
    for place in places:
        if place.is_file():
            return place
    found = shutil.which("piper-tts") or None   # apt's `piper` is a mouse configurator
    return Path(found) if found else None


def voice(lang: str) -> Path:
    return home() / "voices" / (VOICES[lang].rsplit("/", 1)[-1] + ".onnx")


def ready() -> bool:
    return program() is not None and all(voice(lang).is_file() for lang in VOICES)


def _download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    with urllib.request.urlopen(url, timeout=60) as response, open(part, "wb") as out:
        shutil.copyfileobj(response, out, 1 << 16)
    part.replace(target)


def ensure() -> bool:
    """Download whatever is missing (program ~25 MB, voices ~63 MB each). Idempotent."""
    with _install_lock:
        if ready():
            return True
        try:
            if program() is None:
                log.info("Installing the offline voice (Piper)…")
                home().mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(dir=home()) as tmp:
                    archive = Path(tmp) / _asset()
                    _download(RELEASE + _asset(), archive)
                    if archive.suffix == ".zip":
                        with zipfile.ZipFile(archive) as zf:
                            zf.extractall(tmp)
                    else:
                        with tarfile.open(archive) as tf:
                            try:
                                tf.extractall(tmp, filter="data")   # keeps exec bits and in-folder symlinks
                            except TypeError:                      # Python before 3.10.12
                                tf.extractall(tmp)
                    if (home() / "piper").exists():
                        shutil.rmtree(home() / "piper")
                    home().mkdir(parents=True, exist_ok=True)
                    shutil.move(str(Path(tmp) / "piper"), home() / "piper")
                if IS_MAC:   # Apple Silicon refuses unsigned code; an ad-hoc signature per file is enough
                    for f in (home() / "piper").iterdir():
                        if f.is_file() and (f.suffix == ".dylib" or f.stat().st_mode & 0o111):
                            subprocess.run(["codesign", "--force", "-s", "-", str(f)], capture_output=True)
            for lang, name in VOICES.items():
                if not voice(lang).is_file():
                    log.info("Downloading the offline %s voice…", lang)
                    _download(VOICES_URL + name + ".onnx.json", voice(lang).with_name(voice(lang).name + ".json"))
                    _download(VOICES_URL + name + ".onnx", voice(lang))
        except Exception as exc:
            log.warning("Could not install the offline voice: %s", exc)
            return False
        log.info("Offline voice ready (%s).", program())
        return True


class PiperTTS:
    """Implements ``SpeechSynthesizer`` with a Piper process per sentence."""

    def synthesize(self, text: str) -> bytes:
        exe = program()
        if exe is None or not ready():
            raise RuntimeError("the offline voice is not installed yet")
        model = voice("ar" if _ARABIC.search(text) else "en")
        rate = json.loads(model.with_name(model.name + ".json").read_text())["audio"]["sample_rate"]
        result = subprocess.run(
            [str(exe), "--model", str(model), "--output_raw", "--quiet",
             "--espeak_data", str(exe.parent / "espeak-ng-data")],
            input=text.replace("\n", " ").encode(), capture_output=True, timeout=60, cwd=exe.parent,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode or not result.stdout:
            raise RuntimeError(f"Piper failed: {result.stderr.decode(errors='replace')[-300:]}")
        return resample(result.stdout, rate)

    def stream(self, text: str):
        yield self.synthesize(text)
