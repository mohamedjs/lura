"""Screen pixel capture and encoding for look_at_screen (X11 and macOS)."""

from __future__ import annotations

import ctypes
import os
import struct
import tempfile
import zlib

import numpy as np

from ..domain import ActionError, Rect
from .runner import run
from .xlib import _load


class _Funcs(ctypes.Structure):
    _fields_ = [(n, ctypes.c_void_p) for n in
                ("create_image", "destroy_image", "get_pixel", "put_pixel", "sub_image", "add_pixel")]


class _XImage(ctypes.Structure):
    _fields_ = [("width", ctypes.c_int), ("height", ctypes.c_int), ("xoffset", ctypes.c_int),
                ("format", ctypes.c_int), ("data", ctypes.c_void_p), ("byte_order", ctypes.c_int),
                ("bitmap_unit", ctypes.c_int), ("bitmap_bit_order", ctypes.c_int),
                ("bitmap_pad", ctypes.c_int), ("depth", ctypes.c_int),
                ("bytes_per_line", ctypes.c_int), ("bits_per_pixel", ctypes.c_int),
                ("red_mask", ctypes.c_ulong), ("green_mask", ctypes.c_ulong),
                ("blue_mask", ctypes.c_ulong), ("obdata", ctypes.c_void_p), ("f", _Funcs)]


_ERR_HANDLER_TYPE = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
_DESTROY_TYPE = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)
_x_error = False
_x_configured = False


def _on_x_error(_dpy: int, _err: int) -> int:
    global _x_error
    _x_error = True
    return 0


# Kept in a module global so ctypes does not garbage-collect the callback.
_err_handler = _ERR_HANDLER_TYPE(_on_x_error)


def _setup_xlib(lib: ctypes.CDLL) -> None:
    global _x_configured
    if _x_configured:
        return
    lib.XGetImage.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
        ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int,
    ]
    lib.XGetImage.restype = ctypes.POINTER(_XImage)
    lib.XDefaultScreen.argtypes = [ctypes.c_void_p]
    lib.XDefaultScreen.restype = ctypes.c_int
    lib.XDisplayWidth.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.XDisplayWidth.restype = ctypes.c_int
    lib.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.XDisplayHeight.restype = ctypes.c_int
    lib.XSetErrorHandler.argtypes = [_ERR_HANDLER_TYPE]
    lib.XSetErrorHandler.restype = ctypes.c_void_p
    lib.XSetErrorHandler(_err_handler)
    _x_configured = True


def grab_x11(rect: Rect) -> np.ndarray:
    """Capture ``rect`` from the X11 root window as an (h, w, 3) uint8 RGB array."""
    global _x_error
    lib = _load()
    _setup_xlib(lib)
    dpy = lib.XOpenDisplay(None)
    if not dpy:
        raise ActionError("Could not read the screen.")
    try:
        root = lib.XDefaultRootWindow(dpy)
        scr = lib.XDefaultScreen(dpy)
        screen_rect = Rect(0, 0, int(lib.XDisplayWidth(dpy, scr)), int(lib.XDisplayHeight(dpy, scr)))
        clipped = rect.intersect(screen_rect)
        if clipped is None:
            raise ActionError("The window is off screen.")
        x, y, w, h = clipped.x, clipped.y, clipped.width, clipped.height
        all_planes = 0xFFFFFFFF_FFFFFFFF & ctypes.c_ulong(-1).value
        _x_error = False
        img = lib.XGetImage(dpy, root, x, y, w, h, all_planes, 2)  # ZPixmap = 2
        if not img or _x_error:
            if img:
                _DESTROY_TYPE(img.contents.f.destroy_image)(img)
            raise ActionError("Could not read the screen.")
        try:
            xi = img.contents
            if xi.bits_per_pixel != 32:
                raise ActionError("Unsupported screen format.")
            bpl = xi.bytes_per_line
            buf = ctypes.string_at(xi.data, bpl * h)
            arr = np.frombuffer(buf, np.uint8).reshape(h, bpl)[:, : w * 4].reshape(h, w, 4)
            return arr[..., 1:4].copy() if xi.byte_order == 1 else arr[..., 2::-1].copy()
        finally:
            _DESTROY_TYPE(img.contents.f.destroy_image)(img)
    finally:
        lib.XCloseDisplay(dpy)


def downscale(rgb: np.ndarray, max_side: int) -> np.ndarray:
    """Box-average an RGB image down by an integer factor so its longest side is <= max_side."""
    h, w = rgb.shape[:2]
    longest = max(h, w)
    k = (longest + max_side - 1) // max_side
    if k <= 1:
        return rgb
    try:
        import cv2
    except ImportError:
        cv2 = None
    if cv2 is not None:
        # Exact size: an integer box factor halves a 1920 px screen to 960 px and blurs its text.
        f = max_side / longest
        return cv2.resize(rgb, (max(1, round(w * f)), max(1, round(h * f))), interpolation=cv2.INTER_AREA)
    h2, w2 = (h // k) * k, (w // k) * k
    return rgb[:h2, :w2].reshape(h2 // k, k, w2 // k, k, 3).mean(axis=(1, 3)).astype(np.uint8)


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def png(rgb: np.ndarray) -> bytes:
    """Encode an (h, w, 3) uint8 RGB array as an 8-bit RGB PNG using stdlib zlib."""
    arr = np.ascontiguousarray(rgb, dtype=np.uint8)
    h, w = arr.shape[:2]
    rows = np.concatenate([np.zeros((h, 1), dtype=np.uint8), arr.reshape(h, w * 3)], axis=1)
    ihdr = struct.pack("!IIBBBBB", w, h, 8, 2, 0, 0, 0)
    idat = zlib.compress(rows.tobytes(), 6)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


def encode(rgb: np.ndarray, quality: int) -> tuple[bytes, str]:
    """Encode an RGB array as JPEG via OpenCV when available, else PNG."""
    try:
        import cv2
    except ImportError:
        return png(rgb), "image/png"
    ok, buf = cv2.imencode(".jpg", rgb[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, quality])
    if ok:
        return buf.tobytes(), "image/jpeg"
    return png(rgb), "image/png"


def grab_mac(rect: Rect, max_side: int, quality: int) -> tuple[bytes, str]:
    """Capture ``rect`` on macOS using screencapture and sips."""
    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    try:
        res = run(
            [
                "/usr/sbin/screencapture", "-x", "-t", "jpg",
                f"-R{rect.x},{rect.y},{rect.width},{rect.height}",
                path,
            ],
            timeout=10.0,
        )
        if not res.ok:
            raise ActionError("Could not read the screen.")
        if max(rect.width, rect.height) > max_side:
            run(["/usr/bin/sips", "-Z", str(max_side), path], timeout=10.0)
        with open(path, "rb") as handle:
            data = handle.read()
        if not data:
            raise ActionError("Could not read the screen.")
        return data, "image/jpeg"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
