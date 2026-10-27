"""A tiny in-process event bus.

The dashboard and the overlay are presentation; the assistant is the
application. They must not import each other, so "talk now" or "settings
changed" travels as an event instead of a function call.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any, Callable

log = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], None]

#: Event names, so a typo is an AttributeError rather than silence.
SETTINGS_CHANGED = "settings.changed"
KEYS_CHANGED = "keys.changed"
CONNECTORS_CHANGED = "connectors.changed"
PLAN_CHANGED = "plan.changed"
TALK_REQUESTED = "assistant.talk"
STOP_REQUESTED = "assistant.stop"
STATUS = "assistant.status"


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, name: str, handler: Handler) -> None:
        with self._lock:
            self._handlers[name].append(handler)

    def publish(self, name: str, **payload: Any) -> None:
        with self._lock:
            handlers = list(self._handlers.get(name, ()))
        for handler in handlers:
            try:
                handler(payload)
            except Exception:  # one bad subscriber must not break the others
                log.exception("Event handler for %s failed", name)
