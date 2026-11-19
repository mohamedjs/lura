"""Live X11 desktop checks on a throwaway xeyes window (not in run_all)."""

import _setup  # noqa: F401
import shutil
import subprocess
import sys
import time

from lura.modules.computer import ComputerService, default_backend
from lura.modules.computer.domain import Rect, monitor_for, snap_rect, work_area


def _find_window(svc: ComputerService, wid: int) -> dict | None:
    res = svc.run("window_list")
    if not res.ok:
        return None
    for row in res.data.get("windows", []):
        if row["id"] == wid:
            return row
    return None


def main() -> int:
    backend = default_backend()
    reason = backend.unsupported_reason()
    if reason:
        print(f"skipped: {reason}")
        return 0
    probe = backend.probe()
    required = ("x11", "xdotool", "xprop", "xwininfo", "xrandr")
    missing = [k for k in required if not probe.get(k)]
    if missing:
        print(f"skipped: missing {', '.join(missing)}")
        return 0
    if not shutil.which("xeyes"):
        print("skipped: xeyes is not installed")
        return 0

    svc = ComputerService(backend)
    passed = 0
    failed = 0

    def record(name: str, ok: bool, msg: str, ms: float = 0.0) -> None:
        nonlocal passed, failed
        status = "ok" if ok else "FAIL"
        print(f"{name}: {status} {msg} ({ms} ms)")
        if ok:
            passed += 1
        else:
            failed += 1

    def near(a: int, b: int, tol: int = 48) -> bool:
        return abs(a - b) <= tol

    def xeyes_ids() -> set[int]:
        found = subprocess.run(["xdotool", "search", "--class", "xeyes"],
                               capture_output=True, text=True, timeout=3.0, check=False)
        return {int(line) for line in found.stdout.split() if line.isdigit()}

    # xeyes sets no _NET_WM_PID, so `search --pid` never finds it: take the new xeyes window.
    before = xeyes_ids()
    proc = subprocess.Popen(["xeyes", "-geometry", "300x200+100+100"])
    try:
        ids: list[int] = []
        for _ in range(50):
            time.sleep(0.1)
            if ids := sorted(xeyes_ids() - before):
                break
        if not ids:
            print("skipped: could not find xeyes window")
            return 0
        wid = ids[-1]
        time.sleep(0.2)

        # ── window checks ───────────────────────────────────────────────────
        r = svc.run("window_move", {"window": wid, "x": 400, "y": 300})
        time.sleep(0.15)
        w = _find_window(svc, wid)
        ok = r.ok and w is not None and near(w["x"], 400) and near(w["y"], 300)
        record("window_move", ok, r.message, r.ms)

        r = svc.run("window_move_by", {"window": wid, "dx": 50, "dy": 40})
        time.sleep(0.15)
        w2 = _find_window(svc, wid)
        ok = (
            r.ok and w is not None and w2 is not None
            and near(w2["x"], w["x"] + 50) and near(w2["y"], w["y"] + 40)
        )
        record("window_move_by", ok, r.message, r.ms)

        r = svc.run("window_resize", {"window": wid, "width": 500, "height": 350})
        time.sleep(0.15)
        w = _find_window(svc, wid)
        ok = r.ok and w is not None and near(w["width"], 500) and near(w["height"], 350)
        record("window_resize", ok, r.message, r.ms)

        mons = backend.monitors()
        wa_root = backend.workarea()
        for where in ("left", "right", "top_right"):
            w_before = _find_window(svc, wid)
            r = svc.run("window_snap", {"window": wid, "where": where})
            time.sleep(0.15)
            w_after = _find_window(svc, wid)
            if w_before is not None and w_after is not None and mons:
                cur_rect = Rect(w_before["x"], w_before["y"], w_before["width"], w_before["height"])
                area = work_area(monitor_for(cur_rect, mons), wa_root)
                exp = snap_rect(area, where)
                ok = r.ok and near(w_after["x"], exp.x) and near(w_after["width"], exp.width)
            else:
                ok = False
            record(f"window_snap {where}", ok, r.message, r.ms)

        r = svc.run("window_maximize", {"window": wid})
        time.sleep(0.15)
        w = _find_window(svc, wid)
        record("window_maximize", r.ok and w is not None and bool(w["maximized"]), r.message, r.ms)

        r = svc.run("window_restore", {"window": wid})
        time.sleep(0.15)
        w = _find_window(svc, wid)
        record("window_restore (unmax)", r.ok and w is not None and not w["maximized"], r.message, r.ms)

        if len(mons) > 1:
            w0 = _find_window(svc, wid)
            r1 = svc.run("window_to_monitor", {"window": wid, "target": "next"})
            time.sleep(0.15)
            w1 = _find_window(svc, wid)
            ok1 = r1.ok and w0 is not None and w1 is not None and w1["monitor"] != w0["monitor"]
            record("window_to_monitor next", ok1, r1.message, r1.ms)
            r2 = svc.run("window_to_monitor", {"window": wid, "target": "prev"})
            time.sleep(0.15)
            w2 = _find_window(svc, wid)
            ok2 = r2.ok and w0 is not None and w2 is not None and w2["monitor"] == w0["monitor"]
            record("window_to_monitor prev", ok2, r2.message, r2.ms)

        w0 = _find_window(svc, wid)
        _, desk_count = backend.desktops()
        if desk_count > 1 and w0 is not None:
            r1 = svc.run("window_to_workspace", {"window": wid, "target": "next", "follow": False})
            time.sleep(0.15)
            w1 = _find_window(svc, wid)
            ok1 = r1.ok and w1 is not None and w1["workspace"] != w0["workspace"]
            record("window_to_workspace next", ok1, r1.message, r1.ms)
            r2 = svc.run("window_to_workspace", {"window": wid, "target": "prev", "follow": False})
            time.sleep(0.15)
            w2 = _find_window(svc, wid)
            ok2 = r2.ok and w2 is not None and w2["workspace"] == w0["workspace"]
            record("window_to_workspace prev", ok2, r2.message, r2.ms)

        r = svc.run("window_minimize", {"window": wid})
        time.sleep(0.15)
        w = _find_window(svc, wid)
        record("window_minimize", r.ok and w is not None and bool(w["minimized"]), r.message, r.ms)

        r = svc.run("window_restore", {"window": wid})
        time.sleep(0.15)
        w = _find_window(svc, wid)
        record("window_restore (unmin)", r.ok and w is not None and not w["minimized"], r.message, r.ms)

        w_saved = _find_window(svc, wid)
        r = svc.run("layout_save", {"name": "live-test"})
        record("layout_save", r.ok, r.message, r.ms)
        # Keep only the xeyes entry, so the restore below never moves the user's real windows.
        from dataclasses import replace

        from lura.modules.computer.infrastructure import layouts as store
        saved = store.load()
        if "live-test" in saved:
            only = tuple(e for e in saved["live-test"].windows if e.app.lower() == "xeyes")
            saved["live-test"] = replace(saved["live-test"], windows=only)
            store.save(saved)

        r = svc.run("window_move", {"window": wid, "x": 200, "y": 200})
        time.sleep(0.15)
        record("window_move (before restore)", r.ok, r.message, r.ms)

        r = svc.run("layout_restore", {"name": "live-test"})
        time.sleep(0.15)
        w_rest = _find_window(svc, wid)
        ok = (
            r.ok and w_saved is not None and w_rest is not None
            and near(w_rest["x"], w_saved["x"]) and near(w_rest["y"], w_saved["y"])
        )
        record("layout_restore", ok, r.message, r.ms)

        r = svc.run("layout_delete", {"name": "live-test"}, confirmed=True)
        record("layout_delete", r.ok, r.message, r.ms)

        r = svc.run("window_close", {"window": wid})
        still = _find_window(svc, wid) is not None
        record("window_close (unconfirmed)", (not r.ok) and r.needs_confirmation and still, r.message, r.ms)

        r = svc.run("window_close", {"window": wid}, confirmed=True)
        deadline = time.monotonic() + 2.0
        gone = False
        while time.monotonic() < deadline:
            if _find_window(svc, wid) is None:
                gone = True
                break
            time.sleep(0.1)
        record("window_close (confirmed)", r.ok and gone, r.message, r.ms)

        # ── system checks (each restored in finally) ────────────────────────
        if probe.get("wpctl"):
            orig_frac, _ = backend.volume()
            orig_pct = round(orig_frac * 100)
            try:
                r = svc.run("volume_change", {"delta": -5})
                record("volume_change", r.ok, r.message, r.ms)
            finally:
                svc.run("volume_set", {"percent": orig_pct})

        if probe.get("gsd_power"):
            orig_b = backend.brightness()
            try:
                r = svc.run("brightness_change", {"delta": 10})
                record("brightness_change", r.ok, r.message, r.ms)
            finally:
                svc.run("brightness_set", {"percent": orig_b})

        if probe.get("xclip"):
            orig_clip = backend.clipboard_get()
            try:
                r1 = svc.run("clipboard_set", {"text": "lura-live-test"})
                r2 = svc.run("clipboard_get")
                ok = r1.ok and r2.ok and r2.data.get("text") == "lura-live-test"
                record("clipboard round trip", ok, r2.message, r2.ms)
            finally:
                backend.clipboard_set(orig_clip)

        orig_desk, desk_count = backend.desktops()
        if desk_count > 1:
            try:
                r = svc.run("workspace_switch", {"target": "next"})
                record("workspace_switch", r.ok, r.message, r.ms)
            finally:
                svc.run("workspace_switch", {"target": str(orig_desk + 1)})

        rp = svc.run("pointer_position")
        if rp.ok:
            rm = svc.run("pointer_move", {"x": rp.data["x"], "y": rp.data["y"]})
            record("pointer_position + pointer_move", rm.ok, rm.message, rm.ms)
        else:
            record("pointer_position", False, rp.message, rp.ms)
    finally:
        proc.kill()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass

    print(f"live desktop: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
