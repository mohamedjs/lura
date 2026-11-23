"""Live smoke test for tiny-voca-arabic using the real data dir (not in run_all)."""

from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lura.modules.voice.domain import OUTPUT_RATE
from lura.modules.voice.infrastructure import arabic_voice

SENTENCES = [
    "تمام يا محمد، ضفت التاسك على نوشن.",
    "عندك اجتماع الساعة 3 العصر.",
]


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    out_dir.mkdir(parents=True, exist_ok=True)
    if not arabic_voice.ensure():
        print("Failed to install the offline Arabic voice.", file=sys.stderr)
        return 1
    tts = arabic_voice.TinyVocaArabicTTS()
    for i, sentence in enumerate(SENTENCES, 1):
        t0 = time.perf_counter()
        pcm = tts.synthesize(sentence)
        took = time.perf_counter() - t0
        if not pcm:
            print(f"[{i}] empty clip for: {sentence}", file=sys.stderr)
            return 1
        seconds = len(pcm) / (2 * OUTPUT_RATE)
        target = out_dir / f"arabic-voice-{i}.wav"
        with wave.open(str(target), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(OUTPUT_RATE)
            wf.writeframes(pcm)
        print(f"[{i}] {seconds:.2f} s audio in {took:.2f} s -> {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
