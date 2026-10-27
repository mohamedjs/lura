"""The face in a GTK3 window per monitor — Linux, running from source.

The approved, tuned path. Packaged builds use the Qt backend everywhere,
because a frozen Python cannot import the system's PyGObject.
"""

from __future__ import annotations

import logging
import threading

from .render import MARGIN_RIGHT, MARGIN_TOP, OVERLAY_SIZE, render_surface, surface_rgba
from .state import OverlayState, StateBlender

log = logging.getLogger(__name__)


def available() -> bool:
    try:
        import gi
        gi.require_version("Gdk", "3.0")
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gdk, Gtk  # noqa: F401
        import cairo  # noqa: F401
        return True
    except Exception as exc:
        log.debug("%s backend unavailable: %s", __name__.rsplit(".", 1)[-1], exc)
        return False


def _window(monitor, shared: OverlayState, on_open):
    import gi
    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

    win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    win.set_title("Lura")
    win.set_decorated(False)
    win.set_resizable(False)
    win.set_keep_above(True)
    win.stick()
    win.set_skip_taskbar_hint(True)
    win.set_skip_pager_hint(True)
    win.set_accept_focus(False)
    win.set_default_size(OVERLAY_SIZE, OVERLAY_SIZE)
    win.set_type_hint(Gdk.WindowTypeHint.DOCK)
    win.set_app_paintable(True)
    screen = win.get_screen()
    if visual := screen.get_rgba_visual():
        win.set_visual(visual)

    # GDK geometry is in logical px; the work area is only the primary
    # monitor's on some X11 setups, so use the full geometry.
    geo = monitor.get_geometry()
    x = geo.x + geo.width - OVERLAY_SIZE - MARGIN_RIGHT
    y = geo.y + MARGIN_TOP
    win.move(x, y)
    # Some WMs drop the pre-map move on secondary monitors; repeat once mapped.
    win.connect("map-event", lambda w, e: w.move(x, y))

    css = Gtk.CssProvider()
    css.load_from_data(b"window, window.background { background: none; border: none; box-shadow: none; }")
    Gtk.StyleContext.add_provider_for_screen(screen, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def on_press(widget, event):
        if event.button != 1:
            return
        if event.type == Gdk.EventType._2BUTTON_PRESS and on_open:
            on_open()   # double-click: open the dashboard
        else:
            win.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)

    box = Gtk.EventBox()
    box.set_visible_window(False)
    box.connect("button-press-event", on_press)
    img = Gtk.Image()
    box.add(img)
    win.add(box)

    blender = StateBlender()
    phase = [0.0]

    def tick():
        blender.update(shared.state, dt=0.033)
        # Idle motion slows to 60%; scale the increment so the phase stays
        # monotonic and the motion never jumps across a state blend.
        phase[0] += 0.033 * (0.60 * blender.wl + blender.wt + blender.ws)
        surface = render_surface(phase[0], blender, size=OVERLAY_SIZE, amp=shared.amplitude)
        pix = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(surface_rgba(surface)),
                                              GdkPixbuf.Colorspace.RGB, True, 8,
                                              OVERLAY_SIZE, OVERLAY_SIZE, OVERLAY_SIZE * 4)
        # idle_add, not a direct call: Gtk.main() runs in a daemon thread and
        # setting the pixbuf inline leaves the overlay blank on screen.
        GLib.idle_add(img.set_from_pixbuf, pix)
        return True

    # ponytail: 30fps, not 60. One timeout runs per monitor and a frame costs
    # ~11 ms; at 16 ms the main loop saturates and GTK never repaints.
    GLib.timeout_add(33, tick)
    return win


def start(shared: OverlayState, on_open=None) -> threading.Thread:
    def run():
        try:
            import gi
            gi.require_version("Gdk", "3.0")
            gi.require_version("Gtk", "3.0")
            from gi.repository import Gdk, Gtk

            display = Gdk.Display.get_default()
            if not display or display.get_n_monitors() == 0:
                log.warning("No display for the overlay.")
                return
            windows = [_window(display.get_monitor(i), shared, on_open)
                       for i in range(display.get_n_monitors())]
            for win in windows:
                win.show_all()
            log.info("Overlay (GTK) on %d monitor(s).", len(windows))
            Gtk.main()
        except Exception:
            log.warning("Overlay unavailable — running headless.", exc_info=True)

    thread = threading.Thread(target=run, daemon=True, name="overlay")
    thread.start()
    return thread
