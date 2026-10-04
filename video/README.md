# Lura promo video

A ~22 s motion-graphics promo for Lura with Arabic (MSA) narration made by Google TTS.
Everything is generated from code here. There's no editor project and no stock footage.

```bash
cd video
./make.sh                                    # → out/lura.mp4
./make.sh ~/Downloads/"studio lura 2.mp4"    # put your screen recording in scene 4
```

## Scenes

| # | Narration | Picture |
|---|---|---|
| 1 | تخيّل أنّ جهازك يسمعك… ويردّ عليك | Lura's 3D face, sound rings, live waveform |
| 2 | يكفي أن تناديها باسمها | «لورا» pops, wake chime, "سمعتك… تفضّل" |
| 3 | وكلَّ صباح، تطمئنك على جهازك… وعلى عملك | Dawn glow, morning-briefing cards (CPU, RAM, disk, GitHub) |
| 4 | وتفتح لك برامجك بصوتك | Voice command chip, windows opening from the dock (or your clip) |
| 5 | لورا… مساعدتك الصوتية على لينكس | Logo reveal, tagline, repo link |

Scene lengths follow the narration: `voice.py` measures each WAV and writes
`build/timeline.json`, and the picture is timed from that file.

## Voice (Google)

`voice.py` picks the first key it finds:

1. `GOOGLE_TTS_API_KEY` → **Cloud Text-to-Speech**, voice `ar-XA-Chirp3-HD-Aoede`
   (`--cloud-voice ar-XA-Wavenet-A` etc.)
2. `GEMINI_API_KEY`, or the Gemini key already saved by `lura login` →
   **Gemini TTS**, voice `Kore`, model `gemini-2.5-flash-preview-tts` (preview models
   churn; pass `--gemini-model` if it's retired)
3. No key → placeholder timing, music only

API errors are printed as-is. Each line's audio is trimmed of leading and trailing
silence and normalized in loudness. To redo one line that sounds wrong, run
`python3 voice.py --only 3` and then `node render.mjs`.

## The face

`face.js` draws the head from `face_mesh.js`, MediaPipe's canonical face mesh
(468 points, Apache-2.0). The mesh is shaded with real lighting, and the face
turns slowly, blinks, and drops its jaw following the loudness of the narration.
The renderer measures that loudness from each WAV, frame by frame, so the mouth
moves in sync with the actual audio.

## Pieces

- `scenes.html`: the animation. Each frame depends only on time `t`, so you can
  open it in a browser for a live preview.
- `render.mjs`: steps headless Chromium frame by frame (30 fps, 1080p), then
  mixes the narration, a synthesized ambient pad and the wake chime with ffmpeg.
  `--preview` renders at half resolution for a fast check.
- `voice.py`: narration and timeline.
- `face.js` + `face_mesh.js`: the holographic head.

Needs `ffmpeg`, `node` 18+, `python3`. `make.sh` installs Playwright's Chromium on
first run.
