"""ctypes bindings for macOS CoreGraphics, CoreFoundation and ApplicationServices."""

from __future__ import annotations

import ctypes
import functools
import logging
from types import SimpleNamespace

from ....shared.platform import IS_MAC

log = logging.getLogger(__name__)

_CF_UTF8 = 0x08000100
_CF_NUMBER_SINT64 = 4
_CG_HID_EVENT_TAP = 0
_CG_MOUSE_CLICK_STATE = 1
_CG_SCROLL_UNIT_LINE = 1
_CG_WINDOW_LIST_OPTS = 1 | 16   # OnScreenOnly | ExcludeDesktopElements

_MOUSE_EVENTS: dict[int, dict[str, int]] = {
    0: {"down": 1, "up": 2, "move": 5, "drag": 6},
    1: {"down": 3, "up": 4, "move": 5, "drag": 7},
    2: {"down": 25, "up": 26, "move": 5, "drag": 27},
}


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class CGRect(ctypes.Structure):
    _fields_ = [("origin", CGPoint), ("size", CGSize)]


@functools.lru_cache(maxsize=1)
def _lib() -> SimpleNamespace:
    if not IS_MAC:
        raise OSError("CoreGraphics is only available on macOS")
    cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    ax = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
    )

    c_vp = ctypes.c_void_p
    c_u32, c_i32, c_i64, c_u64 = ctypes.c_uint32, ctypes.c_int32, ctypes.c_int64, ctypes.c_uint64
    c_u16, c_bool, c_long, c_cp = ctypes.c_uint16, ctypes.c_bool, ctypes.c_long, ctypes.c_char_p

    # CoreGraphics
    cg.CGEventCreate.argtypes = [c_vp]
    cg.CGEventCreate.restype = c_vp
    cg.CGEventGetLocation.argtypes = [c_vp]
    cg.CGEventGetLocation.restype = CGPoint
    cg.CGEventCreateMouseEvent.argtypes = [c_vp, c_u32, CGPoint, c_u32]
    cg.CGEventCreateMouseEvent.restype = c_vp
    cg.CGEventSetIntegerValueField.argtypes = [c_vp, c_u32, c_i64]
    cg.CGEventSetIntegerValueField.restype = None
    # Non-variadic scroll wheel event (ctypes breaks variadic args on Apple arm64).
    cg.CGEventCreateScrollWheelEvent2.argtypes = [c_vp, c_u32, c_u32, c_i32, c_i32, c_i32]
    cg.CGEventCreateScrollWheelEvent2.restype = c_vp
    cg.CGEventCreateKeyboardEvent.argtypes = [c_vp, c_u16, c_bool]
    cg.CGEventCreateKeyboardEvent.restype = c_vp
    cg.CGEventSetFlags.argtypes = [c_vp, c_u64]
    cg.CGEventSetFlags.restype = None
    cg.CGEventPost.argtypes = [c_u32, c_vp]
    cg.CGEventPost.restype = None
    cg.CGGetActiveDisplayList.argtypes = [c_u32, ctypes.POINTER(c_u32), ctypes.POINTER(c_u32)]
    cg.CGGetActiveDisplayList.restype = c_i32
    cg.CGMainDisplayID.argtypes = []
    cg.CGMainDisplayID.restype = c_u32
    cg.CGDisplayBounds.argtypes = [c_u32]
    cg.CGDisplayBounds.restype = CGRect
    cg.CGWindowListCopyWindowInfo.argtypes = [c_u32, c_u32]
    cg.CGWindowListCopyWindowInfo.restype = c_vp
    cg.CGRectMakeWithDictionaryRepresentation.argtypes = [c_vp, ctypes.POINTER(CGRect)]
    cg.CGRectMakeWithDictionaryRepresentation.restype = c_bool

    # CoreFoundation
    cf.CFRelease.argtypes = [c_vp]
    cf.CFRelease.restype = None
    cf.CFArrayGetCount.argtypes = [c_vp]
    cf.CFArrayGetCount.restype = c_long
    cf.CFArrayGetValueAtIndex.argtypes = [c_vp, c_long]
    cf.CFArrayGetValueAtIndex.restype = c_vp
    cf.CFDictionaryGetValue.argtypes = [c_vp, c_vp]
    cf.CFDictionaryGetValue.restype = c_vp
    cf.CFStringCreateWithCString.argtypes = [c_vp, c_cp, c_u32]
    cf.CFStringCreateWithCString.restype = c_vp
    cf.CFStringGetCString.argtypes = [c_vp, c_cp, c_long, c_u32]
    cf.CFStringGetCString.restype = c_bool
    cf.CFNumberGetValue.argtypes = [c_vp, c_long, c_vp]
    cf.CFNumberGetValue.restype = c_bool
    cf.CFBooleanGetValue.argtypes = [c_vp]
    cf.CFBooleanGetValue.restype = c_bool
    cf.CFDictionaryCreate.argtypes = [
        c_vp, ctypes.POINTER(c_vp), ctypes.POINTER(c_vp), c_long, c_vp, c_vp,
    ]
    cf.CFDictionaryCreate.restype = c_vp

    # ApplicationServices (Accessibility)
    ax.AXIsProcessTrusted.argtypes = []
    ax.AXIsProcessTrusted.restype = c_bool
    ax.AXIsProcessTrustedWithOptions.argtypes = [c_vp]
    ax.AXIsProcessTrustedWithOptions.restype = c_bool

    keys = {
        name: cf.CFStringCreateWithCString(None, name.encode("ascii"), _CF_UTF8)
        for name in (
            "kCGWindowNumber",
            "kCGWindowOwnerPID",
            "kCGWindowLayer",
            "kCGWindowOwnerName",
            "kCGWindowName",
            "kCGWindowIsOnscreen",
            "kCGWindowBounds",
        )
    }
    return SimpleNamespace(cg=cg, cf=cf, ax=ax, keys=keys)


