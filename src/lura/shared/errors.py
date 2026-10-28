"""Errors that cross module boundaries."""

from __future__ import annotations


class LuraError(RuntimeError):
    """Base for errors worth showing the user verbatim."""


class NeedsSetup(LuraError):
    """A human has to fix something (a key, a model download). Retrying won't.

    The daemon exits 78 (EX_CONFIG) on this, and the service units refuse to
    restart on 78, so a fresh install waits quietly instead of looping.
    """



