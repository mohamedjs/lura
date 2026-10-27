"""How the assistant shows what it is doing, without knowing who is watching.

The overlay face implements this; so does a no-op for headless runs. The
assistant and voice modules only ever see this protocol.
"""

from __future__ import annotations

from typing import Protocol

#: The states a face can show.
IDLE, LISTENING, THINKING, SPEAKING, CONNECTING = "idle", "listening", "thinking", "speaking", "connecting"


class Presence(Protocol):
    def set_state(self, state: str) -> None: ...
    def set_amplitude(self, level: float) -> None: ...
    def set_transcript(self, text: str) -> None: ...


class NullPresence:
    def set_state(self, state: str) -> None:
        pass

    def set_amplitude(self, level: float) -> None:
        pass

    def set_transcript(self, text: str) -> None:
        pass
