"""The face in a GTK3 window per monitor — Linux, running from source.

The approved, tuned path. Packaged builds use the Qt backend everywhere,
because a frozen Python cannot import the system's PyGObject.
"""

from __future__ import annotations

import logging
import threading

from .captions import BOX_BG, BOX_BORDER, CAPTION_WIDTH, TUCK, caption_lines, caption_width
from .render import MARGIN_RIGHT, MARGIN_TOP, OVERLAY_SIZE, hit_rects, render_surface, surface_rgba
from .state import OverlayState, StateBlender
from .workspaces import x11_input_shape

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
    try:
        gi.require_version("GdkX11", "3.0")
        from gi.repository import GdkX11  # noqa: F401  (gives Gdk windows get_xid)
    except (ValueError, ImportError):
        pass

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
    css.load_from_data((
        "window, window.background { background: none; border: none; box-shadow: none; }"
        " .lura-caption { background-color: rgba(%d, %d, %d, %.2f); color: #e8f6ff;"
        " border: 1px solid rgba(%d, %d, %d, %.2f); border-radius: 16px; padding: 8px 14px;"
        " font-size: 13px; }" % (*BOX_BG, *BOX_BORDER)).encode())
    Gtk.StyleContext.add_provider_for_screen(screen, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    face_pos = [x, y]
    cur = [OVERLAY_SIZE]   # the face's size now; the dashboard can change it live
    wide = [False]

    pressed = [None]     # where the left button went down, until it moves (a drag) or lifts (a tap)
    tap = [0]            # GLib source of the pending tap

    def cancel_tap():
        if tap[0]:
            GLib.source_remove(tap[0])
            tap[0] = 0

    def fire_tap():
        tap[0] = 0
        actions["tap"]()   # tap: talk to her, or stop her and listen
        return False

    def on_press(widget, event):
        if event.button == 3 and actions.get("subtitles"):
            actions["subtitles"]()   # right-click: show / hide the subtitles
            return
        if event.button != 1:
            return
        if event.type == Gdk.EventType._2BUTTON_PRESS:
            cancel_tap()
            pressed[0] = drag[0] = None
            if actions.get("open"):
                actions["open"]()   # double-click: open the dashboard
        elif event.type == Gdk.EventType.BUTTON_PRESS:
            pressed[0] = (event.x_root, event.y_root)
            wx, wy = win.get_position()
            drag[0] = (event.x_root - wx, event.y_root - wy)

    def on_motion(widget, event):
        # Past a few pixels it is a drag, not a tap. Moved by hand: window managers
        # refuse begin_move_drag on DOCK windows, so the face could not be moved.
        if pressed[0] and abs(event.x_root - pressed[0][0]) + abs(event.y_root - pressed[0][1]) > 4:
            pressed[0] = None
        if drag[0] is not None and pressed[0] is None:
            nx, ny = int(event.x_root - drag[0][0]), int(event.y_root - drag[0][1])
            win.move(nx, ny)
            face_pos[0], face_pos[1] = nx + offset[0], ny

    def on_release(widget, event):
        if event.button == 1 and pressed[0] and actions.get("tap"):
            cancel_tap()
            # A second click within this is a double-click, not two taps.
            tap[0] = GLib.timeout_add(Gtk.Settings.get_default().props.gtk_double_click_time, fire_tap)
        pressed[0] = drag[0] = None

    offset = [0]
    drag = [None]

    def on_configure(widget, event):
        # Remember where the face is after a drag, whatever the window width.
        face_pos[0] = event.x + offset[0]
        face_pos[1] = event.y

    box = Gtk.EventBox()
    box.set_visible_window(False)
    box.add_events(Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.BUTTON1_MOTION_MASK)
    box.connect("button-press-event", on_press)
    box.connect("motion-notify-event", on_motion)
    box.connect("button-release-event", on_release)
    win.connect("configure-event", on_configure)
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    column.set_spacing(-int(OVERLAY_SIZE * TUCK))   # tuck the subtitles up into the empty bottom
    img = Gtk.Image()
    img.set_halign(Gtk.Align.CENTER)
    caption = Gtk.Label()
    caption.set_line_wrap(True)
    caption.set_justify(Gtk.Justification.CENTER)
    # A small natural width, so the size request below sets the width and the text wraps
    # (a wrapping label's natural width is the whole line, which widened the window).
    caption.set_max_width_chars(8)
    caption.set_halign(Gtk.Align.START)
    caption.set_size_request(CAPTION_WIDTH - 30, -1)   # + 2×14px padding + border
    caption.get_style_context().add_class("lura-caption")
    caption.set_no_show_all(True)
    column.pack_start(img, False, False, 0)
    column.pack_start(caption, False, False, 0)
    box.add(column)
    win.add(box)
    last_markup = [""]

    placed = [None]

    def update_caption():
        lines = caption_lines(shared)
        if (lines is not None) != wide[0]:
            wide[0] = lines is not None
            caption.set_visible(wide[0])
            placed[0] = None
            if not wide[0]:
                offset[0] = 0
                img.set_margin_start(0)
                win.resize(cur[0], cur[0])
                win.move(int(face_pos[0]), int(face_pos[1]))
        if lines is None:
            return False
        # A box of fixed width centred under the face, kept on this monitor. Everything is
        # computed from the face's position and size — never from the window's width,
        # which includes the offset itself and grew every frame into a full-width banner.
        size, cw = cur[0], caption_width(cur[0])
        tw = max(cw, size)
        fx = int(face_pos[0])
        left = max(geo.x, min(fx - (tw - size) // 2, geo.x + geo.width - tw))
        layout = (left, fx - left, cw, max(0, min(fx - left + size // 2 - cw // 2, tw - cw)), size)
        if layout != placed[0]:
            placed[0] = layout
            offset[0] = layout[1]
            img.set_halign(Gtk.Align.START)
            img.set_margin_start(layout[1])
            caption.set_margin_start(layout[3])
            caption.set_size_request(cw - 30, -1)
            column.set_spacing(-int(size * TUCK))
            win.resize(tw, 1)
            win.move(left, int(face_pos[1]))
        you, lura = lines
        esc = GLib.markup_escape_text
        parts = []
        if you:
            # FSI…PDI isolates the words, so Arabic after an English label keeps its direction.
            parts.append(f'<span foreground="#b9bdd6">{esc(labels.get("you", "You"))}: \u2068{esc(you)}\u2069</span>')
        if lura:
            parts.append(f'<span foreground="#7fe3f0">{esc(labels.get("lura", "Lura"))}:</span> \u2068{esc(lura)}\u2069')
        markup = "\n".join(parts)
        if markup != last_markup[0]:
            last_markup[0] = markup
            caption.set_markup(markup)
        return False

    def resize_face(size):
        """Grow or shrink keeping the right edge where it was. With subtitles
        showing, drop back to the plain layout; the next frame widens it again
        from the new size (resizing in place upset GtkImage's allocation)."""
        face_pos[0] += cur[0] - size
        cur[0] = size
        wide[0] = False
        caption.set_visible(False)
        offset[0] = 0
        img.set_margin_start(0)
        win.resize(size, size)
        win.move(int(face_pos[0]), int(face_pos[1]))
        return False

    shape = [None]

    def set_input(rects):
        """Clicks land only on the drawn pixels and the subtitles."""
        boxes = [(x + offset[0], y, w, h) for x, y, w, h in rects]
        if caption.get_visible():
            a = caption.get_allocation()
            boxes.append((a.x, a.y, a.width, a.height))
        if boxes == shape[0]:
            return
        shape[0] = boxes
        gdk_win = win.get_window()
        if gdk_win is not None and hasattr(gdk_win, "get_xid"):
            x11_input_shape(gdk_win.get_xid(), boxes)

    def show(pix, size, rects):
        """Called on the GTK loop with this frame, shared by every monitor."""
        img.set_from_pixbuf(pix)
        if size != cur[0]:
            resize_face(size)
        update_caption()
        if rects is not None:
            set_input(rects)
        return False

    win.show_frame = show
    return win


def _frames(shared: OverlayState, windows: dict) -> None:
    """One render per frame, shown on every monitor (as the Qt backend does).

    Rendering per window saturated the loop on two monitors once the face
    was scaled up: idle callbacks never ran and the picture froze.
    """
    import time

    from gi.repository import GdkPixbuf, GLib

    blender = StateBlender()
    phase = [0.0]
    frame = [0]

    def tick():
        start = time.monotonic()
        blender.update(shared.state, dt=0.033)
        # Idle motion slows to 60%; scale the increment so the phase stays
        # monotonic and the motion never jumps across a state blend.
        phase[0] += 0.033 * (0.60 * blender.wl + blender.wt + blender.ws)
        look = shared.look
        size = round(OVERLAY_SIZE * look.scale)
        surface = render_surface(phase[0], blender, size=size, amp=shared.amplitude, look=look,
                                 tools=shared.tools())
        pix = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(surface_rgba(surface)),
                                              GdkPixbuf.Colorspace.RGB, True, 8, size, size, size * 4)
        frame[0] += 1
        rects = hit_rects(surface, threshold=20) if frame[0] % 5 == 1 else None
        # idle_add, not a direct call: Gtk.main() runs in a daemon thread and
        # setting the pixbuf inline leaves the overlay blank on screen.
        for win in list(windows.values()):
            GLib.idle_add(win.show_frame, pix, size, rects)
        # ponytail: ~30fps; a big face that takes longer to draw slows the
        # rate rather than starving GTK's repaints.
        cost = int((time.monotonic() - start) * 1000)
        GLib.timeout_add(max(33, cost * 2), tick)
        return False

    GLib.timeout_add(33, tick)


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
            _frames(shared, windows)
            # A monitor that wakes up later (second GPU, dock) gets a face too.
            display.connect("monitor-added", add)
            display.connect("monitor-removed", remove)
            Gtk.main()
        except Exception:
            log.warning("Overlay unavailable — running headless.", exc_info=True)

    thread = threading.Thread(target=run, daemon=True, name="overlay")
    thread.start()
    return thread
