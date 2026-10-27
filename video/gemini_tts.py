"""Generate a voice-over with Gemini TTS (gemini-3.8-flash-tts, voice Mako).

Usage (from the Lura folder, with Lura's venv, which already has google-genai):
    .venv/bin/python video/gemini_tts.py video/lura_v3_script.txt ~/Downloads/lura_v3_vo

The key is read from GEMINI_API_KEY or ~/.config/lura/keys.json ("gemini"); it is never printed.
See /var/www/html/old/mindspace/CLAUDE.md → "Voice-over generation (Gemini TTS)".
"""
import json, mimetypes, os, struct, sys
from pathlib import Path
from google import genai
from google.genai import types


def gemini_key() -> str:
    return os.environ.get("GEMINI_API_KEY") or json.loads((Path.home() / ".config/lura/keys.json").read_text())["gemini"]


def parse_audio_mime_type(mime_type):
    bits, rate = 16, 24000
    for p in mime_type.split(";"):
        p = p.strip()
        if p.lower().startswith("rate="):
            try: rate = int(p.split("=", 1)[1])
            except (ValueError, IndexError): pass
        elif p.startswith("audio/L"):
            try: bits = int(p.split("L", 1)[1])
            except (ValueError, IndexError): pass
    return bits, rate


def convert_to_wav(data, mime_type):
    bits, rate = parse_audio_mime_type(mime_type)
    block = bits // 8
    return struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(data), b"WAVE", b"fmt ", 16, 1, 1,
                       rate, rate * block, block, bits, b"data", len(data)) + data


def generate(transcript, out, voice="Mako"):
    client = genai.Client(api_key=gemini_key())
    cfg = types.GenerateContentConfig(temperature=1, response_modalities=["audio"],
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))))
    audio, mime = bytearray(), ""
    for ch in client.models.generate_content_stream(model="gemini-3.8-flash-tts",
            contents=[types.Content(role="user", parts=[types.Part.from_text(text="## Transcript:\n" + transcript)])], config=cfg):
        if ch.parts is None: continue
        if ch.parts[0].inline_data and ch.parts[0].inline_data.data:
            audio.extend(ch.parts[0].inline_data.data); mime = ch.parts[0].inline_data.mime_type
    if not audio:
        sys.exit("No audio returned.")
    ext = mimetypes.guess_extension(mime); data = bytes(audio)
    if ext is None: ext, data = ".wav", convert_to_wav(data, mime)
    Path(out + ext).write_bytes(data); print("saved", out + ext)


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "video/lura_v3_script.txt"
    out = sys.argv[2] if len(sys.argv) > 2 else str(Path.home() / "Downloads/lura_v3_vo")
    generate(Path(src).read_text(encoding="utf-8"), os.path.expanduser(out))
