"""Narration for the Lura promo: five MSA lines → WAVs + timeline.json.

Engines:
  gradio  any Gradio TTS app (e.g. a Colab share link), URL in --gradio or TTS_GRADIO_URL
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


MSA_WORDS = ("msa", "فصحى", "fusha", "fus7a", "standard", "modern standard")


def _gradio_value(raw: str):
    """--gradio-arg value: @path → uploaded file, numbers → numbers, else text."""
    if raw.startswith("@"):
        from gradio_client import handle_file
        return handle_file(os.path.expanduser(raw[1:]))
    try:
        return float(raw) if "." in raw else int(raw)
    except ValueError:
        return raw


class GradioTTS:
    """Talks to a Gradio TTS app by reading its API: finds the endpoint that takes
    text and returns audio, picks the MSA option in any dropdown, and leaves the
    other inputs at the app's own defaults."""

    def __init__(self, url: str, api_name: str | None, overrides: list[str],
                 ref_wav: str | None = None, ref_text: str | None = None):
        try:
            from gradio_client import Client
        except ImportError:
            sys.exit("Gradio engine needs gradio_client:  python3 -m pip install gradio_client")
        try:
            self.client = Client(url, verbose=False)
            api = self.client.view_api(return_format="dict", print_info=False)["named_endpoints"]
        except Exception as err:  # noqa: BLE001 — show the real reason
            sys.exit(f"Could not open the Gradio app at {url}\n{type(err).__name__}: {err}\n"
                     "(gradio.live links die after ~72h or when Colab stops — re-run the notebook.)")
        self.api_name = api_name or self._pick(api)
        if self.api_name not in api:
            sys.exit(f"No endpoint {self.api_name!r}. The app has:\n{self._describe(api)}")
        self.params = api[self.api_name]["parameters"]
        self.fixed = {}
        for item in overrides:
            name, _, raw = item.partition("=")
            self.fixed[name.strip()] = _gradio_value(raw.strip())
        # reference voice for cloning apps: map onto their ref audio / ref text inputs
        def ref_param(component):
            return next((p["parameter_name"] for p in self.params
                         if p["component"] == component and "ref" in p["parameter_name"].lower()), None)
        if ref_wav:
            if not Path(ref_wav).expanduser().is_file():
                sys.exit(f"Reference voice not found: {ref_wav}")
            name = ref_param("Audio") or sys.exit(f"{self.api_name} has no reference-audio input")
            self.fixed.setdefault(name, _gradio_value("@" + ref_wav))
        if ref_text:
            name = ref_param("Textbox") or sys.exit(f"{self.api_name} has no reference-text input")
            self.fixed.setdefault(name, ref_text)
        known = {p["parameter_name"] for p in self.params}
        if unknown := [k for k in self.fixed if k not in known]:
            sys.exit(f"--gradio-arg {unknown} not inputs of {self.api_name}. It takes: {', '.join(sorted(known))}")
        self.text_param = self._text_box(self.fixed)
        if not self.text_param:
            sys.exit(f"Endpoint {self.api_name} has no text box. The app has:\n{self._describe(api)}")
        for p in self.params:
            name, choices = p["parameter_name"], p["type"].get("enum") or []
            if name in self.fixed or name == self.text_param:
                continue
            msa = next((c for c in choices if any(w in str(c).lower() for w in MSA_WORDS)), None)
            if msa is not None:
                self.fixed[name] = msa
            elif not p["parameter_has_default"]:
                self.fixed[name] = None          # optional uploads like a reference voice
        shown = {k: f"@{v.get('orig_name')}" if isinstance(v, dict) else v for k, v in self.fixed.items()}
        print(f"[gradio] {url} {self.api_name}  text→{self.text_param}  {shown}")
        print(f"[gradio] inputs: {', '.join(p['parameter_name'] for p in self.params)}")

    def _text_box(self, fixed: dict) -> str | None:
        """The box for the words to speak. Voice-cloning apps (F5-TTS, Habibi…)
        also have a *reference* transcript box — never put our line there."""
        def score(p):
            key = f"{p['parameter_name']} {p.get('label') or ''}".lower()
            return (2 if any(w in key for w in ("gen", "target", "synth", "to speak", "input_text")) else 0) \
                - (3 if "ref" in key else 0)
        boxes = [p for p in self.params if p["component"] == "Textbox" and p["parameter_name"] not in fixed]
        return max(boxes, key=score)["parameter_name"] if boxes else None

    @staticmethod
    def _audio_path(x):
        """Audio outputs come back as a path, a FileData-like dict, or a gr.update."""
        if isinstance(x, dict):
            x = x.get("value", x.get("path"))
            if isinstance(x, dict):
                x = x.get("path")
        return x if isinstance(x, str) and Path(x).is_file() else None

    @staticmethod
    def _pick(api: dict) -> str:
        for name, e in api.items():
            takes_text = any(p["component"] == "Textbox" for p in e["parameters"])
            gives_audio = any(r["component"] == "Audio" for r in e["returns"])
            if takes_text and gives_audio:
                return name
        sys.exit(f"No endpoint takes text and returns audio. The app has:\n{GradioTTS._describe(api)}")

    @staticmethod
    def _describe(api: dict) -> str:
        lines = []
        for name, e in api.items():
            args = ", ".join(f"{p['parameter_name']}:{p['component']}" + (f"{p['type'].get('enum')}" if p["type"].get("enum") else "")
                             for p in e["parameters"])
            lines.append(f"  {name}({args}) → {[r['component'] for r in e['returns']]}")
        return "\n".join(lines)

    def synth(self, text: str, out: Path) -> None:
        try:
            result = self.client.predict(**self.fixed, **{self.text_param: text}, api_name=self.api_name)
        except Exception as err:  # noqa: BLE001
            sys.exit(f"Gradio {self.api_name} failed for: {text}\n{type(err).__name__}: {err}")
        items = result if isinstance(result, (list, tuple)) else [result]
        audio = next((a for a in map(self._audio_path, items) if a), None)
        if audio is None:
            refs = [p["parameter_name"] for p in self.params
                    if "ref" in p["parameter_name"].lower() and self.fixed.get(p["parameter_name"]) is None]
            hint = ("\nThe app needs a reference voice to copy. Either record one now:\n"
                    "  TTS_RECORD_REF=1 TTS_GRADIO_URL=… ./make.sh\n"
                    "or give a 5–10 s MSA clip and the exact words spoken in it:\n"
                    "  TTS_REF_WAV=~/voice.wav TTS_REF_TEXT='الكلمات المنطوقة' TTS_GRADIO_URL=… ./make.sh") if refs else ""
            sys.exit(f"Gradio returned no audio: {result!r}{hint}")
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", audio, "-ac", "1", "-ar", str(RATE), str(out)],
                           capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"ffmpeg could not read the Gradio audio {audio}:\n{r.stderr}")


