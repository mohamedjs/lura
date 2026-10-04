// 9:16 Lura ad: re-renders lura_ad_9x16.mp4 with the new face, keeping its narration.
//   node ad.mjs --source lura_ad_9x16.mp4 [--voice new_voice.wav] [--footage clip.mp4] [--out out/lura_ad_9x16.mp4] [--preview]
// --source supplies the narration track and the real-desktop clip (54.9–58.7 s).
import { existsSync, mkdirSync, readdirSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { envelope, renderPage, run } from './frames.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const B = join(HERE, 'build', 'ad');
const arg = (name, dflt) => { const i = process.argv.indexOf(name); return i > 0 ? process.argv[i + 1] : dflt; };
const SOURCE = arg('--source'), VOICE = arg('--voice'), FOOT = arg('--footage');
const OUT = resolve(arg('--out', join(HERE, 'out', 'lura_ad_9x16.mp4')));
const QUICK = process.argv.includes('--preview');
if (!SOURCE && !VOICE) { console.error('need --source lura_ad_9x16.mp4 (or --voice + --footage)'); process.exit(1); }
for (const f of [SOURCE, VOICE, FOOT]) if (f && !existsSync(f)) { console.error(`not found: ${f}`); process.exit(1); }
mkdirSync(B, { recursive: true }); mkdirSync(dirname(OUT), { recursive: true });

const TL = { fps: 30, total: 62.9 };
const voice = join(B, 'voice.wav');
run('ffmpeg', ['-v', 'error', '-y', '-i', resolve(VOICE || SOURCE), '-vn', '-ac', '2', '-ar', '48000', voice]);
TL.env = envelope(voice, TL.fps);

// real desktop clip → JPEG frames for the window (crop the original's card, or use --footage)
const dir = join(B, 'foot');
rmSync(dir, { recursive: true, force: true }); mkdirSync(dir);
const fit = 'scale=920:840:force_original_aspect_ratio=increase,crop=920:840';
run('ffmpeg', ['-v', 'error', ...(FOOT ? ['-i', resolve(FOOT)] : ['-ss', '55.25', '-i', resolve(SOURCE)]), '-t', '3.6',
  '-vf', `${FOOT ? '' : 'crop=884:800:98:570,'}fps=${TL.fps},${fit}`, '-q:v', '3', join(dir, 'f%04d.jpg')]);
TL.footage = { dir: 'build/ad/foot', fps: TL.fps, count: readdirSync(dir).length };

const picture = join(B, 'picture.mp4');
await renderPage({ html: join(HERE, 'ad.html'), TL, width: 1080, height: 1920, out: picture, quick: QUICK });

// narration on top, a quiet pad underneath that ducks while Lura talks
const T = TL.total;
const pad = `aevalsrc='0.05*(sin(2*PI*110*t)+0.7*sin(2*PI*164.81*t)+0.5*sin(2*PI*220*t)+0.35*sin(2*PI*277.18*t))*(0.75+0.25*sin(2*PI*0.12*t))':s=48000:c=stereo:d=${T}`;
run('ffmpeg', ['-v', 'error', '-y', '-i', picture, '-i', voice, '-f', 'lavfi', '-i', pad, '-filter_complex',
  `[1]asplit[v][key];[2]lowpass=f=1300,afade=t=in:d=2,afade=t=out:st=${T - 2.5}:d=2.5,volume=0.8[p];` +
  `[p][key]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[pd];` +
  `[v][pd]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.95[a]`,
  '-map', '0:v', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-t', String(T), '-movflags', '+faststart', OUT]);
console.log(`✓ ${OUT}`);
