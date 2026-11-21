"""AVFoundation camera authorization status on macOS via the Objective-C runtime."""

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
def _objc():
    avf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AVFoundation.framework/AVFoundation")
    objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
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
