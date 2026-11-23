"""tiny-voca-arabic: an offline Modern Standard Arabic voice on the CPU.

Sits in front of Piper for sentences containing Arabic script when every online
voice fails: Piper's only Arabic voice sounds flat, whereas tiny-voca-arabic
(4.5 M params, ONNX, 24 kHz) sounds much more natural and runs ~200x real time
on the CPU.

The model and CATT diacritizer (~114 MB) are downloaded once into
``<data>/arabic-voice/`` rather than shipped inside the installers, because
every in-app update downloads the full release artifact.

Credits (all Apache-2.0): tiny-voca by Muhammed Nazeem, tiny-voca-arabic by
oddadmix, and the CATT diacritizer by Abjad AI.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import re
import tempfile
import threading
import urllib.request
import zipfile
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from ....shared import paths
from .audio import OUTPUT_RATE, resample

log = logging.getLogger(__name__)

REVISION = "e529f716965142f51835adc9e5661f224c3fa103"
_BASE = f"https://huggingface.co/oddadmix/tiny-voca-arabic/resolve/{REVISION}/onnx/"
FILES: dict[str, tuple[str, str]] = {
    "model.onnx": (_BASE + "model.onnx", "1eb04ad05ec96808eac3cb761ddb7fc0e17edb636deb3263ccb13d99f725ab8f"),
    "model.json": (_BASE + "model.json", "c00789356049545702f7e42624b1bdef1b105c22c38832eaec364d4d5ea007cc"),
}
CATT_ZIP: tuple[str, str] = (
    "https://github.com/abjadai/catt/releases/download/v2/ed_model_onnx.zip",
    "75d2a6360725c7e0bbb238b5a950f621fb8e30b21ad7c65e91e0f36759bcb3ce",
)
THREADS = 2

_SENTENCE_END = re.compile(r"(?<=[.!?؟…])\s+(?=\S)")
_CLAUSE_END = re.compile(r"(?<=[،,;؛:])\s+")
_GAP = 0.12
_RATE = 24000

_install_lock = threading.Lock()
_load_lock = threading.Lock()
_engine = None


def home() -> Path:
    return paths.data_dir() / "arabic-voice"


def available() -> bool:
    return importlib.util.find_spec("onnxruntime") is not None


def ready() -> bool:
    if not available():
        return False
    root = home()
    return all((root / rel).is_file() for rel in (*FILES, "catt/encoder.onnx", "catt/decoder.onnx"))


def _download(url: str, target: Path, sha256: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=60) as response, open(part, "wb") as out:
            while chunk := response.read(1 << 16):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != sha256:
            part.unlink(missing_ok=True)
            raise ValueError(f"checksum mismatch for {url}")
        part.replace(target)
    except Exception:
        part.unlink(missing_ok=True)
        raise


def ensure() -> bool:
    """Download whatever is missing (~114 MB once). Idempotent; never raises."""
    with _install_lock:
        if ready():
            return True
        if not available():
            log.warning("The offline Arabic voice needs onnxruntime; using Piper for Arabic.")
            return False
        log.info("Downloading the offline Arabic voice (tiny-voca-arabic, ~110 MB once)…")
        try:
            root = home()
            for rel, (url, sha256) in FILES.items():
                target = root / rel
                if not target.is_file():
                    _download(url, target, sha256)
            catt_dir = root / "catt"
            if not (catt_dir / "encoder.onnx").is_file() or not (catt_dir / "decoder.onnx").is_file():
                root.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(dir=root) as tmp:
                    tmp_path = Path(tmp)
                    archive = tmp_path / "catt.zip"
                    _download(CATT_ZIP[0], archive, CATT_ZIP[1])
                    with zipfile.ZipFile(archive) as zf:
                        if set(zf.namelist()) != {"encoder.onnx", "decoder.onnx"}:
                            raise ValueError("unexpected files in the diacritizer archive")
                        for member in ("encoder.onnx", "decoder.onnx"):
                            zf.extract(member, tmp_path)
                    catt_dir.mkdir(parents=True, exist_ok=True)
                    for member in ("encoder.onnx", "decoder.onnx"):
                        (tmp_path / member).replace(catt_dir / member)
        except Exception as exc:
            log.warning("Could not install the offline Arabic voice: %s", exc)
            return False
        log.info("Offline Arabic voice ready (%s).", home())
        return True


def _load():
    global _engine
    with _load_lock:
        if _engine is not None:
            return _engine
        import onnxruntime as ort
        from .tiny_voca import arabic_frontend
        from .tiny_voca.catt import CATTEncoderDecoder

        root = home()
        meta = json.loads((root / "model.json").read_text(encoding="utf-8"))
        rate = int(meta.get("sample_rate", _RATE))
        max_tokens = int(meta["max_tokens"])

        options = ort.SessionOptions()
        options.intra_op_num_threads = THREADS
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(str(root / "model.onnx"), options, providers=["CPUExecutionProvider"])

        diacritizer = CATTEncoderDecoder(
            encoder_path=str(root / "catt" / "encoder.onnx"),
            decoder_path=str(root / "catt" / "decoder.onnx"),
            threads=THREADS,
        )
        arabic_frontend.set_diacritizer(diacritizer)
        _engine = (session, rate, max_tokens - 12)
        return _engine


def _pieces(text: str) -> list[str]:
    parts = [p for p in _CLAUSE_END.split(text) if p.strip()]
    if len(parts) > 1:
        return parts
    words = text.split()
    if len(words) < 2:
        return []
    half = len(words) // 2
    return [" ".join(words[:half]), " ".join(words[half:])]


class TinyVocaArabicTTS:
    """Implements ``SpeechSynthesizer`` with tiny-voca-arabic on CPU."""

    def synthesize(self, text: str) -> bytes:
        if not ready():
            raise RuntimeError("the offline Arabic voice is not installed yet")
        from .tiny_voca.numbers import expand_numbers
        from .tiny_voca.text import NothingToPronounce, phonemize

        session, rate, limit = _load()
        text = text.replace("\n", " ").strip()
        if not text:
            return b""
        if text[-1] not in ".!?؟…":
            text += "."

        waves: list[np.ndarray] = []
        gap = np.zeros(int(_GAP * rate), dtype=np.float32)

        def read(piece: str) -> None:
            try:
                phones = phonemize(piece)
            except NothingToPronounce:
                return
            if len(phones) <= limit:
                audio, _ = session.run(None, {
                    "tokens": np.array([phones.ids], dtype=np.int64),
                    "stress": np.array([phones.stress], dtype=np.int64),
                    "voice": np.array([0], dtype=np.int64),
                    "speed": np.array([1.0], dtype=np.float32),
                    "pitch_shift": np.array([0.0], dtype=np.float32),
                    "pitch_range": np.array([1.0], dtype=np.float32),
                })
                waves.extend([audio[0], gap])
                return
            for part in _pieces(piece):
                read(part)

        for sentence in _SENTENCE_END.split(expand_numbers(text)):
            read(sentence)
        if not waves:
            return b""
        audio = np.concatenate(waves[:-1])
        peak = float(np.abs(audio).max())
        if peak > 0.99:
            audio = audio * (0.99 / peak)
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        return resample(pcm, rate, OUTPUT_RATE)

    def stream(self, text: str) -> Iterator[bytes]:
        pcm = self.synthesize(text)
        if pcm:
            yield pcm
