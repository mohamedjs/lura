"""OpenAI's chat-completions wire format, which three of our providers speak.

* OpenAI itself.
* OpenRouter, for every model it hosts.
* Gemini, through Google's OpenAI-compatible endpoint — which takes tools,
  streams, and accepts ``input_audio``, so Gemini can hear the recording.

Streaming is the point: the assistant starts speaking the first sentence
while the rest is still being written, instead of waiting for the whole
answer and then for the whole synthesis.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, Iterator

from ....shared.http import HttpError, request_json, stream_sse
from ..domain import Done, Message, StreamEvent, TextDelta, ToolCall, ToolSpec, missing_required

log = logging.getLogger(__name__)

BASES = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
}

#: OpenRouter asks callers to identify themselves; it is also what makes the
#: app appear by name in the user's usage dashboard.
OPENROUTER_HEADERS = {"HTTP-Referer": "https://github.com/mohamedjs/lura", "X-Title": "Lura"}

TIMEOUT = 120


class OpenAICompatChat:
    def __init__(self, provider: str, key: str, model: str, *, reasoning_effort: str = "",
                 provider_order: list[str] | None = None):
        self.provider = provider
        self.model = model
        self._key = key
        self._effort = reasoning_effort
        self._order = list(provider_order or [])

    # ── request ──
    def _headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._key}"}
        if self.provider == "openrouter":
            headers.update(OPENROUTER_HEADERS)
        return headers

    def _payload(self, system: str, history: list[Message], tools: list[ToolSpec]) -> dict:
        payload: dict[str, Any] = {
            "model": self.model,
            # The system message and the tools are the cached prefix: they
            # must be byte-identical turn to turn. Nothing time-varying there.
            "messages": [{"role": "system", "content": system}, *map(self._wire, history)],
            "stream": True,
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools]
        if self.provider == "openrouter":
            payload["usage"] = {"include": True}
            if self._effort:
                payload["reasoning"] = {"effort": self._effort}
            if self._order:
                payload["provider"] = {"order": self._order, "allow_fallbacks": True}
        elif self._effort:
            # Gemini's compatible endpoint has no "minimal"; "low" is its floor.
            effort = "low" if self.provider == "gemini" and self._effort == "minimal" else self._effort
            payload["reasoning_effort"] = effort
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _wire(self, message: Message) -> dict[str, Any]:
        if message.role == "assistant":
            if message.native and message.native[0] == self.provider:
                return message.native[1]
            out: dict[str, Any] = {"role": "assistant", "content": message.text or None}
            if message.tool_calls:
                out["tool_calls"] = [{"id": c.id, "type": "function", "function": {
                    "name": c.name, "arguments": c.raw_arguments or json.dumps(c.arguments)}}
                    for c in message.tool_calls]
            return out
        if message.role == "tool":
            # tool_call_id must come back exactly, or the next call is a 400.
            return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.text}
        if message.audio_wav:
            parts: list[dict[str, Any]] = [{"type": "input_audio", "input_audio": {
                "data": base64.b64encode(message.audio_wav).decode(), "format": "wav"}}]
            if message.text:
                parts.append({"type": "text", "text": message.text})
            return {"role": "user", "content": parts}
        return {"role": "user", "content": message.text}

    # ── response ──
    def stream(self, system: str, history: list[Message],
               tools: list[ToolSpec]) -> Iterator[StreamEvent]:
        url = f"{BASES[self.provider]}/chat/completions"
        text: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        extras: dict[str, Any] = {}
        finish = ""
        usage: dict[str, Any] = {}

        for _event, data in stream_sse(url, self._headers(), self._payload(system, history, tools),
                                       timeout=TIMEOUT):
            if data.strip() == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if "error" in chunk:
                # OpenRouter reports a mid-stream failure as a data event.
                err = chunk["error"]
                raise HttpError(f"{self.provider}: {err.get('message', err) if isinstance(err, dict) else err}",
                                err.get("code") if isinstance(err, dict) and isinstance(err.get("code"), int) else None)
            usage = chunk.get("usage") or usage
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if piece := delta.get("content"):
                    text.append(piece)
                    yield TextDelta(piece)
                for call in delta.get("tool_calls") or []:
                    _merge_call(calls.setdefault(call.get("index", len(calls)), {}), call)
                # Reasoning details (OpenRouter) and thought signatures
                # (Gemini) must go back with the turn, or tools 400.
                if details := delta.get("reasoning_details"):
                    extras.setdefault("reasoning_details", []).extend(details)
                finish = choice.get("finish_reason") or finish

        if usage:
            log.info("%s: %s prompt tokens (%s cached), %s out%s", self.provider,
                     usage.get("prompt_tokens", 0),
                     (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
                     usage.get("completion_tokens", 0),
                     f", ${usage['cost']:.5f}" if isinstance(usage.get("cost"), (int, float)) else "")

        native: dict[str, Any] = {"role": "assistant", "content": "".join(text) or None, **extras}
        tool_calls: list[ToolCall] = []
        if calls:
            native["tool_calls"] = [calls[i] for i in sorted(calls)]
            for wire in native["tool_calls"]:
                wire.setdefault("type", "function")
                fn = wire.get("function") or {}
                raw = fn.get("arguments") or ""
                try:
                    args = json.loads(raw or "{}")
                    valid = isinstance(args, dict)
                except json.JSONDecodeError:
                    args, valid = {}, False
                spec = next((t for t in tools if t.name == fn.get("name")), None)
                if valid and missing_required(spec, args):
                    valid = False
                tool_calls.append(ToolCall(wire.get("id", ""), fn.get("name", ""),
                                           args if isinstance(args, dict) else {}, valid, raw))

        stop = {"tool_calls": "tool_use", "length": "max_tokens",
                "content_filter": "refusal"}.get(finish, "end")
        yield Done(Message("assistant", "".join(text).strip(), tool_calls=tool_calls,
                           native=(self.provider, native)), stop)


def _merge_call(target: dict[str, Any], delta: dict[str, Any]) -> None:
    """Fold one streamed tool-call fragment into the call being assembled."""
    for key, value in delta.items():
        if key == "index":
            continue
        if key == "function":
            fn = target.setdefault("function", {})
            if name := value.get("name"):
                # Sent once by OpenAI; some upstreams repeat it per fragment.
                have = fn.get("name", "")
                fn["name"] = name if not have or name.startswith(have) else have + name
            if value.get("arguments"):
                fn["arguments"] = fn.get("arguments", "") + value["arguments"]
        elif value not in (None, ""):
            target[key] = value


def list_model_records(provider: str, key: str | None) -> list[dict]:
    """The provider's model listing, as it returns it. OpenRouter's needs no key."""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return request_json(f"{BASES[provider]}/models", headers, timeout=30).get("data", [])


def list_models(provider: str, key: str | None) -> list[str]:
    ids = [m.get("id", "") for m in list_model_records(provider, key) if m.get("id")]
    # Gemini's compatible endpoint prefixes ids with "models/".
    return sorted(i.removeprefix("models/") for i in ids)


def check_key(provider: str, key: str) -> str:
    """A short human answer to "does this key work?". Raises HttpError if not."""
    if provider == "openrouter":
        info = request_json(f"{BASES['openrouter']}/key", {"Authorization": f"Bearer {key}"},
                            timeout=20).get("data", {})
        limit, usage = info.get("limit"), info.get("usage")
        if limit is not None and usage is not None and usage >= limit:
            raise HttpError("This OpenRouter key is out of credit.", 402)
        return f"OK — {info.get('label') or 'key'} (used ${usage or 0:.2f}{f' of ${limit:.2f}' if limit else ''})"
    return f"OK — {len(list_models(provider, key))} models available"
