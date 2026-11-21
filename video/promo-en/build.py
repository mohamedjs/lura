#!/usr/bin/env python3
"""English cut of the Lura landing video (the Arabic one is promo-holo/lura-promo.mp4).

Same real screens as the Arabic cut (the folder, Gemini, Notion and GitHub takes, cropped
from its window panel), restyled in the Notebook design system, with one English narrator.

  python3 build.py                 # timing from vo/*.wav (or estimated), clips, audio, hf/
  python3 build.py --snapshot 3,20 # + HyperFrames stills in hf/snapshots/
  python3 build.py --render out.mp4

Inputs: lines.json (narration), vo/<id>.wav (one take per line, any TTS; missing takes are
timed from their length and the mix is silent there), the Arabic cut (--source).
No music: voice plus a soft whoosh between sections and a tick when a task is done.
"""
import argparse, json, re, shutil, subprocess, sys
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
HF = 'hyperframes@0.9.56'
SR, FPS = 48000, 30

ap = argparse.ArgumentParser()
ap.add_argument('--source', default='/var/www/html/motion-graphic/product-videos/lura/promo-holo/lura-promo.mp4')
ap.add_argument('--vo', default=str(HERE / 'vo'))
ap.add_argument('--out', default=str(HERE / 'hf'))
ap.add_argument('--snapshot')
ap.add_argument('--render')
ap.add_argument('--skip-clips', action='store_true')
a = ap.parse_args()

# Crops of the window panel in the 1920x1080 Arabic cut: (x, y, w, h)
STRIP = (792, 336, 1070, 368)      # the file manager (a wide strip)
WIN = (790, 196, 1070, 662)        # Chrome: Gemini, Notion, GitHub
# id, handwritten label, tool chip, minimum length, footage (from, to, crop, mode)
#   mode 'fit': the span is stretched to the whole section; 'once': plays 1x, then the board
SECTIONS = [
    ('intro', 'Meet Lura', None, 19, None),
    ('folder', '1/4 · Sort a folder', 'folder', 14, (26.0, 38.3, STRIP, 'fit')),
    ('gemini', '2/4 · Ask Gemini', 'gemini', 19, (39.6, 57.9, WIN, 'fit')),
    ('notion', '3/4 · Last thing in Notion', 'notion', 17, (59.2, 61.7, WIN, 'once')),
    ('github', '4/4 · My GitHub news', 'github', 30, (77.0, 79.7, WIN, 'once')),
    ('outro', '', None, 9, None),
]
# Cards that pop when the narrator reaches a word: (section, line, word, icon, title, sub)
# Remix Icon code points (site/fonts/remixicon.woff2 is the full font).
I = {'window': 'f2c6', 'folder': 'ed70', 'settings': 'f0e6', 'cpu': 'ebf0', 'github': 'edca', 'brain': 'f2f7',
     'notion': 'f348', 'done': 'eb80', 'time': 'f20f', 'open': 'ecaf', 'movie': 'ef7f', 'server': 'f0e0',
     'chat': 'eb51', 'stack': 'f181', 'merge': 'edc1', 'all': 'eb79', 'spark': 'f36a', 'folder3': 'ed53',
     'check': 'eb7b', 'windows': 'f2c7', 'apple': 'ea3f', 'ubuntu': 'f240', 'mic': 'ef50', 'translate': 'f226',
     'oss': 'efb3', 'chrome': 'eb8d'}
CARDS = [
    ('intro', 'i2', 'apps', 'window', 'Opens apps & websites', ''),
    ('intro', 'i2', 'files', 'folder', 'Finds your files', ''),
    ('intro', 'i2', 'settings', 'settings', 'Changes settings', ''),
    ('intro', 'i2', "system's", 'cpu', 'System health', 'temperature & memory'),
    ('intro', 'i2', 'GitHub', 'github', 'Works with GitHub', ''),
    ('intro', 'i2', 'remembers', 'brain', 'Remembers you', 'your preferences'),
    ('notion', 'n1', 'Lura', 'notion', 'Lura Intro Video 2.4 · Live Session', 'work page'),
    ('notion', 'n2', 'done', 'done', 'Done', 'status'),
    ('notion', 'n2', '5:24', 'time', '5:24 AM', 'last edited'),
    ('notion', 'n2', 'open', 'open', 'Want me to open it?', ''),
    ('github', 'h2', 'Helm', 'github', '7alm', 'big work · Nov 13'),
    ('github', 'h2', '75-second', 'movie', '75-second product video', 'on the landing page'),
    ('github', 'h3', 'NestJS', 'server', 'V2: NestJS backend', 'instead of Supabase · Nov 12'),
    ('github', 'h3', 'Meta', 'chat', 'Meta Inbox', 'Facebook · Instagram · WhatsApp'),
    ('github', 'h3', 'Docker', 'stack', 'Docker production stack', ''),
    ('github', 'h4', '2.3', 'merge', 'Lura · PR 2.3', 'streaming speech + interrupt her'),
]
PRE, GAP, POST, FIRST = 0.7, 0.35, 0.9, 0.5


