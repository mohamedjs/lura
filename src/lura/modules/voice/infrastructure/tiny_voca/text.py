# Vendored from huggingface.co/spaces/oddadmix/tiny-voca-arabic @64e3e2a (Apache-2.0).
# Lura edits: remove TINY_VOCA_DIACRITIZED env-file loading; bound _cache to 4096 entries.
"""Modern Standard Arabic front end: text -> phoneme tokens with a stress feature.

Same interface as the English front end it replaces (text_english.py), so the model, annotation
and training code are unchanged. The pipeline is in ``arabic_frontend``:

  undiacritized text -> phrases (split at punctuation) -> CATT diacritizer -> MSA rules -> phonemes

* A token is one phoneme; geminates are two identical consonant tokens.
* Stress (0/1) is a parallel feature from MSA syllable weight, not a token.
* Punctuation, word boundaries and the utterance edges are tokens that may last zero frames.
* ``Phonemized.optional`` marks case endings inside a phrase. The aligner keeps one only when
  the recording has it; inference always says them (formal MSA).

Diacritizations can be served from a cache (``TINY_VOCA_DIACRITIZED`` = jsonl of {"p", "d"}) so
annotation and training use exactly the vowels that were aligned.
"""

from dataclasses import dataclass, field
import hashlib
import json

from . import arabic_frontend as af

FRONTEND_VERSION = "ar-1"
FRONTENDS = ("arabic",)
FRONTEND_VARIABLE = "TINY_VOCA_FRONTEND"
_frontend = "arabic"

PAD, BOS, EOS, SP, UNK = "<pad>", "<bos>", "<eos>", "<sp>", "<unk>"
SPECIALS = (PAD, BOS, EOS, SP, UNK)
PUNCTUATION = (",", ".", "?", "!", ";", ":", "…")
_PUNCT_MAP = {"،": ",", ",": ",", ".": ".", "؟": "?", "?": "?", "!": "!", "؛": ";", ";": ";", ":": ":", "…": "…"}
CONSONANTS = tuple(af.CONSONANTS)
VOWELS = tuple(af.VOWELS)
PHONES = CONSONANTS + VOWELS
# Append-only: changing the order or removing a symbol invalidates checkpoints.
SYMBOLS = SPECIALS + PUNCTUATION + PHONES
SYMBOL_TO_ID = {symbol: index for index, symbol in enumerate(SYMBOLS)}
PHONE_SET = frozenset(PHONES)
PHONE_IDS = frozenset(SYMBOL_TO_ID[p] for p in PHONES) | {SYMBOL_TO_ID[UNK]}
VOICELESS = frozenset(af.VOICELESS)
VOWEL_SET = frozenset(VOWELS)


class NothingToPronounce(ValueError):
    """The text has no Arabic word to say."""


@dataclass(frozen=True)
class Phonemized:
    symbols: tuple
    ids: tuple
    stress: tuple
    unknown: tuple
    optional: tuple = field(default=())
    diacritized: tuple = field(default=())

    def __len__(self):
        return len(self.ids)


def frontend():
    return _frontend


def set_frontend(name):
    if name not in FRONTENDS:
        raise ValueError(f"front end must be one of {FRONTENDS}, not {name!r}")


def frontend_identity():
    return {"frontend_version": FRONTEND_VERSION, "phonemizer": "tiny_voca.arabic_frontend",
            "diacritizer": "catt-tashkeel ed_model v2", "symbols": list(SYMBOLS)}


def frontend_runtime():
    return {"phonemizer": hashlib.sha256(open(af.__file__, "rb").read()).hexdigest()}


def frontend_fingerprint():
    return hashlib.sha256(json.dumps(frontend_identity(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


_cache = {}


def _diacritized(phrases):
    missing = [p for p in phrases if p not in _cache]
    if missing:
        if len(_cache) > 4096:
            _cache.clear()
            missing = list(dict.fromkeys(phrases))
        for p, d in zip(missing, af.diacritize(missing)):
            _cache[p] = d
    return [_cache[p] for p in phrases]


def phonemize(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must not be empty")
    parts = af.split_phrases(text)
    if not parts:
        raise NothingToPronounce("the text has nothing to pronounce")
    res = af.phonemize(text, _diacritized([p for p, _ in parts]))
    symbols, stress, optional = [], [], []
    for x in res.phones:
        if x.p == "^": s = BOS
        elif x.p == "$": s = EOS
        elif x.p == " ": s = SP
        elif x.word < 0: s = _PUNCT_MAP.get(x.p, ",")
        else: s = x.p
        if s in PUNCTUATION and symbols and symbols[-1] in PUNCTUATION:
            continue
        if s in PUNCTUATION and symbols and symbols[-1] == SP:
            symbols.pop(); stress.pop(); optional.pop()
        symbols.append(s); stress.append(x.stress); optional.append(int(x.optional))
    if not any(s in PHONE_SET for s in symbols):
        raise NothingToPronounce("the text has nothing to pronounce")
    return Phonemized(tuple(symbols), tuple(SYMBOL_TO_ID[s] for s in symbols), tuple(stress), (),
                      tuple(optional), tuple(res.phrases))


def drop(ph, remove):
    """Copy of ``ph`` without the token positions in ``remove`` (dropped optional case endings)."""
    remove = set(remove)
    keep = [i for i in range(len(ph.symbols)) if i not in remove]
    pick = lambda seq: tuple(seq[i] for i in keep) if seq else seq
    return Phonemized(pick(ph.symbols), pick(ph.ids), pick(ph.stress), ph.unknown, pick(ph.optional), ph.diacritized)


def is_phone(token_ids):
    return [int(i) in PHONE_IDS for i in token_ids]
