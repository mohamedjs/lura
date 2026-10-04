# Lura promo video

A ~22 s motion-graphics promo for Lura with Arabic (MSA) narration made by Google TTS.
Everything is generated from code here. There's no editor project and no stock footage.

```bash
cd video
./make.sh                                    # → out/lura.mp4
./make.sh ~/Downloads/"studio lura 2.mp4"    # put your screen recording in scene 4

# voice from your own Gradio TTS app (Colab share link), Modern Standard Arabic:
TTS_GRADIO_URL=https://xxxx.gradio.live ./make.sh ~/Downloads/"studio lura 2.mp4"
```

## 9:16 ad (Reels / Shorts / TikTok)

`ad.html` + `ad.mjs` remake `lura_ad_9x16.mp4` (63 s, 1080×1920) with the new face:

```bash
node ad.mjs --source ~/Downloads/lura_ad_9x16.mp4          # → out/lura_ad_9x16.mp4
node ad.mjs --source ~/Downloads/lura_ad_9x16.mp4 --footage ~/Downloads/"studio lura 2.mp4"
```

It takes the narration track and the real-desktop clip (54.9–58.7 s) from the
source video, and keeps the original's scene timing so they still line up: intro,
wake word, morning briefing (RAM, load, network, last GitHub commit), voice
commands, real desktop, end card. What changed: the 3D face is lip-synced to the
narration, captions reveal word by word, the numbers count up, and a soft music
bed ducks under the voice. `--voice new.wav` swaps the narration, and
`--preview` renders at half resolution.

## Combo (9:16 + 16:9, dark + light)

`combo.html` + `combo.mjs` rebuild `lura_combo_9x16.mp4` (78 s) with the new face,
and add a **human calling «لورا»** (Habibi, Egyptian voice) at 8.1 s. The source's
own narration is ducked under the call, its wake chime at 9.3 s follows, and Lura
answers «أيوه معاك…» at 10.8 s. The three real screen recordings are cut from the
source's cards and reused.

```bash
node combo.mjs --source ~/Downloads/lura_combo_9x16.mp4          # 4 files in out/
node combo.mjs --source … --aspect h --theme light                 # just one
```

Two design systems, defined as CSS tokens at the top of `combo.html`:

| | **Neon HUD** (`dark`) | **Daylight** (`light`) |
|---|---|---|
| Ground | deep navy, cyan floor grid | porcelain `#f5f6fb`, soft indigo/teal/pink studio lights drifting |
| Ink | `#eafcff` on glow | `#0f172a`, no glow |
| Accent | cyan `#22d3ee` + pink | indigo `#4f46e5` + teal, gradient logo |
| Cards | translucent cyan glass | frosted white glass, soft long shadows |
| Face | additive neon wireframe | indigo ink wireframe, teal highlights |

The layouts live in `.v` (1080×1920) and `.h` (1920×1080) CSS blocks. In 16:9 the
face sits on the left and the content on the right. For a live preview, open
`combo.html?aspect=h&theme=light` in a browser.

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

## Voice

`voice.py` picks the first engine it can use:

0. `TTS_GRADIO_URL` (or `--gradio URL`) → **your Gradio TTS app**. It reads the app's
   API, finds the endpoint that takes text and returns audio, selects the MSA /
   فصحى option in any dropdown, and leaves the other inputs at their defaults.
   Voice-cloning apps (F5-TTS, Habibi) need a voice to copy:
   `TTS_RECORD_REF=1` records one from your mic while you read a sentence on screen,
   or `TTS_REF_WAV=clip.wav TTS_REF_TEXT='exact words in the clip'` uses a clip you have.
   Habibi-TTS ships a reference clip with its transcript: `src/habibi_tts/assets/MSA.mp3` in
   github.com/SWivid/Habibi-TTS, text in the app's "Example Prompts". `TTS_TEMPO=0.88`
   slows the voice down without changing its pitch.
   Override any other input with `TTS_ARGS='--gradio-arg "seed_input=42"'`, or choose
   the endpoint with `--gradio-api /name`. If the link is dead, it says so.
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
- `frames.mjs`: shared frame grabber + loudness envelope.
- `ad.html` + `ad.mjs`: the 9:16 ad.
- `combo.html` + `combo.mjs`: the combo in both aspects and both themes.

Needs `ffmpeg`, `node` 18+, `python3`. `make.sh` installs Playwright's Chromium on
first run.