def sh(*cmd):
    subprocess.run(cmd, check=True)


def wav_len(p):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(p)],
                         capture_output=True, text=True).stdout.strip()
    return float(out)


def load(p):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(p), '-ac', '1', '-ar', str(SR), '-f', 's16le', '-'],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32)


lines = json.load(open(HERE / 'lines.json'))
vo = Path(a.vo)
plain = lambda s: s.replace('*', '')
for ln in lines:
    f = vo / f"{ln['id']}.wav"
    ln['wav'] = f if f.exists() else None
    ln['dur'] = wav_len(f) if f.exists() else round(len(plain(ln['text'])) / 14.5, 2)
missing = [ln['id'] for ln in lines if not ln['wav']]

# --- timeline: sections follow the voice, never shorter than the Arabic cut's sections ---
secs, t = [], 0.0
for sid, label, tool, mn, foot in SECTIONS:
    mine = [ln for ln in lines if ln['sec'] == sid]
    at = t + (FIRST if not secs else PRE)
    for ln in mine:
        ln['at'] = round(at, 3)
        at += ln['dur'] + GAP
    end = max(t + mn, at - GAP + POST + (0.8 if sid == 'outro' else 0))
    secs.append({'id': sid, 'label': label, 'tool': tool, 'start': round(t, 3), 'end': round(end, 3), 'foot': foot})
    t = end
total = round(t, 3)


def word_at(ln, word):
    """When the narrator reaches `word`, by its position in the line (refined later by real takes)."""
    txt = plain(ln['text'])
    k = txt.find(word)
    if k < 0:
        sys.exit(f'{word!r} not in {ln["id"]}')
    return round(ln['at'] + ln['dur'] * k / max(1, len(txt)), 3)


byid = {ln['id']: ln for ln in lines}
cards = [{'sec': s, 'at': word_at(byid[l], w), 'icon': I[ic], 'title': ti, 'sub': su} for s, l, w, ic, ti, su in CARDS]

caps = []
for ln in lines:
    txt = ln.get('cap', ln['text'])
    words, pos, n = [], 0, len(plain(txt))
    inside = False
    for tok in txt.split():
        hl = inside or '*' in tok
        inside ^= tok.count('*') % 2 == 1
        w = tok.replace('*', '')
        core = w.rstrip('.,:;?!')
        words.append({'w': core, 'p': w[len(core):], 'hl': hl, 'at': round(ln['at'] + ln['dur'] * pos / max(1, n), 3)})
        pos += len(w) + 1
    caps.append({'id': ln['id'], 'at': ln['at'], 'end': round(ln['at'] + ln['dur'], 3), 'words': words})

out = Path(a.out)
(out / 'clips').mkdir(parents=True, exist_ok=True)
(out / 'audio').mkdir(parents=True, exist_ok=True)

# --- footage: crop the window panel out of the Arabic cut, timed to this cut ---
for s in secs:
    if not s['foot']:
        continue
    f0, f1, (x, y, w, h), mode = s['foot']
    span = s['end'] - s['start']
    s['clip'] = f"clips/{s['id']}.mp4"
    s['clipdur'] = round(span if mode == 'fit' else f1 - f0, 3)
    s['board'] = mode == 'once'
    s['aspect'] = round(w / h, 4)
    if a.skip_clips and (out / s['clip']).exists():
        continue
    k = s['clipdur'] / (f1 - f0)
    sh('ffmpeg', '-v', 'error', '-y', '-ss', f'{f0:.3f}', '-t', f'{f1 - f0:.3f}', '-i', a.source, '-an',
       '-vf', f'crop={w}:{h}:{x}:{y},setpts=PTS*{k:.5f},fps={FPS},scale=1600:-2:flags=lanczos',
       '-c:v', 'libx264', '-preset', 'medium', '-crf', '17', '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
       str(out / s['clip']))

# --- audio: the voice lines, a soft whoosh between sections, a tick when a task is done. No music. ---
n = int(total * SR) + SR
mix = np.zeros(n, np.float32)
rng = np.random.default_rng(7)


