"""Keep a window on every workspace / Space, so the face follows the user.

Qt has no API for it, so each platform's own switch is flipped through
ctypes (no extra dependency):

* Linux X11 (GNOME, KDE, …): EWMH ``_NET_WM_DESKTOP = 0xFFFFFFFF`` plus a
  ``_NET_WM_STATE_STICKY`` request to the window manager.
* macOS: ``NSWindowCollectionBehaviorCanJoinAllSpaces``.

ponytail: Wayland gives clients no such control and Windows virtual desktops
have no public API; there the face stays on the desktop it opened on.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os

from ...shared.platform import IS_LINUX, IS_MAC

log = logging.getLogger(__name__)

ALL_DESKTOPS = 0xFFFFFFFF
_ADD = 1                       # _NET_WM_STATE_ADD
_CLIENT_MESSAGE = 33
_REDIRECT = (1 << 20) | (1 << 19)   # SubstructureRedirect | SubstructureNotify


class _ClientMessage(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p), ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
                ("format", ctypes.c_int), ("l", ctypes.c_long * 5)]


class _Event(ctypes.Union):
    _fields_ = [("xclient", _ClientMessage), ("pad", ctypes.c_long * 24)]


def _x11(window: int) -> None:
    x = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11") or "libX11.so.6")
    x.XOpenDisplay.restype = ctypes.c_void_p
    x.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x.XInternAtom.restype = ctypes.c_ulong
    x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x.XDefaultRootWindow.restype = ctypes.c_ulong
    x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x.XChangeProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    x.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.c_void_p]
    for name in ("XFlush", "XCloseDisplay"):
        getattr(x, name).argtypes = [ctypes.c_void_p]
    display = x.XOpenDisplay(None)
    if not display:
        return
    try:
        atom = lambda name: x.XInternAtom(display, name, 0)   # noqa: E731
        desktop = atom(b"_NET_WM_DESKTOP")
        x.XChangeProperty(display, window, desktop, atom(b"CARDINAL"), 32, 0,
                          ctypes.byref((ctypes.c_ulong * 1)(ALL_DESKTOPS)), 1)
        root = x.XDefaultRootWindow(display)
        # The property covers a window not yet mapped; a mapped one is moved
        # by asking the window manager, as EWMH says.
        for message_type, data in ((desktop, (ALL_DESKTOPS, 1, 0, 0, 0)),
                                   (atom(b"_NET_WM_STATE"), (_ADD, atom(b"_NET_WM_STATE_STICKY"), 0, 1, 0))):
            event = _Event()
            event.xclient = _ClientMessage(_CLIENT_MESSAGE, 0, 1, display, window, message_type, 32,
                                           (ctypes.c_long * 5)(*[ctypes.c_long(v & 0xFFFFFFFF).value
                                                                 for v in data]))
            x.XSendEvent(display, root, 0, _REDIRECT, ctypes.byref(event))
        x.XFlush(display)
    finally:
        x.XCloseDisplay(display)


def _mac(view: int) -> None:
    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    get = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
        ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value)
    put = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)(
        ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value)
    window = get(view, objc.sel_registerName(b"window"))
    if window:
        # CanJoinAllSpaces | Stationary | FullScreenAuxiliary
        put(window, objc.sel_registerName(b"setCollectionBehavior:"), (1 << 0) | (1 << 4) | (1 << 8))


def on_every_workspace(win_id: int) -> None:
    """Best effort: a failure leaves the face where it is, never crashes."""
    try:
        if IS_MAC:
            _mac(win_id)
        elif IS_LINUX and os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            _x11(win_id)
    except Exception as exc:
        log.debug("Could not put the face on every workspace: %s", exc)
