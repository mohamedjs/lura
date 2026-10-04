"""Arabic speech-to-text through OpenRouter (default: openai/gpt-4o-mini-transcribe).

  python3 transcribe.py clip.wav                  # whole file
  python3 transcribe.py video.mp4 --window 4      # 4 s windows → rough timing for captions
  python3 transcribe.py build/line*.wav --expect  # compare with voice.py's LINES

Key: OPENROUTER_TOKEN, OPENROUTER_API_KEY, or the "openrouter" key saved by `lura login`.
Errors from OpenRouter are printed verbatim (invalid key and out-of-credit need different fixes).
"""

import argparse
import base64
import difflib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

URL = "https://openrouter.ai/api/v1/audio/transcriptions"
MODEL = "openai/gpt-4o-mini-transcribe"


def openrouter_key() -> str:
    for name in ("OPENROUTER_TOKEN", "OPENROUTER_API_KEY"):
        if os.environ.get(name, "").strip():
            return os.environ[name].strip()
    keys = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "lura/keys.json"
    try:
        return json.loads(keys.read_text()).get("openrouter", "").strip()
    except (OSError, json.JSONDecodeError):
        return ""


def to_wav(path: str, start: float | None = None, dur: float | None = None) -> bytes:
    """Any audio/video → 16 kHz mono WAV bytes (optionally a time slice)."""
    cmd = ["ffmpeg", "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", path]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    r = subprocess.run(cmd + ["-vn", "-ac", "1", "-ar", "16000", "-f", "wav", "-"], capture_output=True)
    if r.returncode:
        sys.exit(f"ffmpeg could not read {path}:\n{r.stderr.decode(errors='replace')}")
    return r.stdout


def transcribe(wav: bytes, key: str, model: str = MODEL, language: str = "ar") -> str:
    payload = {"model": model, "file": base64.b64encode(wav).decode(), "filename": "speech.wav",
               "language": language}
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(), method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/mohamedjs/lura", "X-Title": "Lura video"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return (json.loads(resp.read()).get("text") or "").strip()
    except urllib.error.HTTPError as err:
        sys.exit(f"OpenRouter /audio/transcriptions → HTTP {err.code}\n{err.read().decode(errors='replace')[:800]}")
    except urllib.error.URLError as err:
        sys.exit(f"Cannot reach OpenRouter: {err.reason}")


_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")


def normalize(s: str) -> str:
    """Compare words, not spelling variants: drop tashkeel/tatweel/punctuation, unify alef/ya/ta marbuta."""
    s = _DIACRITICS.sub("", s)
    s = re.sub("[إأآٱ]", "ا", s).replace("ى", "ي").replace("ة", "ه")
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.split())


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize(a).split(), normalize(b).split()).ratio()


def duration(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout
    return float(out.strip() or 0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--language", default="ar")
    ap.add_argument("--window", type=float, help="transcribe in N-second windows and print timings")
    ap.add_argument("--expect", action="store_true", help="compare lineN.wav with voice.py's LINES")
    args = ap.parse_args()

    key = openrouter_key() or sys.exit(
        "No OpenRouter key. Set OPENROUTER_TOKEN (cloud: environment settings, then a new session) "
        "or run `lura login --provider openrouter`.")
    lines = []
    if args.expect:
        sys.path.insert(0, str(Path(__file__).parent))
        from voice import LINES as lines  # noqa: N811

    for f in args.files:
        if args.window:
            total, t = duration(f), 0.0
            while t < total - 0.2:
                text = transcribe(to_wav(f, t, args.window), key, args.model, args.language)
                print(f"{t:6.1f}–{min(t + args.window, total):5.1f}s  {text}")
                t += args.window
            continue
        text = transcribe(to_wav(f), key, args.model, args.language)
        m = re.search(r"line(\d)", Path(f).name)
        if args.expect and m:
            want = lines[int(m.group(1)) - 1]
            score = similarity(want, text)
            flag = "✓" if score >= 0.75 else "✗ CHECK"
            print(f"{flag} {Path(f).name} {score:.0%}\n   want: {want}\n   got:  {text}")
        else:
            print(f"{Path(f).name}: {text}")


if __name__ == "__main__":
    main()