def whoosh(d=0.55):
    m = int(d * SR)
    noise = rng.standard_normal(m).astype(np.float32)
    # a band that sweeps up: two one-pole filters with a moving coefficient
    out_, lp, lp2 = np.zeros(m, np.float32), 0.0, 0.0
    for i in range(m):
        c = 0.04 + 0.30 * (i / m)
        lp += c * (noise[i] - lp)
        lp2 += 0.02 * (lp - lp2)
        out_[i] = lp - lp2
    env = np.sin(np.pi * np.arange(m) / m) ** 2
    return out_ * env / (np.abs(out_).max() or 1) * 2600


def tick():
    tt = np.arange(int(.45 * SR)) / SR
    return (np.sin(2 * np.pi * 1318 * tt) * .5 + np.sin(2 * np.pi * 1976 * tt) * .35 * (tt > .07)) * np.exp(-tt * 9) * 2200


def put(sig, at):
    i = int(at * SR)
    j = min(n, i + len(sig))
    mix[i:j] += sig[:j - i]


for ln in lines:
    if ln['wav']:
        v = load(ln['wav'])
        put(v * (0.92 * 32767 / (np.abs(v).max() or 1)) * 0.75, ln['at'])
W = whoosh()
for s in secs[1:]:
    put(W, max(0, s['start'] - 0.25))
for s in secs:
    if s['tool']:
        put(tick(), s['end'] - 0.55)
mix = mix[:int(total * SR)]
peak = np.abs(mix).max() or 1
mix = mix * min(1.0, 0.95 * 32767 / peak)
raw = out / 'audio' / 'mix_raw.wav'
import wave
with wave.open(str(raw), 'wb') as wv:
    wv.setnchannels(1); wv.setsampwidth(2); wv.setframerate(SR); wv.writeframes(mix.astype(np.int16).tobytes())
af = 'loudnorm=I=-15:TP=-1.5' if not missing or len(missing) < len(lines) else 'anull'
sh('ffmpeg', '-v', 'error', '-y', '-i', str(raw), '-af', af, '-ar', str(SR), '-ac', '2', str(out / 'audio' / 'mix.wav'))

# --- the HyperFrames composition ---
for d in ('fonts', 'img'):
    (out / d).mkdir(exist_ok=True)
for f in ('caveat-latin.woff2', 'inter-latin-400-normal.woff2', 'inter-latin-800-normal.woff2', 'remixicon.woff2'):
    shutil.copy2(ROOT / 'site' / 'fonts' / f, out / 'fonts' / f)
shutil.copy2(ROOT / 'site' / 'img' / 'holo-hero.webp', out / 'img' / 'holo-hero.webp')

clips = '\n  '.join(
    f'<video id="v_{s["id"]}" class="clip footage" src="{s["clip"]}" data-start="{s["start"]}" '
    f'data-duration="{s["clipdur"]}" data-track-index="{i + 2}" muted playsinline></video>'
    for i, s in enumerate(x for x in secs if x.get('clip')))
data = {'total': total, 'secs': secs, 'cards': cards, 'caps': caps,
        'talk': [[ln['at'], round(ln['at'] + ln['dur'], 3)] for ln in lines]}
html = (HERE / 'template.html').read_text()
html = html.replace('{{TOTAL}}', str(total)).replace('{{CLIPS}}', clips).replace('{{DATA}}', json.dumps(data, ensure_ascii=False))
(out / 'index.html').write_text(html)
json.dump({'total': total, 'lines': [{k: ln[k] for k in ('id', 'sec', 'at', 'dur')} for ln in lines],
           'secs': [{k: s[k] for k in ('id', 'start', 'end')} for s in secs]}, open(HERE / 'timing.json', 'w'), indent=1)

for s in secs:
    print(f"{s['id']:<7} {s['start']:6.2f}–{s['end']:6.2f}  ({s['end'] - s['start']:5.2f}s)")
print(f'total {total:.2f}s' + (f'   (estimated, no take yet for {", ".join(missing)})' if missing else ''))

if a.snapshot:
    subprocess.run(['npx', '-y', HF, 'snapshot', '--at', a.snapshot, '-o', 'snapshots'], cwd=out, check=True)
if a.render:
    if missing:
        sys.exit(f'refusing to render the final cut without takes for: {", ".join(missing)}')
    subprocess.run(['npx', '-y', HF, 'render', '--quality', 'high', '--output', str(Path(a.render).resolve())], cwd=out, check=True)
