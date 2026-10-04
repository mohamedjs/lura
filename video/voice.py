"""Narration for the Lura promo: five MSA lines → WAVs + timeline.json.

Engines (both Google):
  cloud   Google Cloud Text-to-Speech, key in GOOGLE_TTS_API_KEY
  gemini  Gemini TTS, key in GEMINI_API_KEY or Lura's own ~/.config/lura/keys.json

No key → no WAVs, and the timeline falls back to fixed durations so the
picture can still be rendered and checked. API errors are printed verbatim.
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"

LINES = [
    "تخيّل أنّ جهازك يسمعك… ويردّ عليك",
    "يكفي أن تناديها باسمها",
    "وكلَّ صباح، تطمئنك على جهازك… وعلى عملك",
    "وتفتح لك برامجك بصوتك",
    "لورا… مساعدتك الصوتية على لينكس",
]
# Used only when there is no audio: roughly what a calm narrator takes.
FALLBACK_SECONDS = [3.2, 2.2, 3.8, 2.4, 3.4]

LEAD_IN = 0.8      # picture before the first word
PRE_VOICE = 0.35   # each scene starts this long before its line
TAIL = 0.75        # breathing room after each line
OUTRO = 2.2        # logo hold after the last line
RATE = 24000


def _post(url: str, body: dict, headers: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as err:
        sys.exit(f"{url.split('?')[0]} → HTTP {err.code}\n{err.read().decode(errors='replace')}")


def cloud_tts(text: str, key: str, voice: str) -> bytes:
    """Returns a complete WAV file (Cloud TTS includes the header)."""
    out = _post(
        f"https://texttospeech.googleapis.com/v1/text:synthesize?key={key}",
        {
            "input": {"text": text},
            "voice": {"languageCode": "ar-XA", "name": voice},
            "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": RATE,
                            "speakingRate": 0.92},
        },
        {},
    )
    return base64.b64decode(out["audioContent"])


def gemini_tts(text: str, key: str, voice: str, model: str) -> bytes:
    """Returns raw PCM s16le mono 24 kHz."""
    out = _post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {
            "contents": [{"parts": [{"text":
                # Google's documented pattern: a short style cue, a colon, then the
                # words. Longer instructions risk being read aloud.
                f"Say in a calm, warm Modern Standard Arabic (فصحى) narrator voice: {text}"}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
            },
        },
        {"x-goog-api-key": key},
    )
    try:
        return base64.b64decode(out["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])
    except (KeyError, IndexError):
        sys.exit(f"Gemini TTS returned no audio:\n{json.dumps(out, ensure_ascii=False)[:800]}")


def write_pcm_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


def tidy(path: Path) -> None:
    """Trim leading/trailing silence and even out loudness, in place."""
    tmp = path.with_suffix(".tmp.wav")
    trim = "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05"
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-af",
                        f"{trim},areverse,{trim},areverse,loudnorm=I=-16:TP=-1.5",
                        "-ar", str(RATE), "-ac", "1", str(tmp)], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"ffmpeg could not clean {path.name}:\n{r.stderr}")
    tmp.replace(path)


def wav_seconds(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def lura_gemini_key() -> str:
    keys = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "lura/keys.json"
    try:
        return json.loads(keys.read_text()).get("gemini", "").strip()
    except (OSError, json.JSONDecodeError):
        return ""


def pick_engine(requested: str) -> tuple[str, str]:
    cloud_key = os.environ.get("GOOGLE_TTS_API_KEY", "").strip()
    gem_key = os.environ.get("GEMINI_API_KEY", "").strip() or lura_gemini_key()
    if requested == "cloud" or (requested == "auto" and cloud_key):
        return ("cloud", cloud_key) if cloud_key else sys.exit("GOOGLE_TTS_API_KEY is not set")
    if requested == "gemini" or (requested == "auto" and gem_key):
        return ("gemini", gem_key) if gem_key else sys.exit("No Gemini key (GEMINI_API_KEY or `lura login`)")
    return ("none", "")


def build_timeline(durations: list[float], voiced: bool) -> dict:
    scenes, t = [], LEAD_IN - PRE_VOICE
    for i, (text, d) in enumerate(zip(LINES, durations)):
        hold = OUTRO if i == len(LINES) - 1 else TAIL
        scenes.append({"start": round(t, 3), "voiceStart": round(t + PRE_VOICE, 3),
                       "voiceDur": round(d, 3), "dur": round(PRE_VOICE + d + hold, 3),
                       "text": text, "wav": f"line{i + 1}.wav" if voiced else None})
        t += PRE_VOICE + d + hold
    return {"fps": 30, "width": 1920, "height": 1080, "total": round(t, 3), "scenes": scenes}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", choices=["auto", "cloud", "gemini", "none"], default="auto")
    ap.add_argument("--cloud-voice", default="ar-XA-Chirp3-HD-Aoede",
                    help="any ar-XA voice, e.g. ar-XA-Wavenet-A")
    ap.add_argument("--gemini-voice", default="Kore")
    ap.add_argument("--gemini-model", default="gemini-2.5-flash-preview-tts")
    ap.add_argument("--only", type=int, choices=range(1, 6), metavar="N",
                    help="regenerate just line N, keep the other WAVs")
    args = ap.parse_args()

    BUILD.mkdir(exist_ok=True)
    engine, key = ("none", "") if args.engine == "none" else pick_engine(args.engine)
    durations = list(FALLBACK_SECONDS)
    if engine != "none":
        for i, text in enumerate(LINES):
            path = BUILD / f"line{i + 1}.wav"
            if args.only and args.only != i + 1 and path.exists():
                durations[i] = wav_seconds(path)
                continue
            print(f"[{engine}] {i + 1}/5  {text}")
            if engine == "cloud":
                path.write_bytes(cloud_tts(text, key, args.cloud_voice))
            else:
                write_pcm_wav(path, gemini_tts(text, key, args.gemini_voice, args.gemini_model))
            tidy(path)
            durations[i] = wav_seconds(path)
            print(f"      {durations[i]:.2f}s  → {path}")
    else:
        print("No TTS key — using placeholder timing, video will have music only.")

    timeline = build_timeline(durations, engine != "none")
    (BUILD / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2))
    print(f"timeline: {timeline['total']:.1f}s → {BUILD / 'timeline.json'}")


if __name__ == "__main__":
    main()
