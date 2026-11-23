# Vendored from huggingface.co/spaces/oddadmix/tiny-voca-arabic @64e3e2a (Apache-2.0).
# Lura edits: none.
"""Read digits as Modern Standard Arabic words before diacritization.

Cardinals use the masculine nominative form without gender agreement with the counted noun, which is
how numbers are most often read aloud. Handles Western and Arabic-Indic digits, thousands separators,
decimals (فاصلة), percent (في المائة) and a leading minus (سالب).
"""
import re

ONES = ["صفر", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة"]
TEENS = ["عشرة", "أحد عشر", "اثنا عشر", "ثلاثة عشر", "أربعة عشر", "خمسة عشر", "ستة عشر", "سبعة عشر",
         "ثمانية عشر", "تسعة عشر"]
TENS = ["", "", "عشرون", "ثلاثون", "أربعون", "خمسون", "ستون", "سبعون", "ثمانون", "تسعون"]
HUNDREDS = ["", "مائة", "مئتان", "ثلاثمائة", "أربعمائة", "خمسمائة", "ستمائة", "سبعمائة", "ثمانمائة", "تسعمائة"]
# (value, singular, dual, plural for 3-10)
SCALES = [(10 ** 9, "مليار", "ملياران", "مليارات"), (10 ** 6, "مليون", "مليونان", "ملايين"),
          (1000, "ألف", "ألفان", "آلاف")]
INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def _below_1000(n):
    parts = []
    h, rest = divmod(n, 100)
    if h:
        parts.append(HUNDREDS[h])
    if rest:
        if rest < 10:
            parts.append(ONES[rest])
        elif rest < 20:
            parts.append(TEENS[rest - 10])
        else:
            t, o = divmod(rest, 10)
            parts.append(f"{ONES[o]} و{TENS[t]}" if o else TENS[t])
    return " و".join(parts)


def number_words(n):
    if n == 0:
        return ONES[0]
    if n >= 10 ** 12:
        return " ".join(ONES[int(d)] for d in str(n))
    parts = []
    for value, one, two, few in SCALES:
        k, n = divmod(n, value)
        if not k:
            continue
        if k == 1:
            parts.append(one)
        elif k == 2:
            parts.append(two)
        elif k <= 10:
            parts.append(f"{_below_1000(k)} {few}")
        else:
            parts.append(f"{_below_1000(k)} {one}")
    if n:
        parts.append(_below_1000(n))
    return " و".join(parts)


_NUMBER = re.compile(r"(-?)(\d{1,3}(?:[,٬]\d{3})+|\d+)(?:[.٫](\d+))?(\s*[%٪])?")


def _read(m):
    sign, whole, frac, pct = m.groups()
    out = number_words(int(re.sub(r"[,٬]", "", whole)))
    if frac:
        out += " فاصلة " + (number_words(int(frac)) if not frac.startswith("0") else " ".join(ONES[int(d)] for d in frac))
    if sign:
        out = "سالب " + out
    if pct:
        out += " في المائة"
    return f" {out} "


def expand_numbers(text):
    return re.sub(r"[ \t]+", " ", _NUMBER.sub(_read, text.translate(INDIC))).strip()
