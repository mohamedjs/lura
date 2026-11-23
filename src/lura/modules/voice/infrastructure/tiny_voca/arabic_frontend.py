# Vendored from huggingface.co/spaces/oddadmix/tiny-voca-arabic @64e3e2a (Apache-2.0).
# Lura edits: inject diacritizer via set_diacritizer(); keep the writer's tashkeel and drop MSA case endings (merge_tashkeel); remove __main__ block.
"""MSA front end: undiacritized text -> phonemes + stress, for an Arabic tiny-voca.

text --(split at punctuation)--> phrases --CATT diacritizer--> fully vowelled phrases --rules--> phonemes

Rules (MSA, connected speech):
  * definite article: sun letters assimilate the l (al+shams -> aʃʃams), moon letters keep it
  * hamzat al-wasl: pronounced (ʔa / ʔi) only at the start of a phrase, elided inside it; a preceding
    long vowel is shortened (fiː + l -> fil), a preceding sukun gets a helping vowel (min + al -> mina l)
  * shadda -> doubled consonant; tanween -> vowel + n; long vowels from ا و ي ى and dagger alif
  * ta marbuta: t + case vowel inside a phrase, dropped at a pause (pausal -a)
  * pausal forms (before punctuation / at the end): final short vowel and -un/-in dropped, -an -> aː
  * emphasis: a / aː next to an emphatic (sˤ dˤ tˤ ðˤ), q, or after x ɣ r -> back ɑ / ɑː; dark l in الله
  * word stress by syllable weight: superheavy final > heavy penult > antepenult
Inside a phrase the case ending is marked optional (Phone.optional): the voice sometimes drops it, and
the aligner keeps it only when the audio has it.
"""
from dataclasses import dataclass, field
import re
import unicodedata

FATHA, DAMMA, KASRA, SUKUN, SHADDA = "َ", "ُ", "ِ", "ْ", "ّ"
FATHATAN, DAMMATAN, KASRATAN, DAGGER = "ً", "ٌ", "ٍ", "ٰ"
MARKS = {FATHA, DAMMA, KASRA, SUKUN, SHADDA, FATHATAN, DAMMATAN, KASRATAN, DAGGER}
SHORT = {FATHA: "a", DAMMA: "u", KASRA: "i"}
TANWEEN = {FATHATAN: "a", DAMMATAN: "u", KASRATAN: "i"}

CONS = {"ء": "ʔ", "أ": "ʔ", "إ": "ʔ", "ؤ": "ʔ", "ئ": "ʔ", "ب": "b", "ت": "t", "ث": "θ", "ج": "dʒ", "ح": "ħ",
        "خ": "x", "د": "d", "ذ": "ð", "ر": "r", "ز": "z", "س": "s", "ش": "ʃ", "ص": "sˤ", "ض": "dˤ", "ط": "tˤ",
        "ظ": "ðˤ", "ع": "ʕ", "غ": "ɣ", "ف": "f", "ق": "q", "ك": "k", "ل": "l", "م": "m", "ن": "n", "ه": "h",
        "و": "w", "ي": "j", "ة": "t"}
SUN = set("تثدذرزسشصضطظلن")
EMPHATIC = {"sˤ", "dˤ", "tˤ", "ðˤ", "q", "lˤ"}
BACKING_BEFORE = {"x", "ɣ", "r"}            # back a/aː when they precede the vowel
CONSONANTS = sorted(set(CONS.values()) | {"lˤ"})
VOWELS = ["a", "i", "u", "aː", "iː", "uː", "ɑ", "ɑː"]
LONG = {"aː", "iː", "uː", "ɑː"}
VOICELESS = {"ʔ", "t", "θ", "ħ", "x", "s", "ʃ", "sˤ", "tˤ", "f", "q", "k", "h"}
PAUSE_PUNCT = "،,.؟?!:;؛…"

# Words whose spelling hides a long vowel (written without the dagger alif) or is irregular.
LEXICON = {
    "هذا": "haːðaː", "هذه": "haːðihi", "هذان": "haːðaːni", "هذين": "haːðajni", "هؤلاء": "haːʔulaːʔi",
    "ذلك": "ðaːlika", "ذلكم": "ðaːlikum", "تلك": "tilka", "لكن": "laːkin", "لكنه": "laːkinnahu",
    "لكنها": "laːkinnahaː", "لكنني": "laːkinnaniː", "لكني": "laːkinniː", "أولئك": "ʔulaːʔika", "هكذا": "haːkaðaː",
    "الله": "ʔallˤaːh", "والله": "wallˤaːhi", "بالله": "billaːhi", "لله": "lillaːhi", "الرحمن": "ʔarraħmaːn",
    "إله": "ʔilaːh", "طه": "tˤaːhaː", "داود": "daːwuːd", "عمرو": "ʕamr", "مئة": "miʔa", "مائة": "miʔa",
}


