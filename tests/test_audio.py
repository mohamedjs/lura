"""The microphone path against a fake sounddevice: a USB mic that only does 48 kHz stereo."""

import _setup  # noqa: F401
import sys
import threading
import time
import types

import numpy as np

DEVICES = [{"name": "pulse", "hostapi": 0, "max_input_channels": 32, "max_output_channels": 32, "default_samplerate": 44100.0},
           {"name": "USB Mic: Audio (hw:2,0)", "hostapi": 0, "max_input_channels": 2, "max_output_channels": 0,
            "default_samplerate": 48000.0}]
#: Loudness per 100 ms block the fake mic plays: quiet, a word, quiet.
SCRIPT = [0.005] * 20 + [0.2, 0.25, 0.01, 0.2, 0.22] + [0.005] * 15
opened = []


BUSY = set()


class FakeInput:
    def __init__(self, samplerate, blocksize, device, dtype, channels, callback):
        if device in BUSY:
            raise OSError("Device unavailable [PaErrorCode -9985]")   # held by PipeWire
        opened.append((device, samplerate, channels))
        self._args = samplerate, blocksize, channels, callback
        self._stop = threading.Event()

    def start(self):
        rate, size, channels, callback = self._args

        def feed():
            for level in SCRIPT:
                if self._stop.is_set():
                    return
                block = np.sin(np.arange(size) * 2 * np.pi * 300 / rate) * level * 32767 * 1.414
                callback(np.repeat(block.astype(np.int16), channels).tobytes(), size, None, None)
        threading.Thread(target=feed, daemon=True).start()

    def stop(self):
        self._stop.set()

    def close(self):
        pass


def check_input_settings(device, samplerate, channels, dtype):
    if device == 1 and (samplerate != 48000 or channels != 2):
        raise ValueError("Invalid sample rate")   # what PortAudio says for a raw hw device


def install_fake():
    sd = types.ModuleType("sounddevice")
    sd.query_devices = lambda index=None: DEVICES if index is None else DEVICES[index]
    sd.default = types.SimpleNamespace(hostapi=0, device=[0, 0])
    sd.check_input_settings = check_input_settings
    sd.check_output_settings = lambda **k: None
    sd.RawInputStream = FakeInput
    sys.modules["sounddevice"] = sd


def tone(level, n=1600, rate=16000):
    """A block whose speech-band level is ``level``."""
    return (np.sin(np.arange(n) * 2 * np.pi * 1000 / rate) * level * 32768 * 1.414).astype(np.int16).tobytes()


class FakeMic:
    """Hands scripted 100 ms blocks to ``on_block``, ten times faster than real time."""
    script: list = []

    def __init__(self, device=None, block_seconds=0.1, on_block=None):
        self._on_block, self.latency, self.closed = on_block, 0.0, threading.Event()

    def __enter__(self):
        def run():
            for level in FakeMic.script:
                if self.closed.is_set():
                    return
                self._on_block(tone(level))
                time.sleep(0.01)
        threading.Thread(target=run, daemon=True).start()
        FakeMic.last = self
        return self

    def __exit__(self, *exc):
        self.closed.set()


def check_barge_in(Settings):
    from lura.modules.voice.infrastructure import audio, barge_in

    class Player:
        interrupted = 0

        def interrupt(self):
            Player.interrupted += 1

    def reply(script):
        """She starts speaking after 10 blocks of the room; return the listener when it settles."""
        FakeMic.script = script
        listener = barge_in.BargeInListener(Settings(silence_factor=2.0))
        time.sleep(0.12)                       # the brain thinks: the room is measured
        listener.start(Player())
        speaking = tone(0.2, 1200, 24000)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and listener.carry is None and not FakeMic.last.closed.is_set():
            listener.played(speaking, time.monotonic())
            time.sleep(0.005)
        return listener

    barge_in.MicStream = FakeMic
    # Headphones: the mic hears the room (0.015) until the user talks (0.04) over her,
    # after the 1.2 s probe of her first words.
    listener = reply([0.015] * 32 + [0.04] * 40 + [0.015] * 40)
    assert Player.interrupted == 1 and listener.carry is not None, "the user took the floor"
    heard = listener.carry.heard
    assert len(heard) <= 30 and audio.speech_level(heard[-1]) > 0.03 > audio.speech_level(heard[0])
    s = Settings(silence_ms=500, silence_factor=2.0)
    pcm = audio.record_utterance(s, carry=listener.carry)    # carries on from their first words
    assert FakeMic.last.closed.is_set(), "the recorder closes the carried mic"
    assert len(pcm) / 2 / 16000 > 3.0, len(pcm) / 32000   # 4 s of words, not cut at the hand-over
    # Speakers: she is loud in the mic, so it stands down and lets the mic go.
    Player.interrupted = 0
    listener = reply([0.015] * 12 + [0.15] * 60)
    assert FakeMic.last.closed.wait(2) and listener.carry is None and Player.interrupted == 0


def main() -> int:
    install_fake()
    from lura.modules.settings import Settings
    from lura.modules.voice.infrastructure import audio
    check_barge_in(Settings)

    audio.REVERB_SECONDS = 0
    s = Settings(input_device="USB Mic: Audio (hw:1,0)", silence_ms=1000)
    pcm = audio.record_utterance(s)
    assert opened[-1] == (1, 48000, 2), opened
    # The word starts at block 20; three blocks of pre-roll; the turn ends 1 s after it.
    seconds = len(pcm) / 2 / 16000
    assert abs(seconds - (5 + 3 + 10) * 0.1) < 0.051, seconds
    assert audio.rms_of(pcm[: 3 * 3200]) < 0.01 < audio.rms_of(pcm[3 * 3200: 4 * 3200])

    result = audio.mic_test("Gone")   # unplugged: falls back to the default, never raises
    assert result["ok"] and result["rate"] == 16000 and opened[-1] == (None, 16000, 1), (result, opened[-1])
    global SCRIPT
    BUSY.add(1)   # there, but held by another program: the default takes over, never a crash loop
    assert audio.mic_test("USB Mic: Audio (hw:2,0)")["ok"] and opened[-1] == (None, 16000, 1)
    BUSY.clear()
    SCRIPT = [0.005] * 20 + [0.3] + [0.005] * 15   # a click after Lura answered
    assert audio.record_utterance(s) == b"", "a click is not a turn"
    listing = audio.list_devices()
    assert [d["name"] for d in listing["inputs"]] == ["pulse", "USB Mic: Audio (hw:2,0)"]
    assert [d["name"] for d in listing["outputs"]] == ["pulse"] and listing["inputs"][0]["is_default"]
    print("audio OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
