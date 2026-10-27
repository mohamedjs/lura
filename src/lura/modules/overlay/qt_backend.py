"""The face in a Qt window per screen — macOS, Windows, and packaged Linux builds.

Qt must own the main thread (Cocoa insists), so this backend *runs* the main
loop and the assistant lives on a worker thread. It also puts Lura in the
system tray / menu bar: open the dashboard, talk now, quit.

The cairo surface is handed to Qt without conversion: cairo's ARGB32 is
premultiplied BGRA in memory, which is exactly QImage's
``Format_ARGB32_Premultiplied`` on little-endian machines.
"""

from __future__ import annotations

import logging
import sys
import threading

from .render import MARGIN_RIGHT, MARGIN_TOP, OVERLAY_SIZE, render_surface
from .state import OverlayState, StateBlender

log = logging.getLogger(__name__)


#: What Qt's xcb plugin links against beyond a base desktop (found by ldd).
XCB_LIBS = ("xcb-cursor", "xcb-icccm", "xcb-keysyms", "xcb-xkb", "xkbcommon-x11")


def _linux_display_ok() -> bool:
    """Qt *aborts the process* when its platform plugin cannot load, taking
    the assistant down with the face. Check the usual causes first."""
    import ctypes.util
    import os

    if os.environ.get("QT_QPA_PLATFORM"):
        return True
    if os.environ.get("WAYLAND_DISPLAY"):
        return True
    if not os.environ.get("DISPLAY"):
        log.debug("qt_backend: no DISPLAY or WAYLAND_DISPLAY")
        return False
    missing = [lib for lib in XCB_LIBS if not ctypes.util.find_library(lib)]
    if missing:
        log.warning("The face needs these X11 libraries: %s (sudo apt install libxcb-cursor0 "
                    "libxcb-icccm4 libxcb-keysyms1 libxcb-xkb1 libxkbcommon-x11-0).", ", ".join(missing))
        return False
    return True


def available() -> bool:
    try:
        import cairo  # noqa: F401
        from PySide6 import QtWidgets  # noqa: F401
        if sys.platform.startswith("linux") and not _linux_display_ok():
            return False
        return True
    except Exception as exc:
        log.debug("%s backend unavailable: %s", __name__.rsplit(".", 1)[-1], exc)
        return False


def run(shared: OverlayState, stop: threading.Event, actions: dict, labels: dict,
        show_face: bool = True) -> None:
    from PySide6 import QtCore, QtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Lura")

    class Face(QtWidgets.QWidget):
        def __init__(self, screen):
            super().__init__(None, QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint
                             | QtCore.Qt.Tool | QtCore.Qt.WindowDoesNotAcceptFocus)
            self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
            self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
            self.setFixedSize(OVERLAY_SIZE, OVERLAY_SIZE)
            geo = screen.geometry()
            self.move(geo.x() + geo.width() - OVERLAY_SIZE - MARGIN_RIGHT, geo.y() + MARGIN_TOP)
            self._blender = StateBlender()
            self._phase = 0.0
            self._image = None
            self._surface = None
            self._drag = None

        def tick(self):
            b = self._blender
            b.update(shared.state, dt=0.033)
            self._phase += 0.033 * (0.60 * b.wl + b.wt + b.ws)
            self._surface = render_surface(self._phase, b, size=OVERLAY_SIZE, amp=shared.amplitude)
            self._surface.flush()
            self._image = QtGui.QImage(self._surface.get_data(), OVERLAY_SIZE, OVERLAY_SIZE,
                                       self._surface.get_stride(),
                                       QtGui.QImage.Format_ARGB32_Premultiplied)
            self.update()

        def paintEvent(self, _event):
            painter = QtGui.QPainter(self)
            painter.setCompositionMode(QtGui.QPainter.CompositionMode_Source)
            painter.fillRect(self.rect(), QtCore.Qt.transparent)
            if self._image is not None:
                painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                painter.drawImage(0, 0, self._image)
            painter.end()

        def mousePressEvent(self, event):
            if event.button() == QtCore.Qt.LeftButton:
                self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

        def mouseMoveEvent(self, event):
            if self._drag is not None and event.buttons() & QtCore.Qt.LeftButton:
                self.move(event.globalPosition().toPoint() - self._drag)

        def mouseReleaseEvent(self, _event):
            self._drag = None

        def mouseDoubleClickEvent(self, _event):
            if open_dashboard := actions.get("open"):
                open_dashboard()

    faces = [Face(screen) for screen in app.screens()] if show_face else []
    for face in faces:
        face.show()

    # Tray icon: one still frame of the face.
    icon_surface = render_surface(1.0, StateBlender(), size=64, amp=0.0)
    icon_surface.flush()
    icon = QtGui.QIcon(QtGui.QPixmap.fromImage(QtGui.QImage(
        icon_surface.get_data(), 64, 64, icon_surface.get_stride(),
        QtGui.QImage.Format_ARGB32_Premultiplied).copy()))
    tray = QtWidgets.QSystemTrayIcon(icon)
    menu = QtWidgets.QMenu()
    for key in ("open", "talk"):
        if key in actions:
            menu.addAction(labels.get(key, key)).triggered.connect(lambda _=False, k=key: actions[k]())
    menu.addSeparator()
    menu.addAction(labels.get("quit", "Quit")).triggered.connect(lambda: stop.set())
    tray.setContextMenu(menu)
    tray.setToolTip("Lura")
    tray.activated.connect(lambda reason: actions.get("open", lambda: None)()
                           if reason == QtWidgets.QSystemTrayIcon.DoubleClick else None)
    if QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
        tray.show()

    frame = QtCore.QTimer()
    frame.timeout.connect(lambda: [face.tick() for face in faces])
    frame.start(33)
    # Polling the stop event also hands control back to Python regularly,
    # so Ctrl+C and SIGTERM handlers get to run under Qt's loop.
    watcher = QtCore.QTimer()
    watcher.timeout.connect(lambda: app.quit() if stop.is_set() else None)
    watcher.start(200)
    log.info("Overlay (Qt) on %d screen(s).", len(faces))
    app.exec()
    tray.hide()
