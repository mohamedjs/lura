"""AVFoundation camera authorization and device listing on macOS via the Objective-C runtime."""

from __future__ import annotations

import ctypes
import functools
import logging

from ....shared.platform import IS_MAC

log = logging.getLogger(__name__)

_STATUS_NAMES: dict[int, str] = {
    0: "not_determined",
    1: "restricted",
    2: "denied",
    3: "authorized",
}


@functools.lru_cache(maxsize=1)
def _runtime():
    avf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AVFoundation.framework/AVFoundation")
    objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.objc_autoreleasePoolPush.argtypes = []
    objc.objc_autoreleasePoolPush.restype = ctypes.c_void_p
    objc.objc_autoreleasePoolPop.argtypes = [ctypes.c_void_p]
    objc.objc_autoreleasePoolPop.restype = None
    return (objc, avf)


@functools.lru_cache(maxsize=1)
def _objc():
    objc, avf = _runtime()
    # Exact prototype for arm64 ABI: never call objc_msgSend as variadic.
    msg_send_type = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    msg_send = msg_send_type(ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value)
    cls = objc.objc_getClass(b"AVCaptureDevice")
    sel = objc.sel_registerName(b"authorizationStatusForMediaType:")
    media_type = ctypes.c_void_p.in_dll(avf, "AVMediaTypeVideo").value
    return (msg_send, cls, sel, media_type)


def authorization() -> str:
    if not IS_MAC:
        return "authorized"
    try:
        msg_send, cls, sel, media_type = _objc()
        if not cls or not sel or not media_type:
            return "unknown"
        code = int(msg_send(cls, sel, media_type))
        return _STATUS_NAMES.get(code, "unknown")
    except Exception:
        return "unknown"


def list_devices() -> list[tuple[str, str, int]]:
    """Return AVFoundation video/muxed devices as (name, device_type, transport) in OpenCV's order."""
    if not IS_MAC:
        return []
    try:
        objc, avf = _runtime()
        addr = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
        send_id = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(addr)
        send_id_id = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(addr)
        send_uint = ctypes.CFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p)(addr)
        send_id_uint = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)(addr)
        send_cstr = ctypes.CFUNCTYPE(ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p)(addr)
        send_i32 = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p)(addr)
        send_bool_sel = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(addr)

        cls = objc.objc_getClass(b"AVCaptureDevice")
        if not cls:
            return []
        sel_devices = objc.sel_registerName(b"devicesWithMediaType:")
        sel_count = objc.sel_registerName(b"count")
        sel_at = objc.sel_registerName(b"objectAtIndex:")
        sel_name = objc.sel_registerName(b"localizedName")
        sel_utf8 = objc.sel_registerName(b"UTF8String")
        sel_type = objc.sel_registerName(b"deviceType")
        sel_transport = objc.sel_registerName(b"transportType")
        sel_responds = objc.sel_registerName(b"respondsToSelector:")

        out: list[tuple[str, str, int]] = []
        pool = objc.objc_autoreleasePoolPush()
        try:
            for sym in ("AVMediaTypeVideo", "AVMediaTypeMuxed"):
                try:
                    media_type = ctypes.c_void_p.in_dll(avf, sym).value
                except ValueError:
                    continue
                if not media_type:
                    continue
                arr = send_id_id(cls, sel_devices, media_type)
                if not arr:
                    continue
                n = int(send_uint(arr, sel_count))
                for i in range(n):
                    dev = send_id_uint(arr, sel_at, i)
                    if not dev:
                        continue
                    ns_name = send_id(dev, sel_name)
                    raw_name = send_cstr(ns_name, sel_utf8) if ns_name else None
                    name = raw_name.decode("utf-8", errors="replace") if raw_name else ""
                    kind = ""
                    if send_bool_sel(dev, sel_responds, sel_type):
                        ns_type = send_id(dev, sel_type)
                        raw_type = send_cstr(ns_type, sel_utf8) if ns_type else None
                        kind = raw_type.decode("utf-8", errors="replace") if raw_type else ""
                    transport = (
                        int(send_i32(dev, sel_transport))
                        if send_bool_sel(dev, sel_responds, sel_transport)
                        else 0
                    )
                    out.append((name, kind, transport))
        finally:
            objc.objc_autoreleasePoolPop(pool)
        return out
    except Exception:
        log.debug("AVFoundation device listing failed", exc_info=True)
        return []
