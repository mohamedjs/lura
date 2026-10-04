// Lura combo: rebuilds lura_combo_9x16.mp4 with the new face, adds a human calling
// «لورا» before Lura answers, and renders it 9:16 and/or 16:9, dark and/or light.
//   node combo.mjs --source lura_combo_9x16.mp4 [--human human_lura.wav] [--aspect v|h|both] [--theme dark|light|both] [--preview]
//   node combo.mjs --source … --timing build/gem/timing.json   # new narration: its own timing + audio recipe
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { envelope, renderPage, run } from './frames.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const B = join(HERE, 'build', 'combo');
const arg = (name, dflt) => { const i = process.argv.indexOf(name); return i > 0 ? process.argv[i + 1] : dflt; };
const SOURCE = arg('--source'), HUMAN = arg('--human', join(B, 'human_lura.wav'));
const ASPECTS = { v: ['v'], h: ['h'], both: ['v', 'h'] }[arg('--aspect', 'both')];
const THEMES = { dark: ['dark'], light: ['light'], both: ['dark', 'light'] }[arg('--theme', 'both')];
const QUICK = process.argv.includes('--preview');
const OUTDIR = resolve(arg('--outdir', join(HERE, 'out')));
if (!SOURCE || !existsSync(SOURCE)) { console.error('need --source lura_combo_9x16.mp4'); process.exit(1); }
mkdirSync(B, { recursive: true }); mkdirSync(OUTDIR, { recursive: true });
const TIMING = arg('--timing') && JSON.parse(readFileSync(resolve(arg('--timing')), 'utf8'));
const TL = { fps: 30, total: TIMING ? TIMING.timing.total : 78.4, clips: {}, timing: TIMING?.timing };
const SUFFIX = TIMING ? `_${arg('--name', 'v2')}` : '';

const narration = join(B, 'narration.wav'), voice = join(B, 'voice.wav');
if (TIMING) {
  // new narration: mute the spans we replace, mouth follows what's left (Lura only),
  // then add the people's lines, a wake chime and a pad that ducks under the voice
  const A = TIMING.audio, T = TL.total;
  const mute = A.mute.map(([a, b]) => `between(t,${a},${b})`).join('+');
  run('ffmpeg', ['-v', 'error', '-y', '-i', resolve(A.voice), '-af',
    `aresample=48000,volume='if(${mute},0,1)':eval=frame,apad=whole_dur=${T}`, '-ac', '2', narration]);
  TL.env = envelope(narration, TL.fps);
  const ins = [];
  A.insert.forEach(([f, at]) => ins.push('-i', resolve(f)));
  const n = A.insert.length;
  const chime = `aevalsrc='0.20*exp(-5*t)*sin(2*PI*880*t)+0.15*exp(-4*(t-0.12))*gte(t,0.12)*sin(2*PI*1318.5*t)':s=48000:c=stereo:d=1.6`;
  const pad = `aevalsrc='0.045*(sin(2*PI*110*t)+0.7*sin(2*PI*164.81*t)+0.5*sin(2*PI*220*t)+0.35*sin(2*PI*277.18*t))*(0.75+0.25*sin(2*PI*0.12*t))':s=48000:c=stereo:d=${T}`;
  const g = [`[0]asplit[v][key]`];
  A.insert.forEach(([, at], i) => g.push(`[${i + 1}]aresample=48000,aformat=channel_layouts=stereo,adelay=${Math.round(at * 1000)}:all=1,volume=1.1[h${i}]`));
  g.push(`[${n + 1}]adelay=${Math.round(A.chime * 1000)}:all=1[c]`);
  g.push(`[${n + 2}]lowpass=f=1300,afade=t=in:d=2,afade=t=out:st=${T - 2.5}:d=2.5[p]`, `[p][key]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[pd]`);
  g.push(`[v]${A.insert.map((_, i) => `[h${i}]`).join('')}[c][pd]amix=inputs=${n + 3}:normalize=0:duration=first,alimiter=limit=0.95`);
  run('ffmpeg', ['-v', 'error', '-y', '-i', narration, ...ins, '-f', 'lavfi', '-i', chime, '-f', 'lavfi', '-i', pad,
    '-filter_complex', g.join(';'), '-t', String(T), voice]);
} else {
  // narration from the source; the mouth follows it
  run('ffmpeg', ['-v', 'error', '-y', '-i', resolve(SOURCE), '-vn', '-ac', '2', '-ar', '48000', narration]);
  TL.env = envelope(narration, TL.fps);
  // + the human wake call at 8.1 s, source ducked under it
  if (existsSync(HUMAN)) {
    run('ffmpeg', ['-v', 'error', '-y', '-i', narration, '-i', HUMAN, '-filter_complex',
      "[0]volume='if(between(t,7.95,8.75),0.45,1)':eval=frame[s];[1]aresample=48000,adelay=8100:all=1,volume=1.15[h];" +
      '[s][h]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.95', voice]);
  } else { console.log(`no ${HUMAN} — narration only`); run('ffmpeg', ['-v', 'error', '-y', '-i', narration, voice]); }
}

// real screen recordings, cropped out of the source's cards (inside the border, under its label)
// [source start, length, crop top, page time it appears]; with new timing, C starts later in the
// source so Chrome is already opening when Lura says «فتحتلك كروم»
const at = TIMING?.timing.at;
const CLIPS = { A: [7.0, 6.0, 712, at?.recA ?? 7.0], B: [13.8, 11.6, 232, at?.recB ?? 13.8],
                C: [at ? 66.0 : 63.0, at ? 3.5 : 6.6, 632, at?.recC ?? 63.0] };
for (const [name, [start, len, y, page]] of Object.entries(CLIPS)) {
  const dir = join(B, `clip${name}`);
  rmSync(dir, { recursive: true, force: true }); mkdirSync(dir);
  run('ffmpeg', ['-v', 'error', '-ss', String(start), '-i', resolve(SOURCE), '-t', String(len),
    '-vf', `crop=948:458:66:${y},fps=${TL.fps},scale=980:474`, '-q:v', '3', join(dir, 'f%04d.jpg')]);
  TL.clips[name] = { dir: `build/combo/clip${name}`, start: page, count: readdirSync(dir).length };
}

for (const aspect of ASPECTS) for (const theme of THEMES) {
  const [w, h] = aspect === 'v' ? [1080, 1920] : [1920, 1080];
  const tag = `${aspect === 'v' ? '9x16' : '16x9'}_${theme}`;
  const picture = join(B, `picture_${tag}.mp4`), out = join(OUTDIR, `lura_combo${SUFFIX}_${tag}.mp4`);
  console.log(`— ${tag}`);
  await renderPage({ html: join(HERE, 'combo.html'), TL: { ...TL, aspect, theme }, width: w, height: h, out: picture, quick: QUICK });
  run('ffmpeg', ['-v', 'error', '-y', '-i', picture, '-i', voice, '-map', '0:v', '-map', '1:a', '-c:v', 'copy',
    '-c:a', 'aac', '-b:a', '192k', '-t', String(TL.total), '-movflags', '+faststart', out]);
  console.log(`✓ ${out}`);
}
