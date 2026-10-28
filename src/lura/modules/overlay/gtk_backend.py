"""The face in a GTK3 window per monitor — Linux, running from source.

The approved, tuned path. Packaged builds use the Qt backend everywhere,
because a frozen Python cannot import the system's PyGObject.
"""

from __future__ import annotations

import logging
import threading

from .captions import CAPTION_HEIGHT, CAPTION_WIDTH, caption_lines
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


def _window(monitor, shared: OverlayState, actions: dict, labels: dict):
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
    css.load_from_data(b"window, window.background { background: none; border: none; box-shadow: none; }"
                       b" .lura-caption { background-color: rgba(18, 20, 34, 0.75); color: #ffffff;"
                       b" border-radius: 14px; padding: 8px 14px; font-size: 15px; }")
    Gtk.StyleContext.add_provider_for_screen(screen, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    side = (CAPTION_WIDTH - OVERLAY_SIZE) // 2
    face_pos = [x, y]
    wide = [False]

    def on_press(widget, event):
        if event.button == 3 and actions.get("subtitles"):
            actions["subtitles"]()   # right-click: show / hide the subtitles
            return
        if event.button != 1:
            return
        if event.type == Gdk.EventType._2BUTTON_PRESS and actions.get("open"):
            actions["open"]()   # double-click: open the dashboard
        else:
            win.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)

    offset = [0]

    def on_configure(widget, event):
        # Remember where the face is after a drag, whatever the window width.
        face_pos[0] = event.x + offset[0]
        face_pos[1] = event.y

    box = Gtk.EventBox()
    box.set_visible_window(False)
    box.connect("button-press-event", on_press)
    win.connect("configure-event", on_configure)
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    column.set_spacing(-30)   # tuck the subtitles up under the chin
    img = Gtk.Image()
    img.set_halign(Gtk.Align.CENTER)
    caption = Gtk.Label()
    caption.set_line_wrap(True)
    caption.set_justify(Gtk.Justification.CENTER)
    caption.set_max_width_chars(40)
    caption.set_size_request(CAPTION_WIDTH - 28, -1)   # + 2×14px CSS padding = CAPTION_WIDTH
    caption.get_style_context().add_class("lura-caption")
    caption.set_no_show_all(True)
    column.pack_start(img, False, False, 0)
    column.pack_start(caption, False, False, 0)
    box.add(column)
    win.add(box)
    last_markup = [""]

    def update_caption():
        lines = caption_lines(shared)
        if (lines is not None) != wide[0]:
            wide[0] = lines is not None
            caption.set_visible(wide[0])
            if not wide[0]:
                offset[0] = 0
                img.set_margin_start(0)
                win.resize(OVERLAY_SIZE, OVERLAY_SIZE)
                win.move(int(face_pos[0]), int(face_pos[1]))
        if lines is None:
            return False
        # Centred under the face but kept on this monitor, measured from the
        # width GTK actually gave the window (it sizes to the wrapped text).
        width = max(win.get_size()[0], CAPTION_WIDTH)
        half = (width - OVERLAY_SIZE) // 2
        left = max(geo.x, min(int(face_pos[0]) - half, geo.x + geo.width - width))
        if offset[0] != int(face_pos[0]) - left:
            offset[0] = int(face_pos[0]) - left
            img.set_halign(Gtk.Align.START)
            img.set_margin_start(offset[0])
            win.move(left, int(face_pos[1]))
        you, lura = lines
        esc = GLib.markup_escape_text
        parts = []
        if you:
            parts.append(f'<span foreground="#b9bdd6">{esc(labels.get("you", "You"))}: {esc(you)}</span>')
        if lura:
            parts.append(f'<span foreground="#7fe3f0">{esc(labels.get("lura", "Lura"))}:</span> {esc(lura)}')
        markup = "\n".join(parts)
        if markup != last_markup[0]:
            last_markup[0] = markup
            caption.set_markup(markup)
        return False

    blender = StateBlender()
    phase = [0.0]
    alive = [True]
    win.connect("destroy", lambda *_: alive.__setitem__(0, False))   # monitor unplugged

    def tick():
        if not alive[0]:
            return False
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
        GLib.idle_add(update_caption)
        return True

    # ponytail: 30fps, not 60. One timeout runs per monitor and a frame costs
    # ~11 ms; at 16 ms the main loop saturates and GTK never repaints.
    GLib.timeout_add(33, tick)
    return win


def start(shared: OverlayState, actions: dict | None = None, labels: dict | None = None) -> threading.Thread:
    actions, labels = actions or {}, labels or {}

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
            windows = {}

            def add(_display, monitor):
                if monitor not in windows:
                    windows[monitor] = _window(monitor, shared, actions, labels)
                    windows[monitor].show_all()
                    log.info("Overlay (GTK) on %d monitor(s).", len(windows))

            def remove(_display, monitor):
                if win := windows.pop(monitor, None):
                    win.destroy()

            for i in range(display.get_n_monitors()):
                add(display, display.get_monitor(i))
            # A monitor that wakes up later (second GPU, dock) gets a face too.
            display.connect("monitor-added", add)
            display.connect("monitor-removed", remove)
            Gtk.main()
        except Exception:
            log.warning("Overlay unavailable — running headless.", exc_info=True)

    thread = threading.Thread(target=run, daemon=True, name="overlay")
    thread.start()
    return thread
