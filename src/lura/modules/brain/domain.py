"""The brain: a model that reads the conversation and answers or calls tools.

Provider-neutral shapes. Each adapter turns these into its own wire format
and back. ``Message.native`` carries the provider's own copy of an assistant
turn, because some of it must be sent back byte-for-byte: Claude's thinking
blocks, Gemini's thought signatures, OpenRouter's reasoning details. A turn
replayed to a *different* provider falls back to the neutral fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol, Union


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    #: False when the model's arguments were not valid JSON or broke the
    #: schema. Such a call is answered with an error, never run.
    valid: bool = True
    raw_arguments: str = ""


@dataclass
class Message:
    role: str                        # "user" | "assistant" | "tool"
    text: str = ""
    audio_wav: bytes | None = None   # a user turn the brain hears directly
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""           # on "tool": which call this answers
    tool_name: str = ""
    is_error: bool = False
    native: tuple[str, Any] | None = None   # (provider, provider-shaped turn)


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class Done:
    message: Message
    stop_reason: str = ""            # "end", "tool_use", "max_tokens", "refusal"


StreamEvent = Union[TextDelta, Done]


class ChatModel(Protocol):
    """One provider + model. ``stream`` yields text as it is generated and
    ends with exactly one :class:`Done` carrying the whole assistant turn."""

    provider: str
    model: str

    def stream(self, system: str, history: list[Message],
               tools: list[ToolSpec]) -> Iterator[StreamEvent]: ...


def missing_required(spec: ToolSpec | None, arguments: dict[str, Any]) -> list[str]:
    """Required parameters the call left out — the cheap half of validation."""
    if spec is None:
        return []
    required = spec.parameters.get("required") or []
    return [name for name in required if name not in arguments]
