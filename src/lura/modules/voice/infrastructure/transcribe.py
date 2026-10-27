"""Hearing for brains that cannot: Gemini 3.8 Flash writes down what was said.

Not the local Vosk model — that one is English-only, and would turn Arabic
into confident nonsense that the brain then answers.
"""

from __future__ import annotations

import base64
import logging
import time

from ....shared.http import request_json

log = logging.getLogger(__name__)

BASES = {"gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
         "openrouter": "https://openrouter.ai/api/v1"}

PROMPT = ("Transcribe this recording exactly as spoken, in the language it was spoken "
          "(Egyptian Arabic stays in Arabic script). Output only the transcript. If nothing "
          "intelligible was said, output nothing.")


class ChatTranscriber:
    def __init__(self, provider: str, key: str, model: str):
        self._provider, self._key, self._model = provider, key, model

    def transcribe(self, wav: bytes) -> str:
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "input_audio", "input_audio": {"data": base64.b64encode(wav).decode(),
                                                        "format": "wav"}},
            ]}],
        }
        if self._provider == "openrouter":
            payload["reasoning"] = {"effort": "minimal"}
        else:
            payload["reasoning_effort"] = "low"
        started = time.monotonic()
        data = request_json(f"{BASES[self._provider]}/chat/completions",
                            {"Authorization": f"Bearer {self._key}"}, payload, timeout=60)
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        log.info("heard (%.1fs): %s", time.monotonic() - started, text.strip())
        return text.strip()