def available() -> bool:
    try:
        _lib()
        return True
    except Exception:
        return False


def cursor() -> tuple[int, int]:
    lib = _lib()
    event = lib.cg.CGEventCreate(None)
    if not event:
        raise RuntimeError("Could not read cursor position.")
    try:
        point = lib.cg.CGEventGetLocation(event)
        return (round(point.x), round(point.y))
    finally:
        lib.cf.CFRelease(event)


def post_mouse(kind: str, button: int, x: int, y: int, click_state: int = 1) -> None:
    lib = _lib()
    ev_type = _MOUSE_EVENTS[button][kind]
    event = lib.cg.CGEventCreateMouseEvent(None, ev_type, CGPoint(float(x), float(y)), button)
    if not event:
        raise RuntimeError("Could not create mouse event.")
    try:
        if kind in ("down", "up"):
            lib.cg.CGEventSetIntegerValueField(event, _CG_MOUSE_CLICK_STATE, click_state)
        lib.cg.CGEventPost(_CG_HID_EVENT_TAP, event)
    finally:
        lib.cf.CFRelease(event)


def post_scroll(dy: int, dx: int) -> None:
    lib = _lib()
    event = lib.cg.CGEventCreateScrollWheelEvent2(None, _CG_SCROLL_UNIT_LINE, 2, dy, dx, 0)
    if not event:
        raise RuntimeError("Could not create scroll event.")
    try:
        lib.cg.CGEventPost(_CG_HID_EVENT_TAP, event)
    finally:
        lib.cf.CFRelease(event)


def post_key(keycode: int, down: bool, flags: int) -> None:
    lib = _lib()
    event = lib.cg.CGEventCreateKeyboardEvent(None, keycode, down)
    if not event:
        raise RuntimeError("Could not create keyboard event.")
    try:
        lib.cg.CGEventSetFlags(event, flags)
        lib.cg.CGEventPost(_CG_HID_EVENT_TAP, event)
    finally:
        lib.cf.CFRelease(event)


def displays() -> list[tuple[int, int, int, int, int, bool]]:
    lib = _lib()
    arr = (ctypes.c_uint32 * 16)()
    count = ctypes.c_uint32(0)
    if lib.cg.CGGetActiveDisplayList(16, arr, ctypes.byref(count)) != 0:
        return []
    main_id = lib.cg.CGMainDisplayID()
    out: list[tuple[int, int, int, int, int, bool]] = []
    for i in range(count.value):
        did = arr[i]
        rect = lib.cg.CGDisplayBounds(did)
        out.append((
            int(did),
            round(rect.origin.x),
            round(rect.origin.y),
            round(rect.size.width),
            round(rect.size.height),
            did == main_id,
        ))
    out.sort(key=lambda row: not row[5])
    return out