REF_SENTENCE = "مرحباً، أنا لورا، مساعدتك الصوتية على لينكس. يسعدني أن أساعدك في عملك اليوم."


def record_ref(seconds: int = 9) -> tuple[str, str]:
    """Record a reference voice from the default mic while the user reads REF_SENTENCE."""
    out = BUILD / "ref.wav"
    print("\nReference voice: read this sentence aloud, calmly, in فصحى:\n")
    print(f"    {REF_SENTENCE}\n")
    input("Press Enter, then start reading… ")
    print(f"● recording {seconds}s")
    cmds = [["ffmpeg", "-v", "error", "-y", "-f", "pulse", "-i", "default", "-t", str(seconds),
             "-ac", "1", "-ar", str(RATE), str(out)],
            ["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-d", str(seconds), str(out)]]
    for cmd in cmds:
        try:
            if subprocess.run(cmd).returncode == 0 and out.stat().st_size > 10000:
                break
        except FileNotFoundError:
            continue
    else:
        sys.exit("Could not record from the microphone (tried ffmpeg/pulse and arecord).")
    tidy(out)
    print(f"✓ saved {out} — reused next time via TTS_REF_WAV={out}")
    (BUILD / "ref.txt").write_text(REF_SENTENCE)
    return str(out), REF_SENTENCE


def write_pcm_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


def tidy(path: Path, tempo: float = 1.0) -> None:
    """Trim leading/trailing silence, even out loudness, optionally slow down (pitch kept)."""
    tmp = path.with_suffix(".tmp.wav")
    trim = "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05"
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-af",
                        f"{trim},areverse,{trim},areverse,atempo={tempo},loudnorm=I=-16:TP=-1.5",
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


