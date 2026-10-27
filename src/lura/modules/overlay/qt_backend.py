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

from .captions import CAPTION_HEIGHT, CAPTION_WIDTH, caption_lines
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

    import html

    SIDE = (CAPTION_WIDTH - OVERLAY_SIZE) // 2   # extra width each side while subtitles show

    class Face(QtWidgets.QWidget):
        def __init__(self, screen):
            super().__init__(None, QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint
                             | QtCore.Qt.Tool | QtCore.Qt.WindowDoesNotAcceptFocus)
            self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
            self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
            geo = screen.geometry()
            #: Where the face itself sits; the window grows around it.
            self._face_pos = QtCore.QPoint(geo.x() + geo.width() - OVERLAY_SIZE - MARGIN_RIGHT,
                                           geo.y() + MARGIN_TOP)
            self._wide = None
            self._offset = 0
            self._blender = StateBlender()
            self._phase = 0.0
            self._image = None
            self._surface = None
            self._drag = None
            self._caption_html = ""
            self.label = QtWidgets.QLabel(self)
            self.label.setWordWrap(True)
            self.label.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop)
            self.label.setTextFormat(QtCore.Qt.RichText)
            self.label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
            self.label.setStyleSheet(
                "QLabel { background: rgba(18, 20, 34, 190); color: #ffffff; border-radius: 14px;"
                " padding: 8px 14px; font-size: 15px; }")
            self._layout(False)

        def _layout(self, wide: bool) -> None:
            """Wide with subtitles, face-sized without, the face never moving."""
            if wide == self._wide:
                return
            self._wide = wide
            if wide:
                # Centred under the face, but never past the screen's edges:
                # the face sits top-right, so a centred strip would hang off.
                area = (self.screen() or app.primaryScreen()).availableGeometry()
                left = self._face_pos.x() - SIDE
                left = max(area.left(), min(left, area.right() + 1 - CAPTION_WIDTH))
                self._offset = self._face_pos.x() - left
                self.setFixedSize(CAPTION_WIDTH, OVERLAY_SIZE + CAPTION_HEIGHT)
                self.move(left, self._face_pos.y())
                self.label.setGeometry(0, OVERLAY_SIZE - 36, CAPTION_WIDTH, CAPTION_HEIGHT)
            else:
                self._offset = 0
                self.setFixedSize(OVERLAY_SIZE, OVERLAY_SIZE)
                self.move(self._face_pos)
            self.label.setVisible(wide)

        def _captions(self) -> None:
            lines = caption_lines(shared)
            self._layout(lines is not None)
            if lines is None:
                return
            you, lura = lines
            parts = []
            if you:
                parts.append(f'<span style="color:#b9bdd6">{html.escape(labels.get("you", "You"))}: '
                             f'{html.escape(you)}</span>')
            if lura:
                parts.append(f'<span style="color:#7fe3f0">{html.escape(labels.get("lura", "Lura"))}:</span> '
                             f'{html.escape(lura)}')
            text = "<br>".join(parts)
            if text != self._caption_html:
                self._caption_html = text
                self.label.setText(text)
                self.label.adjustSize()
                self.label.setFixedWidth(CAPTION_WIDTH)

        def tick(self):
            b = self._blender
            b.update(shared.state, dt=0.033)
            self._phase += 0.033 * (0.60 * b.wl + b.wt + b.ws)
            self._surface = render_surface(self._phase, b, size=OVERLAY_SIZE, amp=shared.amplitude)
            self._surface.flush()
            self._image = QtGui.QImage(self._surface.get_data(), OVERLAY_SIZE, OVERLAY_SIZE,
                                       self._surface.get_stride(),
                                       QtGui.QImage.Format_ARGB32_Premultiplied)
            self._captions()
            self.update()

        def paintEvent(self, _event):
            painter = QtGui.QPainter(self)
            painter.setCompositionMode(QtGui.QPainter.CompositionMode_Source)
            painter.fillRect(self.rect(), QtCore.Qt.transparent)
            if self._image is not None:
                painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                painter.drawImage(self._offset, 0, self._image)
            painter.end()

        def mousePressEvent(self, event):
            if event.button() == QtCore.Qt.LeftButton:
                self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            elif event.button() == QtCore.Qt.RightButton and (toggle := actions.get("subtitles")):
                toggle()   # right-click: show / hide the subtitles

        def mouseMoveEvent(self, event):
            if self._drag is not None and event.buttons() & QtCore.Qt.LeftButton:
                top_left = event.globalPosition().toPoint() - self._drag
                self.move(top_left)
                self._face_pos = top_left + QtCore.QPoint(self._offset, 0)

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
    if "subtitles" in actions:
        subs = menu.addAction(labels.get("subtitles", "Subtitles"))
        subs.setCheckable(True)
        subs.setChecked(shared.subtitles_enabled)
        subs.triggered.connect(lambda _=False: actions["subtitles"]())
        menu.aboutToShow.connect(lambda: subs.setChecked(shared.subtitles_enabled))
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
