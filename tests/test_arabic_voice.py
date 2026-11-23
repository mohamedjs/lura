"""Offline Arabic voice (tiny-voca-arabic) and offline router checks.

Runs without network access or model files.
"""

from __future__ import annotations

import _setup  # noqa: F401
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from lura.modules.voice.infrastructure import arabic_voice, offline, piper
from lura.modules.voice.infrastructure.tiny_voca import arabic_frontend, numbers, text


def numbers_read_as_words() -> None:
    out = numbers.expand_numbers("عندي 3 تفاحات")
    assert not any(c.isascii() and c.isdigit() for c in out), out


def not_ready_without_files() -> None:
    assert not arabic_voice.ready()
    try:
        arabic_voice.TinyVocaArabicTTS().synthesize("مرحبا")
        raise AssertionError("expected RuntimeError when files are missing")
    except RuntimeError:
        pass


def checksum_mismatch_deletes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        src = root / "sample.bin"
        src.write_bytes(b"hello world")
        target = root / "out" / "sample.bin"
        try:
            arabic_voice._download("file://" + str(src), target, "0" * 64)
            raise AssertionError("expected ValueError on checksum mismatch")
        except ValueError:
            pass
        assert not target.exists()
        assert not target.with_name(target.name + ".part").exists()


def routing() -> None:
    class FakeArabic:
        def __init__(self, reply=b"A", fail=False):
            self.reply = reply
            self.fail = fail
            self.calls: list[str] = []

        def synthesize(self, s: str) -> bytes:
            self.calls.append(s)
            if self.fail:
                raise RuntimeError("boom")
            return self.reply

    class FakePiper:
        def __init__(self):
            self.calls: list[str] = []

        def synthesize(self, s: str) -> bytes:
            self.calls.append(s)
            return b"P"

    orig_ready = arabic_voice.ready
    try:
        arabic_voice.ready = lambda: True

        a, p = FakeArabic(), FakePiper()
        tts = offline.OfflineTTS(arabic=a, piper_tts=p)
        assert tts.synthesize("مرحبا") == b"A" and a.calls == ["مرحبا"] and p.calls == []
        assert tts.synthesize("hello") == b"P" and p.calls == ["hello"]

        a_fail, p_fail = FakeArabic(fail=True), FakePiper()
        tts_fail = offline.OfflineTTS(arabic=a_fail, piper_tts=p_fail)
        assert tts_fail.synthesize("مرحبا") == b"P" and p_fail.calls == ["مرحبا"]

        a_empty, p_empty = FakeArabic(reply=b""), FakePiper()
        tts_empty = offline.OfflineTTS(arabic=a_empty, piper_tts=p_empty)
        assert tts_empty.synthesize("مرحبا") == b"" and p_empty.calls == []
        assert list(tts_empty.stream("مرحبا")) == []

        arabic_voice.ready = lambda: False
        a_off, p_off = FakeArabic(), FakePiper()
        tts_off = offline.OfflineTTS(arabic=a_off, piper_tts=p_off)
        assert tts_off.synthesize("مرحبا") == b"P" and a_off.calls == [] and p_off.calls == ["مرحبا"]
    finally:
        arabic_voice.ready = orig_ready


def offline_ready_either() -> None:
    orig_piper, orig_ar = piper.ready, arabic_voice.ready
    try:
        for p_val in (False, True):
            for a_val in (False, True):
                piper.ready = lambda v=p_val: v
                arabic_voice.ready = lambda v=a_val: v
                assert offline.ready() is (p_val or a_val), (p_val, a_val)
    finally:
        piper.ready, arabic_voice.ready = orig_piper, orig_ar


def frontend_needs_diacritizer() -> None:
    class FakeDiacritizer:
        def do_tashkeel_batch(self, texts, batch_size=32, verbose=False):
            return list(texts)

    orig_diacritizer = arabic_frontend._diacritizer
    orig_cache = dict(text._cache)
    try:
        arabic_frontend.set_diacritizer(None)
        text._cache.clear()
        try:
            arabic_frontend.diacritize(["مرحبا"])
            raise AssertionError("expected RuntimeError without diacritizer")
        except RuntimeError:
            pass
        arabic_frontend.set_diacritizer(FakeDiacritizer())
        ph = text.phonemize("مرحبا.")
        assert isinstance(ph, text.Phonemized) and len(ph.ids) > 0
    finally:
        arabic_frontend.set_diacritizer(orig_diacritizer)
        text._cache.clear()
        text._cache.update(orig_cache)


def import_without_onnxruntime() -> None:
    env = {**os.environ, "PYTHONPATH": str(_setup.ROOT / "src")}
    code = (
        "import sys; "
        "import lura.modules.voice.infrastructure.arabic_voice; "
        "import lura.modules.voice.infrastructure.offline; "
        "assert 'onnxruntime' not in sys.modules, 'onnxruntime was imported eagerly'"
    )
    proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr or proc.stdout


def vendored_has_no_tqdm_or_print() -> None:
    root = Path(arabic_voice.__file__).resolve().parent / "tiny_voca"
    print_call = re.compile(r"(?<![A-Za-z0-9_])print\(")
    for path in root.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        assert "tqdm" not in content, f"tqdm found in {path}"
        for lineno, line in enumerate(content.splitlines(), 1):
            code = line.split("#", 1)[0]
            assert not print_call.search(code), f"print( found in {path}:{lineno}"


def writer_tashkeel_wins() -> None:
    from lura.modules.voice.infrastructure.tiny_voca.arabic_frontend import merge_tashkeel
    catt = "هَذَا نَمُوذَجٌ صَغِيرٌ"
    assert merge_tashkeel("هذا نموذج صغير", catt) == "هَذَا نَمُوذَجْ صَغِيرْ", "no marks: CATT's, no case endings"
    assert merge_tashkeel("هذا نُموذَجٌ صَغيرٌ", catt) == "هَذَا نُمُوذَجٌ صَغِيرٌ", "written marks win, endings too"
    # Egyptian: a vowelled word with a bare last letter gets no case ending
    assert merge_tashkeel("تَمَام يَا مُحَمَّد", "تَمَامٌ يَا مُحَمَّدُ") == "تَمَامْ يَا مُحَمَّدْ"
    assert merge_tashkeel("الحَقّ", "الْحَقُّ") == "الْحَقّ"
    # Latin words are dropped by CATT; the Arabic words still line up
    assert merge_tashkeel("ضِفْت Notion", "ضِفْتُ") == "ضِفْتْ"
    assert merge_tashkeel("ضِفْت كتاب", "ضِفْتُ") == "ضِفْتْ", "words do not line up: CATT, no case endings"


def main() -> int:
    numbers_read_as_words()
    not_ready_without_files()
    checksum_mismatch_deletes()
    routing()
    offline_ready_either()
    frontend_needs_diacritizer()
    import_without_onnxruntime()
    vendored_has_no_tqdm_or_print()
    writer_tashkeel_wins()
    print("arabic voice OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