def pick_engine(requested: str, gradio_url: str) -> tuple[str, str]:
    if requested == "gradio" or (requested == "auto" and gradio_url):
        return ("gradio", gradio_url) if gradio_url else sys.exit("No Gradio URL (--gradio or TTS_GRADIO_URL)")
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
    ap.add_argument("--engine", choices=["auto", "gradio", "cloud", "gemini", "none"], default="auto")
    ap.add_argument("--gradio", default=os.environ.get("TTS_GRADIO_URL", ""),
                    help="Gradio TTS app URL, e.g. https://xxxx.gradio.live")
    ap.add_argument("--gradio-api", help="endpoint name, e.g. /synthesize (default: auto)")
    ap.add_argument("--gradio-arg", action="append", default=[], metavar="NAME=VALUE",
                    help="fix an input, e.g. dialect=MSA, speed=0.9, ref_audio=@voice.wav")
    ap.add_argument("--cloud-voice", default="ar-XA-Chirp3-HD-Aoede",
                    help="any ar-XA voice, e.g. ar-XA-Wavenet-A")
    ap.add_argument("--gemini-voice", default="Kore")
    ap.add_argument("--gemini-model", default="gemini-2.5-flash-preview-tts")
    ap.add_argument("--ref-wav", default=os.environ.get("TTS_REF_WAV") or None,
                    help="reference voice clip for cloning TTS apps (5–10 s)")
    ap.add_argument("--ref-text", default=os.environ.get("TTS_REF_TEXT") or None,
                    help="exact words spoken in --ref-wav")
    ap.add_argument("--record-ref", action="store_true", help="record a reference voice from the mic")
    ap.add_argument("--tempo", type=float, default=float(os.environ.get("TTS_TEMPO", 1.0)),
                    help="speech speed, e.g. 0.88 = calmer (pitch unchanged)")
    ap.add_argument("--verify", action="store_true",
                    help="transcribe each new line (OpenRouter, Arabic) and flag mismatches")
    ap.add_argument("--only", type=int, choices=range(1, 6), metavar="N",
                    help="regenerate just line N, keep the other WAVs")
    args = ap.parse_args()

    BUILD.mkdir(exist_ok=True)
    engine, key = ("none", "") if args.engine == "none" else pick_engine(args.engine, args.gradio)
    BUILD.mkdir(exist_ok=True)
    if engine == "gradio" and args.record_ref:
        args.ref_wav, args.ref_text = record_ref()
    elif args.ref_wav and not args.ref_text and Path(args.ref_wav).with_suffix(".txt").is_file():
        args.ref_text = Path(args.ref_wav).with_suffix(".txt").read_text().strip()
    gradio = GradioTTS(key, args.gradio_api, args.gradio_arg, args.ref_wav, args.ref_text) \
        if engine == "gradio" else None
    durations = list(FALLBACK_SECONDS)
    if engine != "none":
        for i, text in enumerate(LINES):
            path = BUILD / f"line{i + 1}.wav"
            if args.only and args.only != i + 1 and path.exists():
                durations[i] = wav_seconds(path)
                continue
            print(f"[{engine}] {i + 1}/5  {text}")
            if gradio:
                gradio.synth(text, path)
            elif engine == "cloud":
                path.write_bytes(cloud_tts(text, key, args.cloud_voice))
            else:
                write_pcm_wav(path, gemini_tts(text, key, args.gemini_voice, args.gemini_model))
            tidy(path, args.tempo)
            durations[i] = wav_seconds(path)
            print(f"      {durations[i]:.2f}s  → {path}")
            if args.verify:
                from transcribe import openrouter_key, similarity, to_wav, transcribe
                key_or = openrouter_key() or sys.exit("--verify needs OPENROUTER_TOKEN")
                heard = transcribe(to_wav(str(path)), key_or)
                score = similarity(text, heard)
                print(f"      {'✓' if score >= 0.75 else '✗ CHECK'} heard {score:.0%}: {heard}")
    else:
        print("No TTS key — using placeholder timing, video will have music only.")

    timeline = build_timeline(durations, engine != "none")
    (BUILD / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2))
    print(f"timeline: {timeline['total']:.1f}s → {BUILD / 'timeline.json'}")


if __name__ == "__main__":
    main()
