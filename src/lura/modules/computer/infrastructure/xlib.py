"""EWMH ClientMessages over libX11 via ctypes, with no python-xlib dependency."""

from __future__ import annotations

import ctypes
import ctypes.util
import logging

log = logging.getLogger(__name__)

_CLIENT_MESSAGE = 33
_REDIRECT = (1 << 19) | (1 << 20)   # SubstructureNotify | SubstructureRedirect

_xlib: ctypes.CDLL | None = None


class _ClientMessage(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("message_type", ctypes.c_ulong),
        ("format", ctypes.c_int),
        ("l", ctypes.c_long * 5),
    ]


class _Event(ctypes.Union):
    _fields_ = [("xclient", _ClientMessage), ("pad", ctypes.c_long * 24)]


def _load() -> ctypes.CDLL:
    global _xlib
    if _xlib is None:
        lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11") or "libX11.so.6")
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XDefaultRootWindow.restype = ctypes.c_ulong
        lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        lib.XInternAtom.restype = ctypes.c_ulong
        lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.XSendEvent.restype = ctypes.c_int
        lib.XSendEvent.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.c_void_p,
        ]
        lib.XFlush.argtypes = [ctypes.c_void_p]
        lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        _xlib = lib
    return _xlib


class X11Display:
    """Context manager around one X11 Display connection."""

    def __init__(self) -> None:
        self._lib: ctypes.CDLL | None = None
        self._display: int | None = None
        self.root: int = 0

    def __enter__(self) -> X11Display:
        lib = _load()
        display = lib.XOpenDisplay(None)
        if not display:
            raise RuntimeError("Cannot open the X display.")
        self._lib = lib
        self._display = display
        self.root = int(lib.XDefaultRootWindow(display))
        return self

    def __exit__(self, *exc: object) -> None:
        if self._lib is not None and self._display:
            self._lib.XCloseDisplay(self._display)
            self._display = None

    def atom(self, name: str) -> int:
        assert self._lib is not None and self._display
        return int(self._lib.XInternAtom(self._display, name.encode("ascii"), 0))

    def send(self, window: int, message: str, *data: int) -> None:
        assert self._lib is not None and self._display
        vals = (list(data) + [0, 0, 0, 0, 0])[:5]
        event = _Event()
        event.xclient = _ClientMessage(
            _CLIENT_MESSAGE,
            0,
            1,
            self._display,
            window,
            self.atom(message),
            32,
            (ctypes.c_long * 5)(*[ctypes.c_long(v & 0xFFFFFFFF).value for v in vals]),
        )
        self._lib.XSendEvent(self._display, self.root, 0, _REDIRECT, ctypes.byref(event))
        self._lib.XFlush(self._display)


def available() -> bool:
    try:
        with X11Display():
            return True
    except Exception:
        return False


def activate(wid: int) -> None:
    with X11Display() as d:
        d.send(wid, "_NET_ACTIVE_WINDOW", 2, 0, 0)


def set_maximized(wid: int, on: bool) -> None:
    with X11Display() as d:
        d.send(
            wid,
            "_NET_WM_STATE",
            1 if on else 0,
            d.atom("_NET_WM_STATE_MAXIMIZED_VERT"),
            d.atom("_NET_WM_STATE_MAXIMIZED_HORZ"),
            2,
        )


def close(wid: int) -> None:
    with X11Display() as d:
        d.send(wid, "_NET_CLOSE_WINDOW", 0, 2)


def move_resize(wid: int, x: int, y: int, w: int, h: int) -> None:
    # NorthWest gravity (1) treats x,y as the outer frame's top-left; flags set x,y,w,h; source=pager (2).
    flags = 1 | (0xF << 8) | (2 << 12)
    with X11Display() as d:
        d.send(wid, "_NET_MOVERESIZE_WINDOW", flags, x, y, w, h)


def showing_desktop(on: bool) -> None:
    with X11Display() as d:
        d.send(d.root, "_NET_SHOWING_DESKTOP", 1 if on else 0)