@dataclass
class Phone:
    p: str
    word: int                 # word index in the utterance (-1 = boundary/punctuation token)
    stress: int = 0
    optional: bool = False    # case ending inside a phrase: the aligner may drop it


@dataclass
class Result:
    phrases: list             # diacritized phrases
    phones: list = field(default_factory=list)

    def symbols(self):
        return [x.p for x in self.phones]


_diacritizer = None


def set_diacritizer(diacritizer) -> None:
    global _diacritizer
    _diacritizer = diacritizer


def diacritize(phrases, batch_size=32):
    if _diacritizer is None:
        raise RuntimeError("no diacritizer: call set_diacritizer() first")
    todo = [p for p in phrases if p.strip()]
    out = iter(_diacritizer.do_tashkeel_batch(todo, batch_size=batch_size, verbose=False)) if todo else iter([])
    return [merge_tashkeel(p, next(out)) if p.strip() else "" for p in phrases]


# Lura: CATT strips the input's marks and predicts its own. Text that already carries tashkeel
# (Lura's brain writes it) knows the intended reading, Egyptian included, so its marks win
# letter by letter and CATT only fills the letters left bare.
_KEPT = re.compile("[\u0621-\u063A\u0641-\u064A]")    # the letters CATT keeps (utils.remove_non_arabic)
_MARK = re.compile("[\u064B-\u0652]")                    # tanween, harakat, shadda, sukun
_VOWEL = re.compile("[\u064B-\u0650]")                   # tanween and short vowels
SHADDA_MARK, SUKUN_MARK = "\u0651", "\u0652"


def _letters(word):
    """[(letter, marks)] for the letters CATT keeps; marks are the tashkeel written on each."""
    out = []
    for ch in word:
        if _KEPT.match(ch):
            out.append([ch, ""])
        elif _MARK.match(ch) and out:
            out[-1][1] += ch
    return out


def merge_tashkeel(given, predicted):
    """CATT's ``predicted`` phrase with the marks written in ``given`` kept where they exist, read
    the Egyptian way: a last letter the writer left bare gets no case ending (CATT's are MSA)."""
    theirs = [_letters(w) for w in predicted.split()]
    mine = [w for w in (_letters(w) for w in given.split()) if w]
    if [[c for c, _ in w] for w in mine] != [[c for c, _ in w] for w in theirs]:
        mine = [[[c, ""] for c, _ in w] for w in theirs]      # words do not line up: CATT's marks only
    words = []
    for a, b in zip(mine, theirs):
        if not b:
            continue
        marked = [m or n for (_, m), (_, n) in zip(a, b)]
        if not a[-1][1] and _VOWEL.search(marked[-1]):
            marked[-1] = SHADDA_MARK if SHADDA_MARK in marked[-1] else SUKUN_MARK
        words.append("".join(c for (ch, _), m in zip(a, marked) for c in ch + m))
    return " ".join(words)


