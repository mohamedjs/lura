"""Lura — a voice-first desktop assistant.

The package is a modular monolith. Each folder under ``lura.modules`` is one
bounded context with its own domain, application and infrastructure layers,
and other modules reach it only through what its ``__init__`` exports. The
wiring happens in one place, :mod:`lura.bootstrap`.
"""

__version__ = "2.1.1"
