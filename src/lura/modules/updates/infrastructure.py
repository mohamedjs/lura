"""Talking to GitHub, downloading, and handing over to the new version.

Each installer runs *after* this Lura has exited (Windows and macOS replace
files that are in use otherwise), so ``install`` only prepares and starts it;
the caller then quits. A Linux .deb is installed in place, then Lura restarts.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from ...shared import paths
from ...shared.http import USER_AGENT, request_json
from ...shared.platform import FROZEN, IS_MAC, IS_WINDOWS
from .domain import DEB, MAC_APP, REPO, SOURCE, TARBALL, WINDOWS_SETUP

log = logging.getLogger(__name__)


def latest_release() -> dict:
    return request_json(f"https://api.github.com/repos/{REPO}/releases/latest",
                        {"Accept": "application/vnd.github+json"}, timeout=20)


def install_kind() -> str:
    """How this copy was installed."""
    if not FROZEN:
        return SOURCE
    exe = Path(sys.executable).resolve()
    if IS_WINDOWS:
        return WINDOWS_SETUP if (exe.parent / "unins000.exe").exists() else SOURCE
    if IS_MAC:
        return MAC_APP if mac_bundle() else SOURCE
    if str(exe).startswith("/opt/lura/") and Path("/var/lib/dpkg/info/lura.list").exists():
        return DEB
    return TARBALL


def arch() -> str:
    machine = platform.machine().lower()
    return "arm64" if machine in ("arm64", "aarch64") else "x86_64"


def mac_bundle() -> Path | None:
    """The running Lura.app (…/Lura.app/Contents/MacOS/LuraApp → …/Lura.app)."""
    exe = Path(sys.executable).resolve()
    for parent in exe.parents:
        if parent.suffix == ".app":
            return parent
    return None


def download(url: str, progress=lambda done, total: None) -> Path:
    """Into Lura's data folder (not /tmp: it can be cleaned under a running install)."""
    folder = paths.data_dir() / "updates"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / url.rsplit("/", 1)[-1]
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response, open(target.with_suffix(".part"), "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while chunk := response.read(1 << 16):
            out.write(chunk)
            done += len(chunk)
            progress(done, total)
    os.replace(target.with_suffix(".part"), target)
    return target


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install(kind: str, file: Path) -> str:
    """Start installing ``file``. Returns "exit" (the installer takes over once
    Lura has quit and starts the new one) or "restart" (installed; restart)."""
    if kind == WINDOWS_SETUP:
        return _windows(file)
    if kind == MAC_APP:
        return _mac(file)
    if kind == DEB:
        return _deb(file)
    if kind == TARBALL:
        return _tarball(file)
    raise RuntimeError("This copy of Lura runs from source: update it with git pull.")


def _windows(setup: Path) -> str:
    # Silent, into the folder it is already installed in (Inno remembers it by
    # AppId); lura.iss closes the running Lura and starts the new one.
    flags = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"]
    subprocess.Popen([str(setup), *flags], creationflags=0x00000008 | 0x00000200, close_fds=True)
    return "exit"


def _mac(archive: Path) -> str:
    bundle = mac_bundle()
    if bundle is None:
        raise RuntimeError("Could not find the running Lura.app.")
    if not os.access(bundle.parent, os.W_OK):
        raise RuntimeError(f"{bundle.parent} is not writable: update with "
                           "brew upgrade --cask --greedy lura, or download the new zip.")
    staging = Path(tempfile.mkdtemp(prefix="lura-update-", dir=archive.parent))
    subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive), str(staging)], check=True, timeout=300)
    new = next(staging.glob("*.app"), None)
    if new is None:
        raise RuntimeError("The download has no Lura.app in it.")
    subprocess.run(["/usr/bin/xattr", "-dr", "com.apple.quarantine", str(new)], check=False)
    # Swap once this Lura has exited, then open the new one.
    script = staging / "swap.sh"
    script.write_text(
        "#!/bin/sh\n"
        f"while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.5; done\n"
        f'rm -rf "{bundle}.old" && mv "{bundle}" "{bundle}.old" && mv "{new}" "{bundle}" '
        f'&& rm -rf "{bundle}.old" || {{ [ -d "{bundle}" ] || mv "{bundle}.old" "{bundle}"; }}\n'
        f'open "{bundle}"\n'
        f'rm -rf "{staging}"\n')
    subprocess.Popen(["/bin/sh", str(script)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "exit"


def _deb(package: Path) -> str:
    # pkexec asks for the password in a desktop dialog; apt installs the
    # dependencies the new version may need.
    if not shutil.which("pkexec"):
        raise RuntimeError(f"Install it with: sudo apt install {package}")
    done = subprocess.run(["pkexec", "apt-get", "install", "-y", str(package)],
                          capture_output=True, text=True, timeout=900)
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout or "the installation was cancelled").strip()[-300:])
    return "restart"


def _tarball(archive: Path) -> str:
    import tarfile

    home = Path(sys.executable).resolve().parent          # …/lura (holds lura, LuraApp, _internal)
    if not os.access(home.parent, os.W_OK):
        raise RuntimeError(f"{home} is not writable; unpack {archive} there yourself.")
    staging = Path(tempfile.mkdtemp(prefix="lura-update-", dir=home.parent))
    with tarfile.open(archive) as tar:
        tar.extractall(staging, filter="data")
    new = staging / "lura"
    if not (new / "LuraApp").exists():
        raise RuntimeError("The download has no lura/LuraApp in it.")
    old = home.with_name(home.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    home.rename(old)                 # the running files stay valid under their new name
    new.rename(home)
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    return "restart"