def normalize(text):
    text = unicodedata.normalize("NFC", text).replace("ـ", "")          # tatweel
    text = re.sub(r"[\"'«»“”()\[\]{}<>*_~`]", " ", text)
    text = re.sub(r"\s*-\s*", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def split_phrases(text):
    """[(phrase, punct)] split at pause punctuation; punct is the closing mark or '' at the end."""
    parts = re.split(f"([{re.escape(PAUSE_PUNCT)}]+)", normalize(text))
    out = []
    for i in range(0, len(parts), 2):
        ph = parts[i].strip(); pu = parts[i + 1][0] if i + 1 < len(parts) else ""
        if ph:
            out.append((ph, pu))
        elif pu and out:
            out[-1] = (out[-1][0], out[-1][1] or pu)
    return out


def letters(word):
    """[(letter, set_of_marks)] with marks attached to the preceding letter."""
    out = []
    for ch in word:
        if ch in MARKS and out:
            out[-1][1].add(ch)
        elif "ء" <= ch <= "ي" or ch == "ٱ":
            out.append([ch, set()])
    return out


def strip_marks(s):
    return "".join(c for c in s if c not in MARKS)


def word_phones(word, first_in_phrase, last_in_phrase):
    """Phones of one diacritized word -> (list of (phone, optional), starts_with_wasl)."""
    bare = strip_marks(word)
    if bare in LEXICON:
        seq = re.findall(r"dʒ|sˤ|dˤ|tˤ|ðˤ|lˤ|[a-zʔʕħθðʃɣχ]ː?", LEXICON[bare])
        if bare.startswith("ال") and not first_in_phrase:
            seq = seq[2:]
        if last_in_phrase is False and LEXICON[bare][-1] in "aiu" and len(seq) > 1:
            return [(s, i == len(seq) - 1) for i, s in enumerate(seq)], bare.startswith("ال")
        return [(s, False) for s in seq], bare.startswith("ال")
    L = letters(word)
    if not L:
        return [], False
    out = []
    wasl = False
    i = 0
    # proclitics before a definite article: و ف ب ك ل + ال / لل
    pro = []
    while i < len(L) - 2 and L[i][0] in "وفبك" and L[i + 1][0] == "ا" and L[i + 2][0] == "ل" and i == 0:
        pro.append(L[i]); i += 1; break
    if i == 0 and len(L) > 2 and L[0][0] == "ل" and L[1][0] == "ل" and not (L[0][1] & {SUKUN}):
        # لل = li + l(article)
        out += [("l", False), ("i", False)]; i = 1
        art_l = True
    else:
        art_l = False
    for c, m in pro:
        out += [(CONS[c], False), (SHORT.get(next(iter(m & set(SHORT)), FATHA if c != "ب" else KASRA), "a"), False)]
    # article
    if not art_l and i < len(L) - 1 and L[i][0] in "اٱ" and L[i + 1][0] == "ل":
        nxt = L[i + 2][0] if i + 2 < len(L) else ""
        if not pro:
            wasl = True
            if first_in_phrase:
                out += [("ʔ", False), ("a", False)]
        i += 1                                          # skip the alif
        if nxt in SUN:
            i += 1                                      # silent l; the sun letter carries shadda
            if L[i][0] != "ل" and SHADDA not in L[i][1]:
                L[i][1].add(SHADDA)
        else:
            out.append(("l", False)); i += 1
    elif art_l:
        i = 1
        nxt = L[2][0] if len(L) > 2 else ""
        if nxt in SUN and nxt != "ل":
            i = 2
            if SHADDA not in L[2][1]:
                L[2][1].add(SHADDA)
        else:
            out.append(("l", False)); i = 2
    elif i == 0 and L[0][0] in "اٱ":                  # other hamzat al-wasl (verbs, ism, ibn ...)
        wasl = True
        v = SHORT.get(next(iter(L[0][1] & set(SHORT)), KASRA), "i")
        if first_in_phrase:
            out += [("ʔ", False), (v, False)]
        i = 1
    n = len(L)
    while i < n:
        c, m = L[i]
        last = i == n - 1
        nxt = L[i + 1] if i + 1 < n else None
        prev_v = out[-1][0] if out else ""
        if c == "ا":
            if prev_v in ("a", "ɑ"):
                out[-1] = ("aː", False)
            elif i == n - 1 and len(L) > 1 and L[i - 1][0] == "و":
                pass                                    # silent alif after plural waw (كتبوا)
            elif FATHATAN in L[i - 1][1] if i else False:
                pass
            else:
                out.append(("aː", False))
            i += 1; continue
        if c == "آ":
            out += [("ʔ", False), ("aː", False)]; i += 1; continue
        if c == "ى":
            if prev_v in ("a", "ɑ") or not prev_v:
                if out: out[-1] = ("aː", False)
                else: out.append(("aː", False))
            elif prev_v == "i":
                out[-1] = ("iː", False)
            else:
                out.append(("aː", False))
            i += 1; continue
        if c in "وي" and not (m & (set(SHORT) | set(TANWEEN) | {SHADDA})):
            longv = {"و": ("u", "uː"), "ي": ("i", "iː")}[c]
            if prev_v == longv[0]:
                out[-1] = (longv[1], False); i += 1; continue
            if prev_v in ("a", "ɑ") and SUKUN in m:
                out.append((CONS[c], False)); i += 1; continue   # diphthong aw / aj
            if not prev_v or prev_v in VOWELS:
                if SUKUN not in m and not m and prev_v not in VOWELS:
                    out.append((longv[1], False)); i += 1; continue
        if c == "ة":
            if last_in_phrase:
                i += 1; continue                        # pausal: ...a(h)
            cons = "t"
        else:
            cons = CONS.get(c)
        if cons is None:
            i += 1; continue
        out.append((cons, False))
        if c in "أإ" and not (m & (set(SHORT) | set(TANWEEN) | {SUKUN})) and nxt is not None and nxt[0] not in "اوي":
            m = m | {KASRA if c == "إ" else FATHA}
        if SHADDA in m:
            out.append((cons, False))
        if DAGGER in m:
            out.append(("aː", False))
        tv = next(iter(m & set(TANWEEN)), None)
        sv = next(iter(m & set(SHORT)), None)
        if tv:
            v = TANWEEN[tv]
            if last_in_phrase:
                if v == "a" and c != "ة":
                    out.append(("aː", False))
            else:
                out += [(v, True), ("n", True)]
        elif sv:
            v = SHORT[sv]
            if last and last_in_phrase:
                pass                                    # pausal: final short vowel dropped
            else:
                out.append((v, last))
        i += 1
    return out, wasl


def emphasis(seq):
    """Back a/aː near emphatics. seq: list of phone strings (one word, or the whole phrase)."""
    out = list(seq)
    for k, p in enumerate(out):
        if p not in ("a", "aː"):
            continue
        prev = out[k - 1] if k else ""; nxt = out[k + 1] if k + 1 < len(out) else ""
        nxt2 = out[k + 2] if k + 2 < len(out) else ""
        if prev in EMPHATIC or prev in BACKING_BEFORE or nxt in EMPHATIC or (nxt not in VOWELS and nxt2 in EMPHATIC and False):
            out[k] = "ɑ" if p == "a" else "ɑː"
    return out


def syllable_stress(phones):
    """Index (into phones) of the stressed vowel of one word, by MSA syllable weight."""
    vidx = [k for k, p in enumerate(phones) if p in VOWELS]
    if not vidx:
        return None
    syl = []                       # (vowel index, weight) weight: 1 light, 2 heavy, 3 superheavy
    for j, k in enumerate(vidx):
        end = vidx[j + 1] - 1 if j + 1 < len(vidx) else len(phones)   # next onset consonant belongs to next syllable
        coda = (vidx[j + 1] - k - 2) if j + 1 < len(vidx) else (len(phones) - k - 1)
        coda = max(coda, 0)
        w = (2 if phones[k] in LONG else 1) + min(coda, 2) - (0 if phones[k] in LONG or coda else 0)
        w = 1 if (phones[k] not in LONG and coda == 0) else (2 if (phones[k] in LONG) + coda == 1 else 3)
        syl.append((k, w))
    if len(syl) == 1:
        return syl[0][0]
    if syl[-1][1] == 3:
        return syl[-1][0]
    if syl[-2][1] >= 2 or len(syl) == 2:
        return syl[-2][0]
    return syl[-3][0]


def phonemize(text, diacritized=None):
    """text -> Result. Tokens: '^' start, '$' end, ' ' word boundary, punctuation marks, phones."""
    parts = split_phrases(text)
    dia = diacritized if diacritized is not None else diacritize([p for p, _ in parts])
    res = Result(phrases=dia)
    res.phones.append(Phone("^", -1))
    wi = 0
    for (phrase, punct), dphrase in zip(parts, dia):
        words = dphrase.split()
        pw = []
        for j, w in enumerate(words):
            seq, wasl = word_phones(w, j == 0, j == len(words) - 1)
            pw.append([seq, wasl])
        # elision across words: wasl after a long vowel shortens it; after a consonant add a helping vowel
        for j in range(1, len(pw)):
            if pw[j][1] and pw[j - 1][0]:
                prev = pw[j - 1][0]
                last = prev[-1][0]
                if last in LONG:
                    prev[-1] = ({"aː": "a", "iː": "i", "uː": "u", "ɑː": "ɑ"}[last], False)
                elif last not in VOWELS:
                    helper = "u" if strip_marks(words[j - 1]) in ("هم", "أنتم", "لكم", "عليهم", "بكم", "منهم") else \
                             ("a" if strip_marks(words[j - 1]) == "من" else "i")
                    prev.append((helper, False))
                elif prev[-1][1]:
                    prev[-1] = (last, False)               # the case vowel carries the liaison: no longer optional
        for j in range(1, len(pw)):
            if strip_marks(words[j]) == "الله" and pw[j - 1][0] and pw[j - 1][0][-1][0] == "i":
                pw[j][0] = [("l" if p == "lˤ" else p, o) for p, o in pw[j][0]]
        for j, (seq, _) in enumerate(pw):
            if not seq:
                continue
            ps = emphasis([p for p, _ in seq])
            st = syllable_stress(ps)
            if j:
                res.phones.append(Phone(" ", -1))
            for k, (p, (_, opt)) in enumerate(zip(ps, seq)):
                res.phones.append(Phone(p, wi, 1 if k == st else 0, opt))
            wi += 1
        res.phones.append(Phone(punct if punct else " ", -1) if punct else Phone(" ", -1))
    if res.phones[-1].p == " ":
        res.phones.pop()
    res.phones.append(Phone("$", -1))
    return res


def show(res):
    s = []
    for x in res.phones:
        if x.word < 0:
            s.append({"^": "", "$": "", " ": " "}.get(x.p, f" {x.p} "))
        else:
            s.append(("ˈ" if x.stress else "") + x.p + ("?" if x.optional else ""))
    return "".join(s).strip()
