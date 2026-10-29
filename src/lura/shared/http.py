"""JSON and server-sent-event requests over the standard library.

No HTTP dependency: every provider here speaks plain JSON over HTTPS, and the
streaming ones speak SSE, which is a dozen lines of line splitting.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

from .errors import LuraError


def use_bundled_certificates() -> None:
    """Point every HTTPS client at certifi's CA bundle when the OS one is out of reach.

    A frozen build's Python looks for root certificates where the build
    machine kept them (on macOS, inside the runner's toolcache), so on a
    user's Mac every urllib request failed with CERTIFICATE_VERIFY_FAILED:
    Gemini and OpenRouter keys "would not save". SSL_CERT_FILE is read by
    every default SSL context, urllib's included. An explicit setting wins.
    """
    import os
    import ssl

    if os.environ.get("SSL_CERT_FILE"):
        return
    found = ssl.get_default_verify_paths()
    if (found.cafile and os.path.exists(found.cafile)) or (found.capath and os.path.isdir(found.capath)
                                                          and os.listdir(found.capath)):
        return   # the OS bundle is there (keeps corporate CAs working)
    try:
        import certifi
    except ImportError:
        return
    os.environ["SSL_CERT_FILE"] = certifi.where()

USER_AGENT = "lura-desktop"


class HttpError(LuraError):
    """An API error. ``status`` is the HTTP code, or None for network trouble.

    The message carries the server's own words: "model not found" and "out of
    credit" need different fixes, and only the server knows which this is.
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def retryable(self) -> bool:
        """Would another model or a second try plausibly succeed?"""
        return self.status in (None, 402, 408, 409, 429, 500, 502, 503, 504, 529)


def _build(url: str, headers: dict[str, str] | None, payload: Any, form: dict | None):
    all_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        all_headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        all_headers["Content-Type"] = "application/x-www-form-urlencoded"
    return urllib.request.Request(url, data=data, headers=all_headers)


def _open(request, timeout: float):
    label = urllib.parse.urlsplit(request.full_url).path
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:600]
        raise HttpError(f"HTTP {exc.code} from {label}: {detail}", exc.code) from None
    except TimeoutError:
        raise HttpError(f"{label} timed out after {timeout:.0f}s", 408) from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise HttpError(f"{label} timed out after {timeout:.0f}s", 408) from None
        raise HttpError(f"Cannot reach {urllib.parse.urlsplit(request.full_url).netloc}: {exc.reason}") from None


def request_json(url: str, headers: dict[str, str] | None = None, payload: Any = None,
                 timeout: float = 60, form: dict | None = None) -> Any:
    with _open(_build(url, headers, payload, form), timeout) as response:
        body = response.read()
    return json.loads(body) if body else {}


def request_bytes(url: str, headers: dict[str, str] | None = None, payload: Any = None,
                  timeout: float = 60) -> tuple[bytes, str]:
    """Raw body and its content type — for speech, which is not JSON."""
    with _open(_build(url, headers, payload, None), timeout) as response:
        return response.read(), response.headers.get("Content-Type", "")


def stream_sse(url: str, headers: dict[str, str] | None = None, payload: Any = None,
               timeout: float = 120) -> Iterator[tuple[str, str]]:
    """Yield ``(event, data)`` for each server-sent event.

    Comment lines (``: keep-alive``, OpenRouter's ``: OPENROUTER PROCESSING``)
    are skipped; multi-line data is joined the way the spec says.
    """
    request = _build(url, {"Accept": "text/event-stream", **(headers or {})}, payload, None)
    with _open(request, timeout) as response:
        event, data = "message", []
        for raw in response:
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                if data:
                    yield event, "\n".join(data)
                event, data = "message", []
                continue
            if line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "event":
                event = value
            elif field == "data":
                data.append(value)
        if data:
            yield event, "\n".join(data)
