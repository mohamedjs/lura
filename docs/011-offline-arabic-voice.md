# 011 — Offline Arabic voice: tiny-voca-arabic behind the online voices
Status: Implemented (2026-11-23, Gemini 4 Neon high, 1 round; reviewed by Claude)     Builds on: docs/ARCHITECTURE.md, CLAUDE.md → "Offline voice (Piper)"

## 1. Goal

When every online voice fails (no ElevenLabs/Gemini/OpenRouter credit, quota, no network),
Lura already speaks through `_OfflineFallback` → Piper. Piper's only Arabic voice
(`ar_JO-kareem-medium`) sounds flat. This doc puts **tiny-voca-arabic**
(`oddadmix/tiny-voca-arabic`, Apache-2.0, 4.5 M params, ONNX, CPU, 24 kHz, MSA, ~200× real
time) in front of Piper **for sentences that contain Arabic script**. English sentences still
go to Piper English. If tiny-voca is not installed or fails on a sentence, that sentence goes
to Piper Arabic as today.

Measured on the maintainer's Linux box (onnxruntime 1.31.1, 2 threads): session + diacritizer
load 0.33 s warm; 3.8 s of speech in 0.15–0.23 s; 6.9 s in 0.66 s.

Pipeline per sentence: numbers → words (`numbers.py`) → automatic diacritization (CATT
encoder-decoder ONNX) → rule-based MSA phonemes (`arabic_frontend.py`, `text.py`) → tiny-voca
ONNX → float32 audio at 24 kHz.

