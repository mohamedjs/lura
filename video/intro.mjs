// The landing page's intro video, in English and Arabic.
//   node intro.mjs [--lang en|ar] [--out out/intro-en.mp4] [--preview]
// Music and a wake chime are synthesised with ffmpeg; no voice, no footage, no
// keys — so CI can render it from a clean checkout.
import { mkdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { renderPage, run } from './frames.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const arg = (name, dflt) => { const i = process.argv.indexOf(name); return i > 0 ? process.argv[i + 1] : dflt; };
const LANG = arg('--lang', 'en');
const OUT = resolve(arg('--out', join(HERE, 'out', `intro-${LANG}.mp4`)));
const QUICK = process.argv.includes('--preview');
const TL = { lang: LANG, fps: 30, total: 34 };
mkdirSync(dirname(OUT), { recursive: true });

const picture = OUT.replace(/\.mp4$/, '.picture.mp4');
await renderPage({ html: join(HERE, 'intro.html'), TL, width: 1920, height: 1080, out: picture, quick: QUICK });

const T = TL.total, chimeAt = 6.2;
const pad = `aevalsrc='0.05*(sin(2*PI*110*t)+0.7*sin(2*PI*164.81*t)+0.5*sin(2*PI*220*t)+0.35*sin(2*PI*329.63*t))*(0.75+0.25*sin(2*PI*0.15*t))':s=48000:d=${T}`;
const chime = `aevalsrc='0.22*exp(-5*t)*sin(2*PI*660*t)+0.18*exp(-4*(t-0.12))*gte(t,0.12)*sin(2*PI*990*t)':s=48000:d=1.6`;
run('ffmpeg', ['-v', 'error', '-y', '-i', picture, '-f', 'lavfi', '-i', pad, '-f', 'lavfi', '-i', chime,
  '-filter_complex', `[1]lowpass=f=1400,afade=t=in:d=1.5,afade=t=out:st=${T - 2}:d=2[p];` +
    `[2]adelay=${chimeAt * 1000}:all=1[c];[p][c]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.95[a]`,
  '-map', '0:v', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '160k', '-t', String(T), '-movflags', '+faststart', OUT]);
// A poster frame for the page, before the video loads.
run('ffmpeg', ['-v', 'error', '-y', '-ss', '30.5', '-i', picture, '-frames:v', '1', '-q:v', '3', OUT.replace(/\.mp4$/, '.jpg')]);
console.log(`✓ ${OUT}`);