def _cf_int(lib: SimpleNamespace, d: int, key: str) -> int:
    val = lib.cf.CFDictionaryGetValue(d, lib.keys[key])
    if not val:
        return 0
    out = ctypes.c_int64(0)
    if lib.cf.CFNumberGetValue(val, _CF_NUMBER_SINT64, ctypes.byref(out)):
        return int(out.value)
    return 0


def _cf_str(lib: SimpleNamespace, d: int, key: str) -> str:
    val = lib.cf.CFDictionaryGetValue(d, lib.keys[key])
    if not val:
        return ""
    buf = ctypes.create_string_buffer(1024)
    if lib.cf.CFStringGetCString(val, buf, 1024, _CF_UTF8):
        return buf.value.decode("utf-8", errors="replace")
    return ""


def _cf_bool(lib: SimpleNamespace, d: int, key: str) -> bool:
    val = lib.cf.CFDictionaryGetValue(d, lib.keys[key])
    return bool(lib.cf.CFBooleanGetValue(val)) if val else False


def _cf_rect(lib: SimpleNamespace, d: int, key: str) -> tuple[int, int, int, int]:
    val = lib.cf.CFDictionaryGetValue(d, lib.keys[key])
    if not val:
        return (0, 0, 0, 0)
    rect = CGRect()
    if lib.cg.CGRectMakeWithDictionaryRepresentation(val, ctypes.byref(rect)):
        return (
            round(rect.origin.x),
            round(rect.origin.y),
            round(rect.size.width),
            round(rect.size.height),
        )
    return (0, 0, 0, 0)


def window_list() -> list[dict]:
    lib = _lib()
    arr = lib.cg.CGWindowListCopyWindowInfo(_CG_WINDOW_LIST_OPTS, 0)
    if not arr:
        return []
    out: list[dict] = []
    try:
        count = lib.cf.CFArrayGetCount(arr)
        for i in range(count):
            d = lib.cf.CFArrayGetValueAtIndex(arr, i)
            if not d:
                continue
            x, y, width, height = _cf_rect(lib, d, "kCGWindowBounds")
            out.append({
                "number": _cf_int(lib, d, "kCGWindowNumber"),
                "pid": _cf_int(lib, d, "kCGWindowOwnerPID"),
                "owner": _cf_str(lib, d, "kCGWindowOwnerName"),
                "name": _cf_str(lib, d, "kCGWindowName"),
                "layer": _cf_int(lib, d, "kCGWindowLayer"),
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "onscreen": _cf_bool(lib, d, "kCGWindowIsOnscreen"),
            })
    finally:
        lib.cf.CFRelease(arr)
    return out


_screen_capture_requested = False


def screen_capture_allowed(request: bool) -> bool:
    global _screen_capture_requested
    lib = _lib()
    preflight = getattr(lib.cg, "CGPreflightScreenCaptureAccess", None)
    if preflight is None:
        return True
    preflight.argtypes = []
    preflight.restype = ctypes.c_bool
    if bool(preflight()):
        return True
    if request and not _screen_capture_requested:
        _screen_capture_requested = True
        req = getattr(lib.cg, "CGRequestScreenCaptureAccess", None)
        if req is not None:
            req.argtypes = []
            req.restype = ctypes.c_bool
            req()
    return False


def ax_trusted(prompt: bool = False) -> bool:
    try:
        lib = _lib()
        if not prompt:
            return bool(lib.ax.AXIsProcessTrusted())
        key = ctypes.c_void_p.in_dll(lib.ax, "kAXTrustedCheckOptionPrompt")
        val = ctypes.c_void_p.in_dll(lib.cf, "kCFBooleanTrue")
        keys = (ctypes.c_void_p * 1)(key.value)
        vals = (ctypes.c_void_p * 1)(val.value)
        key_cb = ctypes.addressof(ctypes.c_byte.in_dll(lib.cf, "kCFTypeDictionaryKeyCallBacks"))
        val_cb = ctypes.addressof(ctypes.c_byte.in_dll(lib.cf, "kCFTypeDictionaryValueCallBacks"))
        opts = lib.cf.CFDictionaryCreate(None, keys, vals, 1, key_cb, val_cb)
        if not opts:
            return bool(lib.ax.AXIsProcessTrusted())
        try:
            return bool(lib.ax.AXIsProcessTrustedWithOptions(opts))
        finally:
            lib.cf.CFRelease(opts)
    except Exception:
        return False
