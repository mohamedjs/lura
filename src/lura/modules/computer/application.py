"""Desktop action use cases: Backend port, ComputerService and ToolProvider adapter."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from ..brain import ToolSpec
from .domain import (
    ACTIONS,
    ACTIONS_BY_NAME,
    CONFIRM,
    DESKTOP_TOOL,
    ActionError,
    ActionResult,
    Layout,
    LayoutEntry,
    Monitor,
    PresetApp,
    Rect,
    Window,
    WindowGrip,
    bounds,
    catalog_text,
    clamp_visible,
    find_window,
    grid_rects,
    is_target,
    match_layout,
    monitor_for,
    monitor_target,
    next_window,
    preset_from_windows,
    resolve_desktop,
    snap_rect,
    validate_args,
    validate_keys,
    validate_preset,
    work_area,
)
from .infrastructure import layouts as _layouts_mod
from .infrastructure import presets as _presets_mod

log = logging.getLogger(__name__)

_BUTTONS = {"left": 1, "middle": 2, "right": 3}
_SCROLL = {"up": 4, "down": 5, "left": 6, "right": 7}
_LAYOUT_NAME_RE = re.compile(r"^[\w \-]{1,40}$")


class Backend(Protocol):
    def unsupported_reason(self) -> str: ...
    def probe(self) -> dict[str, bool]: ...
    def own_pids(self) -> set[int]: ...
    # read
    def windows(self) -> list[Window]: ...
    def active_window(self) -> int | None: ...
    def monitors(self) -> list[Monitor]: ...
    def workarea(self) -> Rect | None: ...
    def desktops(self) -> tuple[int, int]: ...
    def showing_desktop(self) -> bool: ...
    def pointer(self) -> tuple[int, int]: ...
    # windows
    def activate(self, wid: int) -> None: ...
    def move_resize(self, wid: int, frame: Rect) -> None: ...
    def set_maximized(self, wid: int, on: bool) -> None: ...
    def minimize(self, wid: int) -> None: ...
    def close(self, wid: int) -> None: ...
    def set_window_desktop(self, wid: int, desktop: int) -> None: ...
    def switch_desktop(self, desktop: int) -> None: ...
    def set_showing_desktop(self, on: bool) -> None: ...
    def overview(self) -> None: ...
    # input
    def pointer_move(self, x: int, y: int) -> None: ...
    def pointer_button(self, button: int, action: str, count: int = 1) -> None: ...
    def key(self, combo: str) -> None: ...
    # system
    def volume(self) -> tuple[float, bool]: ...
    def set_volume(self, fraction: float) -> None: ...
    def change_volume(self, delta_percent: int) -> None: ...
    def toggle_mute(self) -> None: ...
    def mic_muted(self) -> bool: ...
    def toggle_mic(self) -> None: ...
    def brightness(self) -> int: ...
    def set_brightness(self, percent: int) -> None: ...
    def media(self, command: str) -> None: ...
    def lock(self) -> None: ...
    def clipboard_get(self) -> str: ...
    def clipboard_set(self, text: str) -> None: ...


class ComputerService:
    """Validated desktop actions backed by an OS-specific Backend."""

    def __init__(
        self,
        backend: Backend,
        layouts_store: Any = None,
        presets_store: Any = None,
        launcher: Callable[[str], str] | None = None,
    ) -> None:
        self._backend = backend
        self._layouts = layouts_store if layouts_store is not None else _layouts_mod
        self._presets = presets_store if presets_store is not None else _presets_mod
        self._launcher = launcher
        self._sleep = time.sleep
        self._clock = time.monotonic
        self._lock = threading.RLock()
        self._probe: dict[str, bool] | None = None

    def _get_probe(self, refresh: bool = False) -> dict[str, bool]:
        if refresh or self._probe is None:
            self._probe = dict(self._backend.probe())
        return self._probe

    def capabilities(self, refresh: bool = False) -> dict[str, dict]:
        reason = self._backend.unsupported_reason()
        probe = {} if reason else self._get_probe(refresh=refresh)
        out: dict[str, dict] = {}
        for spec in ACTIONS:
            if not spec.requires:
                out[spec.name] = {"available": True, "why": ""}
            elif reason:
                out[spec.name] = {"available": False, "why": reason}
            else:
                missing = [k for k in spec.requires if not probe.get(k, False)]
                if missing:
                    out[spec.name] = {"available": False, "why": f"missing {', '.join(missing)}"}
                else:
                    out[spec.name] = {"available": True, "why": ""}
        return out

    # ── ToolProvider ────────────────────────────────────────────────────────

    def tool_specs(self) -> list[ToolSpec]:
        return [DESKTOP_TOOL]

    def owns(self, name: str) -> bool:
        return name == DESKTOP_TOOL.name

    def call(self, name: str, arguments: dict) -> str:
        raw = dict(arguments or {})
        action = str(raw.pop("action", "") or "")
        raw_conf = raw.pop("confirmed", False)
        if isinstance(raw_conf, bool):
            confirmed = raw_conf
        elif isinstance(raw_conf, str):
            confirmed = raw_conf.strip().lower() in ("true", "1")
        else:
            confirmed = bool(raw_conf)
        return self.run(action, raw, confirmed=confirmed).to_text()

    # ── dispatch ────────────────────────────────────────────────────────────

    def _describe_confirm(self, name: str, args: dict) -> str:
        if name == "window_close":
            w = self._target(args)
            return f"close {self._label(w)}"
        if name == "layout_delete":
            return f"delete layout '{args['name']}'"
        if name == "preset_delete":
            return f"delete preset '{args['name']}'"
        if name == "lock_screen":
            return "lock the screen"
        return name

    def run(self, name: str, args: dict | None = None, *, confirmed: bool = False) -> ActionResult:
        spec = ACTIONS_BY_NAME.get(name)
        if spec is None:
            return ActionResult(False, f"Unknown action '{name}'. Call action='capabilities' for the list.")
        try:
            clean = validate_args(spec, args or {})
        except ActionError as exc:
            return ActionResult(False, str(exc))

        if spec.requires:
            reason = self._backend.unsupported_reason()
            if reason:
                return ActionResult(False, f"Desktop control is not available here: {reason}.")
            probe = self._get_probe()
            missing = [k for k in spec.requires if not probe.get(k, False)]
            if missing:
                return ActionResult(
                    False,
                    f"{name} needs {', '.join(missing)}, which is not available on this computer.",
                )

        if spec.safety == CONFIRM and not confirmed:
            try:
                describe = self._describe_confirm(name, clean)
            except ActionError as exc:
                return ActionResult(False, str(exc))
            except Exception as exc:
                log.warning("desktop_action %s failed before confirm: %s", name, exc)
                return ActionResult(False, f"{name} failed: {exc}")
            return ActionResult(
                False,
                f"{name} needs the user's OK first: {describe}. "
                "Ask them, then call again with confirmed=true.",
                needs_confirmation=True,
            )

        safe_args = {
            k: (f"<{len(v)} chars>" if k == "text" and isinstance(v, str) else v)
            for k, v in clean.items()
            if v is not None
        }
        log.info("desktop_action %s %s", name, safe_args)

        t0 = time.perf_counter()
        try:
            if name == "preset_apply":
                result = self._do_preset_apply(clean)
            else:
                with self._lock:
                    result = getattr(self, f"_do_{name}")(clean)
        except ActionError as exc:
            log.warning("desktop_action %s failed: %s", name, exc)
            result = ActionResult(False, str(exc))
        except Exception as exc:
            log.warning("desktop_action %s failed: %s", name, exc)
            result = ActionResult(False, f"{name} failed: {exc}")
        result.ms = round((time.perf_counter() - t0) * 1000, 1)
        return result

    # ── fast path (continuous moves) ────────────────────────────────────────

    def grip(self, window: str | int = "active") -> WindowGrip:
        with self._lock:
            clean = validate_args(ACTIONS_BY_NAME["window_focus"], {"window": window})
            w = self._target(clean)
            mons = self._backend.monitors()
            b = bounds(mons)
            start = w.rect
            if w.maximized:
                area = work_area(monitor_for(w.rect, mons), self._backend.workarea())
                start = snap_rect(area, "center")
                self._backend.set_maximized(w.id, False)
                self._backend.move_resize(w.id, start)
            log.info("desktop grip %s", w.id)
            return WindowGrip(id=w.id, title=w.title, start=start, bounds=b)

    def place(self, grip: WindowGrip, rect: Rect) -> None:
        with self._lock:
            self._backend.move_resize(grip.id, clamp_visible(rect, grip.bounds))

    def is_maximized(self, window: str | int = "active") -> bool:
        with self._lock:
            clean = validate_args(ACTIONS_BY_NAME["window_focus"], {"window": window})
            return self._target(clean).maximized

    def screen_bounds(self) -> Rect:
        with self._lock:
            return bounds(self._backend.monitors())

    # ── helpers ─────────────────────────────────────────────────────────────

    def _targets(self) -> list[Window]:
        own = self._backend.own_pids()
        return [w for w in self._backend.windows() if is_target(w, own)]

    def _label(self, w: Window) -> str:
        name = w.title[:60] if w.title else (w.app or f"window {w.id}")
        return f"'{name}'"

    def _target(self, args: dict) -> Window:
        ws = self._targets()
        app_query = args.get("app")
        if app_query:
            needle = str(app_query).lower()
            matches = [w for w in ws if needle in w.app.lower() or needle in w.title.lower()]
            if not matches:
                raise ActionError(f"No window matches '{app_query}'.")
            if len(matches) == 1:
                return matches[0]
            current, _ = self._backend.desktops()
            active = self._backend.active_window()
            on_cur = [w for w in matches if w.desktop == current or w.desktop < 0]
            active_hits = [w for w in on_cur if w.id == active]
            if len(active_hits) == 1:
                return active_hits[0]
            listing = ", ".join(f"{w.title or w.app} ({w.id})" for w in matches)
            raise ActionError(
                f"Several windows match '{app_query}': {listing}. Say which one (window=<id>)."
            )
        target = args["window"]
        if target == "active":
            active = self._backend.active_window()
            found = next((w for w in ws if w.id == active), None) if active is not None else None
            if found is None:
                raise ActionError("There is no active window I can control.")
            return found
        found = next((w for w in ws if w.id == target), None)
        if found is None:
            raise ActionError(f"Window {target} is not one I can control.")
        return found

    def _clamp_pointer(self, x: int, y: int) -> tuple[int, int]:
        mons = self._backend.monitors()
        if not mons:
            return (x, y)
        b = bounds(mons)
        cx = max(b.x, min(b.x + b.width - 1, x))
        cy = max(b.y, min(b.y + b.height - 1, y))
        return (cx, cy)

    # ── info ────────────────────────────────────────────────────────────────

    def _do_capabilities(self, args: dict) -> ActionResult:
        caps = self.capabilities()
        avail = {k: v["available"] for k, v in caps.items()}
        reason = self._backend.unsupported_reason()
        return ActionResult(
            True,
            "Desktop actions on this computer:",
            data={"backend": reason or "x11", "actions": catalog_text(avail)},
        )

    def _do_window_list(self, args: dict) -> ActionResult:
        ws = self._targets()
        active = self._backend.active_window()
        mons = self._backend.monitors()
        rows = [
            {
                "id": w.id,
                "title": w.title[:80],
                "app": w.app,
                "workspace": "all" if w.desktop < 0 else w.desktop + 1,
                "monitor": monitor_for(w.rect, mons).index + 1 if mons else 1,
                "x": w.rect.x,
                "y": w.rect.y,
                "width": w.rect.width,
                "height": w.rect.height,
                "maximized": w.maximized,
                "minimized": w.minimized,
                "active": w.id == active,
            }
            for w in reversed(ws)
        ]
        msg = f"Found {len(rows)} window(s)." if rows else "No controllable windows are open."
        return ActionResult(True, msg, data={"windows": rows})

    def _do_monitors(self, args: dict) -> ActionResult:
        mons = self._backend.monitors()
        rows = [
            {
                "monitor": m.index + 1,
                "name": m.name,
                "x": m.rect.x,
                "y": m.rect.y,
                "width": m.rect.width,
                "height": m.rect.height,
                "primary": m.primary,
            }
            for m in mons
        ]
        return ActionResult(True, f"Found {len(rows)} monitor(s).", data={"monitors": rows})

    def _do_pointer_position(self, args: dict) -> ActionResult:
        x, y = self._backend.pointer()
        return ActionResult(True, f"Pointer is at {x}, {y}.", data={"x": x, "y": y})

    # ── windows ─────────────────────────────────────────────────────────────

    def _do_window_focus(self, args: dict) -> ActionResult:
        w = self._target(args)
        self._backend.activate(w.id)
        return ActionResult(True, f"Focused {self._label(w)}.")

    def _move_window(self, w: Window, target_rect: Rect) -> ActionResult:
        mons = self._backend.monitors()
        rect = clamp_visible(target_rect, bounds(mons)) if mons else target_rect
        if w.maximized:
            self._backend.set_maximized(w.id, False)
        self._backend.move_resize(w.id, rect)
        return ActionResult(True, f"Moved {self._label(w)} to {rect.x}, {rect.y}.")

    def _do_window_move(self, args: dict) -> ActionResult:
        w = self._target(args)
        return self._move_window(w, Rect(args["x"], args["y"], w.rect.width, w.rect.height))

    def _do_window_move_by(self, args: dict) -> ActionResult:
        w = self._target(args)
        return self._move_window(w, w.rect.translate(args["dx"], args["dy"]))

    def _do_window_resize(self, args: dict) -> ActionResult:
        w = self._target(args)
        mons = self._backend.monitors()
        wa = work_area(monitor_for(w.rect, mons), self._backend.workarea()) if mons else None
        width = min(args["width"], wa.width) if wa else args["width"]
        height = min(args["height"], wa.height) if wa else args["height"]
        if w.maximized:
            self._backend.set_maximized(w.id, False)
        self._backend.move_resize(w.id, Rect(w.rect.x, w.rect.y, width, height))
        return ActionResult(True, f"Resized {self._label(w)} to {width}x{height}.")

    def _do_window_maximize(self, args: dict) -> ActionResult:
        w = self._target(args)
        self._backend.set_maximized(w.id, True)
        return ActionResult(True, f"Maximized {self._label(w)}.")

    def _do_window_restore(self, args: dict) -> ActionResult:
        w = self._target(args)
        did: list[str] = []
        if w.minimized:
            self._backend.activate(w.id)
            did.append("unminimized")
        if w.maximized:
            self._backend.set_maximized(w.id, False)
            did.append("unmaximized")
        if not did:
            return ActionResult(True, f"{self._label(w)} is already restored.")
        return ActionResult(True, f"Restored {self._label(w)} ({' and '.join(did)}).")

    def _do_window_minimize(self, args: dict) -> ActionResult:
        w = self._target(args)
        self._backend.minimize(w.id)
        return ActionResult(True, f"Minimized {self._label(w)}.")

    def _do_window_close(self, args: dict) -> ActionResult:
        w = self._target(args)
        self._backend.close(w.id)
        return ActionResult(True, f"Closed {self._label(w)}.")

    def _do_window_snap(self, args: dict) -> ActionResult:
        w = self._target(args)
        where = args["where"]
        if where == "full":
            self._backend.set_maximized(w.id, True)
            return ActionResult(True, f"Snapped {self._label(w)} to full screen.")
        mons = self._backend.monitors()
        area = work_area(monitor_for(w.rect, mons), self._backend.workarea())
        rect = snap_rect(area, where)
        if w.maximized:
            self._backend.set_maximized(w.id, False)
        self._backend.move_resize(w.id, rect)
        return ActionResult(True, f"Snapped {self._label(w)} to {where}.")

    def _do_window_to_monitor(self, args: dict) -> ActionResult:
        w = self._target(args)
        mons = self._backend.monitors()
        src = monitor_for(w.rect, mons)
        src_idx = mons.index(src)
        dst_idx = monitor_target(args["target"], src_idx, len(mons))
        dst = mons[dst_idx]
        dx = dst.rect.x - src.rect.x
        dy = dst.rect.y - src.rect.y
        dst_area = work_area(dst, self._backend.workarea())
        rect = clamp_visible(w.rect.translate(dx, dy), dst_area)
        was_max = w.maximized
        if was_max:
            self._backend.set_maximized(w.id, False)
        self._backend.move_resize(w.id, rect)
        if was_max:
            self._backend.set_maximized(w.id, True)
        return ActionResult(True, f"Moved {self._label(w)} to monitor {dst_idx + 1} ({dst.name}).")

    def _do_window_to_workspace(self, args: dict) -> ActionResult:
        w = self._target(args)
        current, count = self._backend.desktops()
        base = current if w.desktop < 0 else w.desktop
        dst = resolve_desktop(args["target"], base, count)
        self._backend.set_window_desktop(w.id, dst)
        if args["follow"]:
            self._backend.switch_desktop(dst)
            self._backend.activate(w.id)
        return ActionResult(True, f"Moved {self._label(w)} to workspace {dst + 1}.")

    def _do_app_switch(self, args: dict) -> ActionResult:
        current, _ = self._backend.desktops()
        active = self._backend.active_window()
        cands = [w for w in self._targets() if not w.minimized and (w.desktop == current or w.desktop < 0)]
        nxt = next_window([w.id for w in cands], active, args["direction"])
        if nxt is None:
            return ActionResult(False, "There is no other window to switch to.")
        self._backend.activate(nxt)
        picked = next(w for w in cands if w.id == nxt)
        return ActionResult(True, f"Switched to {self._label(picked)}.")

    def _do_windows_arrange(self, args: dict) -> ActionResult:
        current, _ = self._backend.desktops()
        mons = self._backend.monitors()
        wa_root = self._backend.workarea()
        cands = [w for w in self._targets() if not w.minimized and (w.desktop == current or w.desktop < 0)]
        groups: dict[int, list[Window]] = {}
        for w in reversed(cands):
            m = monitor_for(w.rect, mons)
            groups.setdefault(m.index, []).append(w)
        mon_by_idx = {m.index: m for m in mons}
        total = 0
        for idx, ws_on_m in groups.items():
            area = work_area(mon_by_idx[idx], wa_root)
            for w, r in zip(ws_on_m, grid_rects(len(ws_on_m), area)):
                if w.maximized:
                    self._backend.set_maximized(w.id, False)
                self._backend.move_resize(w.id, r)
                total += 1
        return ActionResult(True, f"Arranged {total} windows on {len(groups)} monitors.")

    # ── workspaces ──────────────────────────────────────────────────────────

    def _do_workspace_switch(self, args: dict) -> ActionResult:
        current, count = self._backend.desktops()
        dst = resolve_desktop(args["target"], current, count)
        if dst == current:
            return ActionResult(True, f"Already on workspace {current + 1}.")
        self._backend.switch_desktop(dst)
        return ActionResult(True, f"Switched to workspace {dst + 1}.")

    def _do_workspace_overview(self, args: dict) -> ActionResult:
        self._backend.overview()
        return ActionResult(True, "Toggled workspace overview.")

    def _do_show_desktop(self, args: dict) -> ActionResult:
        now = not self._backend.showing_desktop()
        self._backend.set_showing_desktop(now)
        return ActionResult(True, "Showed desktop." if now else "Restored windows from show desktop.")

    # ── layouts ─────────────────────────────────────────────────────────────

    def _do_layout_save(self, args: dict) -> ActionResult:
        name = args["name"]
        if not _LAYOUT_NAME_RE.match(name):
            raise ActionError("Layout name may contain only letters, digits, spaces, '-' and '_'.")
        ws = [w for w in self._targets() if not w.minimized]
        entries = tuple(LayoutEntry(w.app, w.title, w.desktop, w.rect, w.maximized) for w in ws)
        saved_at = datetime.now().isoformat(timespec="seconds")
        all_layouts = self._layouts.load()
        all_layouts[name] = Layout(name, saved_at, entries)
        self._layouts.save(all_layouts)
        return ActionResult(True, f"Saved layout '{name}' with {len(entries)} windows.")

    def _do_layout_restore(self, args: dict) -> ActionResult:
        name = args["name"]
        all_layouts = self._layouts.load()
        if name not in all_layouts:
            known = ", ".join(sorted(all_layouts)) or "none"
            return ActionResult(False, f"No saved layout named '{name}'. Available layouts: {known}.")
        layout = all_layouts[name]
        matched, unmatched = match_layout(layout.windows, self._targets())
        for entry, w in matched:
            if entry.desktop >= 0 and entry.desktop != w.desktop:
                self._backend.set_window_desktop(w.id, entry.desktop)
            if w.maximized:
                self._backend.set_maximized(w.id, False)
            self._backend.move_resize(w.id, entry.rect)
            if entry.maximized:
                self._backend.set_maximized(w.id, True)
        msg = f"Restored {len(matched)} windows."
        if unmatched:
            missing = ", ".join(e.app or e.title for e in unmatched)
            msg += f" Not open: {missing}."
        return ActionResult(True, msg)

    def _do_layout_list(self, args: dict) -> ActionResult:
        all_layouts = self._layouts.load()
        rows = [
            {"name": lay.name, "windows": len(lay.windows), "saved_at": lay.saved_at}
            for lay in all_layouts.values()
        ]
        msg = f"Found {len(rows)} saved layout(s)." if rows else "No saved layouts."
        return ActionResult(True, msg, data={"layouts": rows})

    def _do_layout_delete(self, args: dict) -> ActionResult:
        name = args["name"]
        all_layouts = self._layouts.load()
        if name not in all_layouts:
            return ActionResult(False, f"No saved layout named '{name}'.")
        del all_layouts[name]
        self._layouts.save(all_layouts)
        return ActionResult(True, f"Deleted layout '{name}'.")

    # ── pointer ─────────────────────────────────────────────────────────────

    def _do_pointer_move(self, args: dict) -> ActionResult:
        x, y = self._clamp_pointer(args["x"], args["y"])
        self._backend.pointer_move(x, y)
        return ActionResult(True, f"Moved pointer to {x}, {y}.")

    def _do_pointer_move_by(self, args: dict) -> ActionResult:
        cx, cy = self._backend.pointer()
        x, y = self._clamp_pointer(cx + args["dx"], cy + args["dy"])
        self._backend.pointer_move(x, y)
        return ActionResult(True, f"Moved pointer to {x}, {y}.")

    def _do_pointer_click(self, args: dict) -> ActionResult:
        self._backend.pointer_button(_BUTTONS[args["button"]], "click", args["count"])
        return ActionResult(True, f"Clicked {args['button']} button ({args['count']}x).")

    def _do_pointer_down(self, args: dict) -> ActionResult:
        self._backend.pointer_button(_BUTTONS[args["button"]], "down")
        return ActionResult(True, f"Pressed {args['button']} button down.")

    def _do_pointer_up(self, args: dict) -> ActionResult:
        self._backend.pointer_button(_BUTTONS[args["button"]], "up")
        return ActionResult(True, f"Released {args['button']} button.")

    def _do_pointer_scroll(self, args: dict) -> ActionResult:
        self._backend.pointer_button(_SCROLL[args["direction"]], "click", args["amount"])
        return ActionResult(True, f"Scrolled {args['direction']} by {args['amount']}.")

    def _do_pointer_drag(self, args: dict) -> ActionResult:
        btn = _BUTTONS[args["button"]]
        sx, sy = self._backend.pointer()
        tx, ty = self._clamp_pointer(args["x"], args["y"])
        self._backend.pointer_button(btn, "down")
        try:
            steps = 12
            for i in range(1, steps + 1):
                px = round(sx + (tx - sx) * i / steps)
                py = round(sy + (ty - sy) * i / steps)
                self._backend.pointer_move(px, py)
                if i < steps:
                    time.sleep(0.01)
        finally:
            self._backend.pointer_button(btn, "up")
        return ActionResult(True, f"Dragged pointer to {tx}, {ty}.")

    # ── system ──────────────────────────────────────────────────────────────

    def _do_volume_set(self, args: dict) -> ActionResult:
        self._backend.set_volume(args["percent"] / 100.0)
        frac, muted = self._backend.volume()
        pct = round(frac * 100)
        return ActionResult(True, f"Volume set to {pct}%.", data={"volume": pct, "muted": muted})

    def _do_volume_change(self, args: dict) -> ActionResult:
        self._backend.change_volume(args["delta"])
        frac, muted = self._backend.volume()
        pct = round(frac * 100)
        return ActionResult(True, f"Volume is now {pct}%.", data={"volume": pct, "muted": muted})

    def _do_volume_mute(self, args: dict) -> ActionResult:
        self._backend.toggle_mute()
        _, muted = self._backend.volume()
        return ActionResult(True, "Sound muted." if muted else "Sound unmuted.", data={"muted": muted})

    def _do_mic_mute(self, args: dict) -> ActionResult:
        self._backend.toggle_mic()
        muted = self._backend.mic_muted()
        msg = (
            "Microphone muted. While it is muted Lura cannot hear the wake word."
            if muted else "Microphone unmuted."
        )
        return ActionResult(True, msg, data={"muted": muted})

    def _do_brightness_set(self, args: dict) -> ActionResult:
        self._backend.set_brightness(args["percent"])
        val = self._backend.brightness()
        return ActionResult(True, f"Brightness set to {val}%.", data={"brightness": val})

    def _do_brightness_change(self, args: dict) -> ActionResult:
        cur = self._backend.brightness()
        nxt = max(5, min(100, cur + args["delta"]))
        self._backend.set_brightness(nxt)
        val = self._backend.brightness()
        return ActionResult(True, f"Brightness is now {val}%.", data={"brightness": val})

    def _do_media(self, args: dict) -> ActionResult:
        self._backend.media(args["command"])
        return ActionResult(True, f"Sent media command '{args['command']}'.")

    def _do_lock_screen(self, args: dict) -> ActionResult:
        self._backend.lock()
        return ActionResult(True, "Locked the screen.")

    # ── clipboard & keys ────────────────────────────────────────────────────

    def _do_clipboard_get(self, args: dict) -> ActionResult:
        text = self._backend.clipboard_get()
        msg = "Read clipboard (truncated to 4000 characters)." if len(text) > 4000 else "Read clipboard."
        return ActionResult(True, msg, data={"text": text[:4000]})

    def _do_clipboard_set(self, args: dict) -> ActionResult:
        text = args["text"]
        self._backend.clipboard_set(text)
        return ActionResult(True, f"Copied {len(text)} characters to the clipboard.")

    def _do_copy(self, args: dict) -> ActionResult:
        self._backend.key("ctrl+c")
        return ActionResult(True, "Sent copy shortcut.")

    def _do_cut(self, args: dict) -> ActionResult:
        self._backend.key("ctrl+x")
        return ActionResult(True, "Sent cut shortcut.")

    def _do_paste(self, args: dict) -> ActionResult:
        self._backend.key("ctrl+v")
        return ActionResult(True, "Sent paste shortcut.")

    def _do_select_all(self, args: dict) -> ActionResult:
        self._backend.key("ctrl+a")
        return ActionResult(True, "Sent select-all shortcut.")

    def _do_undo(self, args: dict) -> ActionResult:
        self._backend.key("ctrl+z")
        return ActionResult(True, "Sent undo shortcut.")

    def _do_press_keys(self, args: dict) -> ActionResult:
        combo = validate_keys(args["keys"])
        self._backend.key(combo)
        return ActionResult(True, f"Pressed {combo}.")

    # ── presets ─────────────────────────────────────────────────────────────

    def _do_preset_list(self, args: dict) -> ActionResult:
        all_presets = self._presets.load()
        rows = [
            {
                "name": p.name,
                "apps": [a.launch for a in p.apps],
                "saved_at": p.saved_at,
            }
            for p in all_presets.values()
        ]
        msg = f"Found {len(rows)} saved preset(s)." if rows else "No saved presets."
        return ActionResult(True, msg, data={"presets": rows})

    def _do_preset_define(self, args: dict) -> ActionResult:
        name = args["name"]
        try:
            raw = json.loads(args["spec"])
        except ValueError as exc:
            raise ActionError(f"spec is not valid JSON: {exc}") from None
        if not isinstance(raw, list):
            raise ActionError("spec must be a JSON list of app objects.")
        saved_at = datetime.now().isoformat(timespec="seconds")
        preset = validate_preset(name, raw, saved_at=saved_at)
        all_presets = self._presets.load()
        all_presets[preset.name] = preset
        self._presets.save(all_presets)
        return ActionResult(True, f"Saved preset '{preset.name}' with {len(preset.apps)} apps.")

    def _do_preset_save(self, args: dict) -> ActionResult:
        name = args["name"]
        ws = [w for w in self._targets() if not w.minimized]
        mons = self._backend.monitors()
        saved_at = datetime.now().isoformat(timespec="seconds")
        preset = preset_from_windows(name, ws, mons, saved_at)
        all_presets = self._presets.load()
        all_presets[preset.name] = preset
        self._presets.save(all_presets)
        return ActionResult(True, f"Saved preset '{preset.name}' with {len(preset.apps)} apps.")

    def _do_preset_delete(self, args: dict) -> ActionResult:
        name = args["name"]
        all_presets = self._presets.load()
        if name not in all_presets:
            return ActionResult(False, f"No saved preset named '{name}'.")
        del all_presets[name]
        self._presets.save(all_presets)
        return ActionResult(True, f"Deleted preset '{name}'.")

    def _do_preset_apply(self, args: dict) -> ActionResult:
        name = args["name"]
        all_presets = self._presets.load()
        if name not in all_presets:
            known = ", ".join(sorted(all_presets)) or "none"
            return ActionResult(False, f"No saved preset named '{name}'. Available presets: {known}.")
        preset = all_presets[name]

        matched: list[tuple[PresetApp, Window]] = []
        missing: list[PresetApp] = []
        used: set[int] = set()
        with self._lock:
            before_windows = self._targets()
            before_ids = {w.id for w in before_windows}
            for app in preset.apps:
                w = find_window(app, before_windows, used)
                if w is not None:
                    used.add(w.id)
                    matched.append((app, w))
                else:
                    missing.append(app)

        not_running: list[str] = []
        pending: list[PresetApp] = []
        if missing:
            if self._launcher is None:
                not_running = [app.launch for app in missing]
            else:
                for app in missing:
                    self._launcher(app.launch)
                    pending.append(app)

        opened = 0
        did_not_open: list[str] = []
        if pending:
            deadline = self._clock() + 10.0
            remaining = list(pending)
            while remaining:
                self._sleep(0.4)
                with self._lock:
                    cur = self._targets()
                new_windows = [w for w in cur if w.id not in before_ids]
                still: list[PresetApp] = []
                for app in remaining:
                    w = find_window(app, new_windows, used)
                    if w is not None:
                        used.add(w.id)
                        matched.append((app, w))
                        opened += 1
                    else:
                        still.append(app)
                remaining = still
                if not remaining or self._clock() >= deadline:
                    break
            did_not_open = [app.launch for app in remaining]

        with self._lock:
            mons = self._backend.monitors()
            wa_root = self._backend.workarea()
            b = bounds(mons) if mons else None
            for app, w in matched:
                if app.workspace > 0 and (app.workspace - 1) != w.desktop:
                    self._backend.set_window_desktop(w.id, app.workspace - 1)
                if app.rect is not None:
                    if w.maximized:
                        self._backend.set_maximized(w.id, False)
                    target_rect = clamp_visible(app.rect, b) if b is not None else app.rect
                    self._backend.move_resize(w.id, target_rect)
                elif app.where == "full":
                    if 0 < app.monitor <= len(mons):
                        src = monitor_for(w.rect, mons)
                        dst = mons[app.monitor - 1]
                        if dst != src:
                            if w.maximized:
                                self._backend.set_maximized(w.id, False)
                            dst_area = work_area(dst, wa_root)
                            rect = clamp_visible(
                                w.rect.translate(dst.rect.x - src.rect.x, dst.rect.y - src.rect.y),
                                dst_area,
                            )
                            self._backend.move_resize(w.id, rect)
                    self._backend.set_maximized(w.id, True)
                elif app.where:
                    if mons:
                        mon = (
                            mons[app.monitor - 1]
                            if 0 < app.monitor <= len(mons)
                            else monitor_for(w.rect, mons)
                        )
                        if w.maximized:
                            self._backend.set_maximized(w.id, False)
                        self._backend.move_resize(w.id, snap_rect(work_area(mon, wa_root), app.where))
                elif 0 < app.monitor <= len(mons):
                    src = monitor_for(w.rect, mons)
                    dst = mons[app.monitor - 1]
                    dst_area = work_area(dst, wa_root)
                    rect = clamp_visible(
                        w.rect.translate(dst.rect.x - src.rect.x, dst.rect.y - src.rect.y),
                        dst_area,
                    )
                    was_max = w.maximized
                    if was_max:
                        self._backend.set_maximized(w.id, False)
                    self._backend.move_resize(w.id, rect)
                    if was_max:
                        self._backend.set_maximized(w.id, True)

        placed = len(matched)
        msg = f"Applied preset '{name}': {placed} placed ({opened} opened)."
        if not_running:
            msg += f" Not running: {', '.join(not_running)}."
        if did_not_open:
            msg += f" Did not open: {', '.join(did_not_open)}."
        return ActionResult(placed > 0, msg)
