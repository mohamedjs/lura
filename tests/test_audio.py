"""The microphone path against a fake sounddevice: a USB mic that only does 48 kHz stereo."""

import _setup  # noqa: F401
import sys
import threading
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


def main() -> int:
    install_fake()
    from lura.modules.settings import Settings
    from lura.modules.voice.infrastructure import audio

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
