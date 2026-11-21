"""Safe subprocess runner for desktop actions: argv lists only, never shell=True."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Run:
    ok: bool
    code: int
    out: str
    err: str


def run(argv: list[str], timeout: float = 3.0, input_text: str | None = None,
        capture: bool = True) -> Run:
    """``capture=False`` for programs that fork a child which keeps running (xclip serving the
    selection): a captured pipe stays open in the child and the call waits until it times out."""
    assert isinstance(argv, list) and all(isinstance(a, str) for a in argv), "argv must be a list of str"
    out = subprocess.PIPE if capture else subprocess.DEVNULL
    try:
        proc = subprocess.run(
            argv,
            stdout=out,
            stderr=out,
            text=True,
            timeout=timeout,
            input=input_text,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
    except FileNotFoundError:
        return Run(False, 127, "", "not installed")
    except subprocess.TimeoutExpired:
        return Run(False, -1, "", f"timed out after {timeout}s")
    return Run(proc.returncode == 0, proc.returncode, proc.stdout or "", proc.stderr or "")
