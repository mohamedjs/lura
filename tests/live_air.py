"""Live webcam benchmark for Lura Air (NOT part of tests/run_all.py)."""

from __future__ import annotations

import _setup  # noqa: F401
import json
import sys
import time
from collections import Counter

from lura.modules.air import AirService, air_available, list_cameras, pick_camera


def main() -> int:
    reason = air_available()
    if reason:
        print(f"skipped: {reason}")
        return 0
    if pick_camera(list_cameras()) is None:
        print("skipped: no usable camera")
        return 0

    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
    counts: Counter[str] = Counter()

    def on_event(ev) -> None:
        counts[ev.gesture] += 1
        print(f"{ev.t:8.3f}  {ev.gesture:13} {ev.hand:5} {ev.confidence:.2f}  "
              f"{ev.x:.3f},{ev.y:.3f}  {ev.data or ''}", flush=True)

    svc = AirService(None)
    svc.subscribe(on_event)
    svc.start()
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        svc.stop()

    print("\nstats:")
    print(json.dumps(svc.stats(), indent=2))
    print("\ngestures:")
    print(json.dumps(dict(counts), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
