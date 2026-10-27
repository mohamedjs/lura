"""Claude, through the official Anthropic SDK.

Claude cannot hear: the assistant hands it a transcript (see the voice
module), never audio. Its own turns are replayed exactly as the API returned
them — thinking blocks included — because stripping or editing them breaks
the next request.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterator

from ....shared.errors import NeedsSetup
from ....shared.http import HttpError
from ..domain import Done, Message, StreamEvent, TextDelta, ToolCall, ToolSpec, missing_required

log = logging.getLogger(__name__)

#: Long enough for any spoken answer plus a tool call or two; streaming
#: means a large ceiling costs nothing until it is used.
MAX_TOKENS = 16000

#: Models that take ``output_config.effort`` (thinking is adaptive on all of them).
_EFFORT_MODELS = re.compile(r"claude-(opus|sonnet|haiku|fable|mythos)-5|claude-opus-4-[5-8]")
#: Models that take the server-side refusal fallback in its "default" form.
_FALLBACK_MODELS = re.compile(r"claude-(opus-5-5|opus-5|fable-5-1|sonnet-5-5)$")
FALLBACK_BETA = "server-side-fallback-2026-07-01"

#: Voice wants speed. "minimal" is not an Anthropic effort level; "low" is the floor.
_EFFORT = {"minimal": "low", "low": "low", "medium": "medium", "high": "high"}


class AnthropicChat:
    provider = "anthropic"

    def __init__(self, key: str, model: str, *, reasoning_effort: str = "low"):
        try:
            import anthropic
        except ImportError:
            raise NeedsSetup("Claude needs the Anthropic SDK: pip install 'lura[anthropic]'") from None
        self._sdk = anthropic
        self._client = anthropic.Anthropic(api_key=key, max_retries=2)
        self.model = model
        self._effort = _EFFORT.get(reasoning_effort, "")

    # ── request ──
    def _messages(self, history: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for message in history:
            if message.role == "tool":
                block = {"type": "tool_result", "tool_use_id": message.tool_call_id,
                         "content": message.text, **({"is_error": True} if message.is_error else {})}
                # Every result of one assistant turn goes in ONE user message;
                # splitting them teaches Claude to stop calling tools in parallel.
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                        and all(b.get("type") == "tool_result" for b in out[-1]["content"]):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            elif message.role == "assistant":
                if message.native and message.native[0] == self.provider:
                    out.append({"role": "assistant", "content": message.native[1]})
                    continue
                blocks: list[dict[str, Any]] = []
                if message.text:
                    blocks.append({"type": "text", "text": message.text})
                blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                           for c in message.tool_calls]
                out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "…"}]})
            else:
                if message.audio_wav and not message.text:
                    raise ValueError("Claude cannot hear audio; transcribe the turn first.")
                out.append({"role": "user", "content": message.text})
        return out

    def _kwargs(self, system: str, history: list[Message], tools: list[ToolSpec]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model, "max_tokens": MAX_TOKENS,
            "system": system, "messages": self._messages(history),
        }
        if tools:
            # eager_input_streaming is left off on purpose: these inputs are a
            # few words, so buffering costs nothing, and it keeps them validated.
            kwargs["tools"] = [{"name": t.name, "description": t.description,
                                "input_schema": t.parameters} for t in tools]
        if self._effort and _EFFORT_MODELS.search(self.model):
            kwargs["output_config"] = {"effort": self._effort}
        if _FALLBACK_MODELS.search(self.model):
            # A refused request is re-run on a fallback model in the same call
            # instead of ending the turn in silence.
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    # ── response ──
    def stream(self, system: str, history: list[Message],
               tools: list[ToolSpec]) -> Iterator[StreamEvent]:
        sdk = self._sdk
        kwargs = self._kwargs(system, history, tools)
        api = self._client.beta.messages if "betas" in kwargs else self._client.messages
        try:
            with api.stream(**kwargs) as stream:
                for piece in stream.text_stream:
                    yield TextDelta(piece)
                final = stream.get_final_message()
        except sdk.APIStatusError as exc:
            raise HttpError(f"anthropic: HTTP {exc.status_code}: {exc.message}", exc.status_code) from None
        except sdk.APIConnectionError as exc:
            raise HttpError(f"Cannot reach Anthropic: {exc}") from None

        blocks = [b.model_dump(exclude_none=True) for b in final.content]
        stop = final.stop_reason or ""
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        usage = final.usage
        log.info("anthropic: %s in (%s cached), %s out, stop=%s", usage.input_tokens,
                 getattr(usage, "cache_read_input_tokens", 0), usage.output_tokens, stop)

        calls: list[ToolCall] = []
        # A refusal or a cut-off can leave a half-written tool call; never run it.
        runnable = stop not in ("refusal", "max_tokens")
        for block in _after_last_fallback(blocks):
            if block.get("type") != "tool_use":
                continue
            args = block.get("input")
            valid = runnable and isinstance(args, dict)
            spec = next((t for t in tools if t.name == block.get("name")), None)
            if valid and missing_required(spec, args):
                valid = False
            calls.append(ToolCall(block["id"], block.get("name", ""),
                                  args if isinstance(args, dict) else {}, valid))

        mapped = {"tool_use": "tool_use", "max_tokens": "max_tokens", "refusal": "refusal"}.get(stop, "end")
        yield Done(Message("assistant", text, tool_calls=calls,
                           native=(self.provider, _echoable(blocks))), mapped)


def _after_last_fallback(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Blocks produced by the model that finished the turn."""
    last = max((i for i, b in enumerate(blocks) if b.get("type") == "fallback"), default=-1)
    return blocks[last + 1:]


def _echoable(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The turn as it may be sent back.

    After a mid-output fallback, the declined model's thinking and tool_use
    blocks before the boundary are dropped; text and everything after the
    boundary are echoed unchanged.
    """
    last = max((i for i, b in enumerate(blocks) if b.get("type") == "fallback"), default=-1)
    if last < 0:
        return blocks
    head = [b for b in blocks[:last] if b.get("type") == "text"]
    return head + blocks[last:]


def list_models(key: str) -> list[str]:
    from ....shared.http import request_json

    data = request_json("https://api.anthropic.com/v1/models?limit=100",
                        {"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout=20)
    return sorted(m["id"] for m in data.get("data", []) if m.get("id"))