Files (≈114 MB) are **downloaded once** into `<data>/arabic-voice/` at daemon start (like
Piper's voices), not shipped in the installers: every in-app update downloads the full
artifact, and 114 MB extra on each update is not worth it. Every download is pinned to a
revision and checked against a SHA-256.

Known limits (documented, not fixed here): one female MSA voice; Egyptian text is read with MSA
rules; Latin-script words (Notion, GitHub) are silently skipped by the front end.

## 2. Stack and packages

- New runtime dependency **onnxruntime**, in `[project].dependencies` (core, so CI, install.sh
  and release builds all get it) with a platform split — Intel Macs only have wheels up to
  1.23.2 (cp310–cp313 `macosx_13_0_x86_64` exist):
  ```
  "onnxruntime>=1.20; sys_platform != 'darwin' or platform_machine != 'x86_64'",
  "onnxruntime>=1.20,<1.24; sys_platform == 'darwin' and platform_machine == 'x86_64'",
  ```
- No other packages. `tqdm` is **not** added (the vendored code must not import it).
- Vendored third-party code (Apache-2.0), copied byte-for-byte first, then edited only as §8 says.
  Source: the demo Space at the pinned revision
  `https://huggingface.co/spaces/oddadmix/tiny-voca-arabic/resolve/64e3e2ae08f7553c38f280a58f05b955a0c296f4/<path>`:
  `tiny_voca_ar/numbers.py`, `tiny_voca_ar/text.py`, `tiny_voca_ar/arabic_frontend.py`,
  `catt_tashkeel/__init__.py`, `catt_tashkeel/models.py`, `catt_tashkeel/tokenizer.py`,
  `catt_tashkeel/bw2ar.py`, `catt_tashkeel/utils.py`, `catt_tashkeel/LICENSE`, `LICENSE`.
- Downloaded at run time (constants in `arabic_voice.py`):

  | File | URL | SHA-256 |
  |---|---|---|
  | `model.onnx` (21 364 044 B) | `https://huggingface.co/oddadmix/tiny-voca-arabic/resolve/e529f716965142f51835adc9e5661f224c3fa103/onnx/model.onnx` | `1eb04ad05ec96808eac3cb761ddb7fc0e17edb636deb3263ccb13d99f725ab8f` |
  | `model.json` | `https://huggingface.co/oddadmix/tiny-voca-arabic/resolve/e529f716965142f51835adc9e5661f224c3fa103/onnx/model.json` | `c00789356049545702f7e42624b1bdef1b105c22c38832eaec364d4d5ea007cc` |
  | `ed_model_onnx.zip` (85 926 550 B; holds `encoder.onnx`, `decoder.onnx`) | `https://github.com/abjadai/catt/releases/download/v2/ed_model_onnx.zip` | `75d2a6360725c7e0bbb238b5a950f621fb8e30b21ad7c65e91e0f36759bcb3ce` |

## 3. Structure

```
pyproject.toml                                         (changed: onnxruntime dependency)
packaging/pyinstaller/lura.spec                        (changed: collect_all "onnxruntime")
install.sh                                             (changed: offline voice step)
README.md                                              (changed: one paragraph)
CLAUDE.md                                              (changed: one bullet)
src/lura/modules/voice/application.py                  (changed: use offline.OfflineTTS / ready / ensure)
src/lura/modules/voice/infrastructure/offline.py       (NEW: router Arabic → tiny-voca → Piper)
src/lura/modules/voice/infrastructure/arabic_voice.py  (NEW: download + TinyVocaArabicTTS)
src/lura/modules/voice/infrastructure/tiny_voca/__init__.py          (NEW)
src/lura/modules/voice/infrastructure/tiny_voca/LICENSE              (NEW, copied: Space LICENSE)
src/lura/modules/voice/infrastructure/tiny_voca/NOTICE               (NEW)
src/lura/modules/voice/infrastructure/tiny_voca/numbers.py           (NEW, vendored)
src/lura/modules/voice/infrastructure/tiny_voca/text.py              (NEW, vendored + edits)
src/lura/modules/voice/infrastructure/tiny_voca/arabic_frontend.py   (NEW, vendored + edits)
src/lura/modules/voice/infrastructure/tiny_voca/catt/__init__.py     (NEW, vendored + edits)
src/lura/modules/voice/infrastructure/tiny_voca/catt/LICENSE         (NEW, copied: catt_tashkeel/LICENSE)
src/lura/modules/voice/infrastructure/tiny_voca/catt/models.py       (NEW, vendored + edits)
src/lura/modules/voice/infrastructure/tiny_voca/catt/tokenizer.py    (NEW, vendored)
src/lura/modules/voice/infrastructure/tiny_voca/catt/bw2ar.py        (NEW, vendored)
src/lura/modules/voice/infrastructure/tiny_voca/catt/utils.py        (NEW, vendored)
src/lura/selftest.py                                   (changed: one offline-voice line)
tests/test_arabic_voice.py                             (NEW, in run_all)
tests/run_all.py                                       (changed: add test_arabic_voice.py)
tests/live_arabic_voice.py                             (NEW, not in run_all; real model)
```

Nothing else. Do not touch `piper.py`, the dashboard, i18n, settings or release.yml.

## 4. File responsibilities

- **`arabic_voice.py`** — owns the tiny-voca install in `<data>/arabic-voice/` and synthesis.
  Exports `home()`, `available()`, `ready()`, `ensure()`, `TinyVocaArabicTTS`. Imports stdlib,
  `numpy`, `....shared.paths`, `.audio` (`resample`, `OUTPUT_RATE`), and lazily `onnxruntime`
  and `.tiny_voca`. Must not import onnxruntime at module import time (the module must import
  on a machine without onnxruntime). Must not touch Piper.
- **`offline.py`** — the offline leg of `_OfflineFallback`: per sentence, Arabic script →
  `TinyVocaArabicTTS` if `arabic_voice.ready()`, falling back to `PiperTTS` on any exception;
  otherwise `PiperTTS`. Exports `OfflineTTS`, `ready()`, `ensure()`. Imports `.piper` and
  `.arabic_voice` only.
- **`tiny_voca/`** — vendored front end. Pure text → phoneme ids, plus the CATT ONNX wrapper.
  No downloads, no prints, no env-var configuration, no `tqdm`, no absolute imports of
  `catt_tashkeel` / `tiny_voca_ar`.
- **`application.py`** — only swaps Piper for `offline` (§8.5).
- **`selftest.py`** — prints whether the offline voices are installed.
- **tests** — §8.7.

## 5. Layering rules

All new code lives in `voice/infrastructure/`. `application.py` imports it lazily inside
functions, as it does for `piper` today. No module outside `voice` imports any of it
(`tests/test_architecture.py` enforces this; `selftest.py` must go through `c.voice`, see §8.6).

## 6. Coding conventions (match the repo)

- `from __future__ import annotations`, type hints on public functions, `log =
  logging.getLogger(__name__)`, module docstring explaining *why*, short comments only where
  the reason is not obvious. Line length ≤ 110. Same style as `piper.py`.
- Never `print` in library code. Failures to install are `log.warning` and return `False`.
- Vendored files: keep the upstream code and comments; add at the very top a two-line comment
  `# Vendored from huggingface.co/spaces/oddadmix/tiny-voca-arabic @64e3e2a (Apache-2.0).`
  `# Lura edits: <one line listing the edits, or "none">.` Edit nothing beyond §8.3.

## 7. Interfaces

```python
# arabic_voice.py
REVISION = "e529f716965142f51835adc9e5661f224c3fa103"
FILES: dict[str, tuple[str, str]]   # relative path in home() -> (url, sha256); "model.onnx", "model.json"
CATT_ZIP: tuple[str, str]           # (url, sha256) of ed_model_onnx.zip
THREADS = 2

def home() -> Path                  # paths.data_dir() / "arabic-voice"
def available() -> bool             # importlib.util.find_spec("onnxruntime") is not None
def ready() -> bool                 # available() and model.onnx, model.json, catt/encoder.onnx, catt/decoder.onnx exist
def ensure() -> bool                # download what is missing; never raises; True when ready()

class TinyVocaArabicTTS:            # implements voice.domain.SpeechSynthesizer
    def synthesize(self, text: str) -> bytes   # 16-bit mono PCM at OUTPUT_RATE; b"" when nothing to say
    def stream(self, text: str) -> Iterator[bytes]   # yields synthesize(text) once if non-empty

# offline.py
class OfflineTTS:                   # implements SpeechSynthesizer
    def __init__(self, arabic=None, piper_tts=None)  # injectable for tests; defaults TinyVocaArabicTTS(), PiperTTS()
    def synthesize(self, text: str) -> bytes
    def stream(self, text: str) -> Iterator[bytes]
def ready() -> bool                 # piper.ready() or arabic_voice.ready()
def ensure() -> bool                # runs piper.ensure() AND arabic_voice.ensure() (both, always); returns p or a

# tiny_voca/arabic_frontend.py (added)
def set_diacritizer(diacritizer) -> None   # object with do_tashkeel_batch(list[str], batch_size, verbose)
```

## 8. Functions

### 8.1 `arabic_voice.py`

Module docstring: what tiny-voca is, why it sits before Piper for Arabic, why downloaded not
shipped (size × every update), credits (tiny-voca by Muhammed Nazeem, Arabic model by
oddadmix, CATT by Abjad AI; all Apache-2.0).

Constants: `REVISION`, `_BASE = f"https://huggingface.co/oddadmix/tiny-voca-arabic/resolve/{REVISION}/onnx/"`,
`FILES = {"model.onnx": (_BASE + "model.onnx", "1eb04a…ab8f"), "model.json": (_BASE + "model.json", "c00789…07cc")}`
(full hashes from §2), `CATT_ZIP = ("https://github.com/abjadai/catt/releases/download/v2/ed_model_onnx.zip", "75d2a6…b3ce")`,
`THREADS = 2`, `_SENTENCE_END = re.compile(r"(?<=[.!?؟…])\s+(?=\S)")`,
`_CLAUSE_END = re.compile(r"(?<=[،,;؛:])\s+")`, `_GAP = 0.12` (seconds), `_RATE = 24000`.
`_install_lock = threading.Lock()`, `_load_lock = threading.Lock()`, `_engine = None`.

`_download(url, target, sha256) -> None`: `target.parent.mkdir(parents=True, exist_ok=True)`;
stream to `target.name + ".part"` (urllib, timeout 60, 64 KiB chunks) hashing as it goes; if the
hex digest ≠ `sha256`: delete the part file and `raise ValueError(f"checksum mismatch for {url}")`;
else `part.replace(target)`.

`ensure() -> bool`: under `_install_lock`: if `ready()` return True. If not `available()`:
`log.warning("The offline Arabic voice needs onnxruntime; using Piper for Arabic.")`, return False.
`log.info("Downloading the offline Arabic voice (tiny-voca-arabic, ~110 MB once)…")`. For each
`FILES` entry whose target is missing: `_download`. If `catt/encoder.onnx` or `catt/decoder.onnx`
is missing: in `tempfile.TemporaryDirectory(dir=home())`, `_download(CATT_ZIP…)` to `tmp/catt.zip`,
open with `zipfile`, require the member names to be exactly `{"encoder.onnx", "decoder.onnx"}`
(else `ValueError("unexpected files in the diacritizer archive")`), extract each member by name
into `tmp`, `mkdir home()/"catt"`, move both into it with `Path.replace`. Any exception →
`log.warning("Could not install the offline Arabic voice: %s", exc)`, return False. On success
`log.info("Offline Arabic voice ready (%s).", home())`, return True.

`_load()`: under `_load_lock`, once: import onnxruntime as ort; read `model.json`
(`sample_rate`, `max_tokens`); `options = ort.SessionOptions(); options.intra_op_num_threads =
THREADS; options.inter_op_num_threads = 1`; `session = ort.InferenceSession(str(model.onnx),
options, providers=["CPUExecutionProvider"])`; build `CATTEncoderDecoder(encoder_path=…,
decoder_path=…, threads=THREADS)` from `.tiny_voca.catt` and `arabic_frontend.set_diacritizer(it)`.
Store `_engine = (session, rate, max_tokens - 12)`. Return it.

`TinyVocaArabicTTS.synthesize(text)`: if not `ready()`: `raise RuntimeError("the offline Arabic voice is not installed yet")`.
`session, rate, limit = _load()`. Port of the demo's `speak()` with speed 1.0, pitch_shift 0.0,
pitch_range 1.0, voice 0:
1. `text = text.replace("\n", " ").strip()`; empty → return `b""`. If `text[-1]` not in `".!?؟…"`,
   append `"."`.
2. `waves = []`; `read(piece)`: `phonemize(piece)`; `NothingToPronounce` → return; if
   `len(phones) <= limit`: run the session (inputs exactly: `tokens` int64 [1,N], `stress`
   int64 [1,N], `voice` int64 [0], `speed`/`pitch_shift`/`pitch_range` float32 [1]) and append
   `audio[0]` and a zero gap of `int(_GAP * rate)` samples; else for each part of `_pieces(piece)`
   (split by `_CLAUSE_END` if that gives >1 non-blank part, else halves by words, else `[]`):
   `read(part)`.
3. For each sentence of `_SENTENCE_END.split(expand_numbers(text))`: `read(sentence)`.
4. No waves → return `b""`. `audio = np.concatenate(waves[:-1])` (drop trailing gap);
   `peak = abs max`; if > 0.99 scale to 0.99; `pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()`;
   return `resample(pcm, rate)` (no-op when rate == OUTPUT_RATE).

`stream(text)`: `pcm = self.synthesize(text)`; `if pcm: yield pcm`.

### 8.2 `offline.py`

Docstring: the offline voice is two engines; why Arabic goes to tiny-voca first.

```python
from .piper import _ARABIC, PiperTTS
from . import arabic_voice, piper
```
`OfflineTTS.__init__(self, arabic=None, piper_tts=None)`: `self._arabic = arabic or arabic_voice.TinyVocaArabicTTS()`,
`self._piper = piper_tts or PiperTTS()`.
`synthesize(text)`: if `_ARABIC.search(text) and arabic_voice.ready()`: try
`return self._arabic.synthesize(text)` except Exception as exc:
`log.warning("Offline Arabic voice failed (%s); using Piper.", str(exc)[:120])` then fall through.
Return `self._piper.synthesize(text)`.
(Note: when tiny-voca returns `b""` for a sentence with Arabic script — nothing pronounceable —
return that `b""`; do not send it to Piper.)
`stream(text)`: `pcm = self.synthesize(text)`; `if pcm: yield pcm`.
`ready()`: `return piper.ready() or arabic_voice.ready()`.
`ensure()`: `p = piper.ensure(); a = arabic_voice.ensure(); return p or a`.

The `__init__` parameter is named `piper_tts` (not `piper`) so it does not shadow the module;
§7's signature uses that name too.

### 8.3 Vendored `tiny_voca/` edits (and nothing else)

- `numbers.py`, `catt/tokenizer.py`, `catt/bw2ar.py`, `catt/utils.py`: unchanged except the
  two-line header (bw2ar/tokenizer already use relative imports — keep them).
- `text.py`: (a) delete the `TINY_VOCA_DIACRITIZED` env-file loading in `_diacritized` (start
  with an empty dict); (b) bound the cache: if `len(_cache) > 4096` before adding, `_cache.clear()`;
  everything else unchanged.
- `arabic_frontend.py`: replace `diacritize()`'s lazy `from catt_tashkeel import
  CATTEncoderDecoder` construction with a module global set by a new
  `set_diacritizer(diacritizer)`; if `_diacritizer is None` when `diacritize()` runs, `raise
  RuntimeError("no diacritizer: call set_diacritizer() first")`. Delete the `if __name__ ==
  "__main__":` block. Everything else unchanged.
- `catt/__init__.py`: keep `from .models import CATTEncoderDecoder, CATTEncoderOnly` and
  `from .utils import remove_non_arabic`, plus the version/author lines.
- `catt/models.py`: remove `import tqdm`, `zipfile`, `urllib.request`, and the methods
  `_download_models` and `_get_model_paths`; `__init__` takes `encoder_path, decoder_path`
  as **required** arguments plus `threads: int = 2`, and passes `threads` to
  `_load_onnx_sessions` (`so.intra_op_num_threads = threads`, replacing the `CATT_THREADS`
  env lookup; add `providers=["CPUExecutionProvider"]`). Replace every `print(...)` with
  `log.debug(...)` (`log = logging.getLogger(__name__)`). In `do_tashkeel_batch`, drop the
  `tqdm` branch (iterate plainly). `CATTEncoderDecoder.__init__` / `CATTEncoderOnly.__init__`
  get the same required paths + `threads`.
- `tiny_voca/__init__.py`: docstring only ("tiny-voca-arabic's text front end and the CATT
  diacritizer, vendored; see NOTICE").
- `tiny_voca/NOTICE`: credit lines: tiny-voca by Muhammed Nazeem (github.com/n4ze3m/tiny-voca,
  Apache-2.0); tiny-voca-arabic model and Arabic front end by oddadmix
  (huggingface.co/oddadmix/tiny-voca-arabic, Apache-2.0); CATT diacritizer by Abjad AI
  (github.com/abjadai/catt, Apache-2.0). "The voice is synthetic."

### 8.4 Packaging

- `pyproject.toml`: add the two onnxruntime lines (§2) to `[project].dependencies` with the
  comment `# The offline Arabic voice (tiny-voca). Intel Macs: no wheels after 1.23.2.`
  Add `"modules/voice/infrastructure/tiny_voca/LICENSE", "modules/voice/infrastructure/tiny_voca/NOTICE", "modules/voice/infrastructure/tiny_voca/catt/LICENSE"`
  to `[tool.setuptools.package-data] lura`.
- `lura.spec`: add `"onnxruntime"` to the `collect_all` tuple; add the three license/notice
  files to `datas` (destinations `lura/modules/voice/infrastructure/tiny_voca` and
  `…/tiny_voca/catt`).
- `install.sh`: the offline-voice step calls
  `from lura.modules.voice.infrastructure.offline import ensure; ensure()` and says
  `"Installing the offline voices (Piper + Arabic, ~260 MB once)"`; comment: Piper and
  tiny-voca-arabic speak when every online voice fails.

### 8.5 `application.py`

- `_OfflineFallback` docstring: "Speak with the offline voices (tiny-voca for Arabic, Piper)…".
- `_offline_ready()`: `from .infrastructure.offline import ready; return ready()`.
- `synthesizer()`: replace `from .infrastructure.piper import PiperTTS` with
  `from .infrastructure.offline import OfflineTTS` and `PiperTTS()` with `OfflineTTS()`; the
  docstring says "the offline voices behind it all".
- `ensure_offline_voice()`: `from .infrastructure.offline import ensure`; docstring "Install
  Piper and the Arabic voice if missing (~260 MB once)".
- Add `@staticmethod def offline_voices() -> dict[str, bool]`: returns
  `{"piper": piper.ready(), "arabic": arabic_voice.ready()}` (lazy imports).

### 8.6 `selftest.py`

In the voice section, after the online speech check (inside or after that `try`, not
depending on keys), add:
`v = c.voice.offline_voices()` and
`ok(f"offline voices: Piper {'ready' if v['piper'] else 'not installed'}, Arabic {'ready' if v['arabic'] else 'not installed'}")`.
Use the same `ok` helper already in the file.

### 8.7 Tests

`tests/test_arabic_voice.py` (plain script like the others: `import _setup` first, functions
called from `main()`, prints `arabic voice OK` and exits 0; no network, no model files, < 3 s):
1. `numbers_read_as_words`: `expand_numbers("عندي 3 تفاحات")` contains no ASCII digit.
2. `not_ready_without_files`: `arabic_voice.ready()` is False in the temp data dir;
   `TinyVocaArabicTTS().synthesize("مرحبا")` raises `RuntimeError`.
3. `checksum_mismatch_deletes`: write a file in a temp dir, call `_download("file://" + path,
   target, "0"*64)` → `ValueError`, and neither `target` nor `target.part` exists.
4. `routing`: fakes `A` (records calls, returns `b"A"`), `P` (returns `b"P"`); patch
   `arabic_voice.ready` to return True: Arabic text → `b"A"`, English → `b"P"`; `A` raising →
   Arabic text → `b"P"`; `A` returning `b""` → `b""` (Piper not called); patch `ready` False →
   Arabic text → `b"P"`. `stream` yields nothing for `b""`.
5. `offline_ready_either`: patch `piper.ready`/`arabic_voice.ready` combos → `offline.ready()`
   is their OR.
6. `frontend_needs_diacritizer`: `arabic_frontend.diacritize(["مرحبا"])` raises `RuntimeError`
   before `set_diacritizer`; with a fake diacritizer returning its input, `text.phonemize("مرحبا.")`
   returns a `Phonemized` whose `ids` are non-empty.
7. `import_without_onnxruntime`: importing `arabic_voice` and `offline` does not import
   `onnxruntime` (`"onnxruntime" not in sys.modules` after import, checked in a subprocess
   with a clean interpreter and `PYTHONPATH=src`).
8. `vendored_has_no_tqdm_or_print`: no file under `tiny_voca/` contains `tqdm` or `print(`
   (check via text search).
Restore every patch in `finally`.

`tests/run_all.py`: append `"test_arabic_voice.py"` to `CHECKS`.

`tests/live_arabic_voice.py` (not in run_all): uses the **real** data dir (does not import
`_setup`; inserts `src` into `sys.path`), runs `arabic_voice.ensure()`, then synthesizes
"تمام يا محمد، ضفت التاسك على نوشن." and "عندك اجتماع الساعة 3 العصر.", prints seconds of
audio and wall time per sentence, writes `arabic-voice-<n>.wav` (24 kHz mono 16-bit, `wave`
module) into the directory given as `sys.argv[1]` (default: current dir), exits 1 if any clip
is empty.

## 9. Tasks

1. Vendor the ten files (§2) into `tiny_voca/` with headers; apply §8.3 edits; LICENSE/NOTICE.
2. `arabic_voice.py` (§8.1).
3. `offline.py` (§8.2).
4. `application.py`, `selftest.py` (§8.5, §8.6).
5. `pyproject.toml`, `lura.spec`, `install.sh` (§8.4).
6. Tests (§8.7) and `run_all.py`.
7. README: under the existing offline-voice text (search "Piper"), add one paragraph: Arabic
   sentences use tiny-voca-arabic (MSA, downloaded once ~110 MB, Apache-2.0, credits), Piper
   for English and as backup; Latin words in Arabic sentences are skipped. CLAUDE.md: under
   the "Offline voice (Piper)" bullet add a sub-bullet with the same facts and the file names.

## 10. Acceptance checks

Run from the repo root with the machine's Python that has numpy + onnxruntime
(`/tmp/claude-1000/-home-mohamed--claude-spares-spare-cA9rq9/79804ebb-8efb-407e-b35c-aadd6b649f9c/scratchpad/tva/venv/bin/python`
has onnxruntime 1.31 and numpy; install `sounddevice vosk certifi` into it with pip if a check
needs them):

```bash
python -m compileall -q src/                         # no output
PYTHONPATH=src python tests/test_arabic_voice.py      # arabic voice OK
PYTHONPATH=src python tests/test_domain.py            # passes (Piper _ARABIC check still there)
PYTHONPATH=src python tests/test_architecture.py      # passes
PYTHONPATH=src python tests/run_all.py                # All checks passed.
grep -rn "tqdm\|print(" src/lura/modules/voice/infrastructure/tiny_voca/   # no output
```

Do **not** run `tests/live_arabic_voice.py` with the real data dir yourself; the reviewer runs it.

## 11. Out of scope

Dashboard UI, settings, i18n, release.yml, version bump, piper.py, any change to how online
voices are chosen. Do not commit. Do not edit anything under `docs/`.

## 12. Decisions

- Download vs bundle: download into `<data>/arabic-voice/` (size × every in-app update).
- Arabic routing is per sentence by script, same regex as Piper (`piper._ARABIC`).
- tiny-voca returning `b""` (nothing pronounceable, e.g. only Latin words) is final for that
  sentence; Piper is only for exceptions or when tiny-voca is not installed.
- Accepted deviations (review): `text._diacritized` recomputes `missing` after clearing the cache
  (avoids a KeyError); the vendored-code test ignores commented-out `#print(` and
  `frontend_fingerprint(`.
- Follow-up (2026-11-23, Claude): `merge_tashkeel` keeps written tashkeel and drops CATT's case
  endings; the brain is asked for full tashkeel (closing language rule + per-request
  `with_tashkeel_note`); `LURA_OFFLINE_VOICE=1` forces the offline voice. Released in 2.7.0.
