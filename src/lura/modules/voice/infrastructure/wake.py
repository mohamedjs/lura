"""The wake word, offline: Vosk with a two-entry grammar.

The recogniser is told the only things it may hear are the wake word and
"[unk]". Open-vocabulary transcription of a room all day costs far more CPU
and accepts far more rubbish; this sits near idle and rarely fires by accident.

The word must be in the model's vocabulary — it is compiled into the FST, and
a missing word is logged once and then simply never fires. "lura" is not in
the small English model, so it is listened for as "laura".
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from ....shared import paths
from ..domain import INPUT_RATE
from .audio import MicStream

log = logging.getLogger(__name__)

VOSK_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
ALIASES = {"lura": "laura"}


def model_dir() -> Path:
    return paths.data_dir() / "vosk-model"


def ensure_model(progress=None) -> Path:
    """Download the ~40 MB model on first run. Idempotent."""
    target = model_dir()
    if (target / "am").exists() or (target / "conf").exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    log.info("Downloading the wake-word model (~40 MB, once)…")
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        archive = Path(tmp) / "model.zip"
        with urllib.request.urlopen(VOSK_URL, timeout=60) as response, open(archive, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while chunk := response.read(1 << 16):
                out.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done / total)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(tmp)
        inner = next(p for p in Path(tmp).iterdir() if p.is_dir())
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(inner), target)
    log.info("Wake-word model ready at %s", target)
    return target


class WakeListener:
    """Blocks until the wake word is heard.

    Holds the microphone only while waiting: the conversation needs the same
    device, so ``listen()`` opens and closes the stream itself.
    """

    def __init__(self, wake_word: str, input_device=None, cooldown: float = 1.5):
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)
        self._word = wake_word.lower().strip()
        self._device = input_device
        self._cooldown = cooldown
        heard_as = ALIASES.get(self._word, self._word)
        self._accepted = {self._word, heard_as}
        self._recognizer = KaldiRecognizer(Model(str(ensure_model())), INPUT_RATE,
                                           json.dumps([heard_as, "[unk]"]))

    def _heard(self, payload: str) -> bool:
        try:
            text = json.loads(payload).get("text", "") or json.loads(payload).get("partial", "")
        except json.JSONDecodeError:
            return False
        return bool(set(text.lower().split()) & self._accepted)

    def listen(self, stop, interrupt=None, reload=None) -> bool:
        """Wait for the wake word. True if heard or ``interrupt`` (an Event,
        e.g. "talk now" from the dashboard) is set; False if ``stop`` or
        ``reload`` (the microphone changed) is set."""
        with MicStream(self._device, block_seconds=0.25) as mic:
            log.info("Listening for %r…", self._word)
            while not stop.is_set():
                if reload is not None and reload.is_set():
                    return False
                if interrupt is not None and interrupt.is_set():
                    interrupt.clear()
                    return True
                block = mic.read(timeout=0.3)
                if block is None:
                    continue
                if self._recognizer.AcceptWaveform(block):
                    if self._heard(self._recognizer.Result()):
                        return True
                elif self._heard(self._recognizer.PartialResult()):
                    # Partials fire the moment the word lands, not when the
                    # recogniser decides the phrase ended: snappy, not sluggish.
                    self._recognizer.Reset()
                    return True
        return False

    def cooldown(self) -> None:
        """Swallow the tail of the wake word so it cannot re-trigger."""
        time.sleep(self._cooldown)
        self._recognizer.Reset()
