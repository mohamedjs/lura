"""Lura's own Node.js runtime for MCP connectors (downloaded once into <data>/node)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import threading
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

from ....shared import paths
from ....shared.platform import IS_WINDOWS

log = logging.getLogger(__name__)

NODE_MAJOR = 22
FALLBACK_VERSION = "v22.12.0"
DIST = "https://nodejs.org/dist/"

_lock = threading.Lock()
_state = {"installing": False, "error": ""}


def home() -> Path:
    return paths.data_dir() / "node"


def platform_triplet(system: str | None = None, machine: str | None = None) -> tuple[str, str, str] | None:
    sys_name = (system if system is not None else platform.system()).lower()
    mach = (machine if machine is not None else platform.machine()).lower()
    os_map = {"darwin": "darwin", "linux": "linux", "windows": "win"}
    arch_map = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64", "x64": "x64"}
    os_id = os_map.get(sys_name)
    arch = arch_map.get(mach)
    if not os_id or not arch:
        return None
    ext = "zip" if os_id == "win" else "tar.xz"
    return (os_id, arch, ext)


def pick_version(index: list[dict], major: int = NODE_MAJOR) -> str | None:
    prefix = f"v{major}."
    for entry in index:
        if not isinstance(entry, dict):
            continue
        version = entry.get("version")
        if isinstance(version, str) and version.startswith(prefix) and entry.get("lts"):
            return version
    return None


def parse_shasums(text: str, filename: str) -> str | None:
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1] == filename:
            return parts[0].lower()
    return None


def node_exe() -> Path | None:
    candidate = home() / "current" / ("node.exe" if IS_WINDOWS else Path("bin") / "node")
    return candidate if candidate.is_file() else None


def npm_script(name: str) -> Path | None:
    rel = (
        Path("node_modules") / "npm" / "bin" / f"{name}-cli.js"
        if IS_WINDOWS
        else Path("lib") / "node_modules" / "npm" / "bin" / f"{name}-cli.js"
    )
    candidate = home() / "current" / rel
    return candidate if candidate.is_file() else None


def ready() -> bool:
    return node_exe() is not None and npm_script("npx") is not None


def _get_text(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Lura"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _latest_version() -> str:
    try:
        index = json.loads(_get_text(DIST + "index.json", timeout=20))
        if isinstance(index, list):
            picked = pick_version(index, NODE_MAJOR)
            if picked:
                return picked
    except Exception as exc:
        log.debug("Could not read Node.js index.json, using fallback %s: %s", FALLBACK_VERSION, exc)
    return FALLBACK_VERSION


def _download_verified(url: str, dest: Path, expected_sha256: str) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Lura"})
    digest = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
        while chunk := resp.read(65536):
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest().lower() != expected_sha256:
        raise RuntimeError("Node.js download is corrupt (checksum mismatch).")


def ensure(progress: Callable[[str], None] | None = None) -> bool:
    with _lock:
        if ready():
            return True
        triplet = platform_triplet()
        if triplet is None:
            _state["error"] = "No Node.js build for this computer."
            return False
        _state["installing"] = True
        try:
            v = _latest_version()
            os_id, arch, ext = triplet
            name = f"node-{v}-{os_id}-{arch}"
            filename = f"{name}.{ext}"
            msg = f"Downloading Node.js {v}…"
            if progress is not None:
                progress(msg)
            log.info("%s", msg)
            shasums = _get_text(DIST + f"{v}/SHASUMS256.txt", timeout=20)
            expected = parse_shasums(shasums, filename)
            if not expected:
                raise RuntimeError(f"Checksum for {filename} not found in SHASUMS256.txt.")
            home().mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=home()) as tmp:
                tmp_path = Path(tmp)
                archive = tmp_path / filename
                _download_verified(DIST + f"{v}/{filename}", archive, expected)
                if ext == "zip":
                    with zipfile.ZipFile(archive) as zf:
                        zf.extractall(tmp_path)
                else:
                    with tarfile.open(archive, "r:xz") as tf:
                        try:
                            # Keeps exec bits and relative symlinks such as bin/npx.
                            tf.extractall(tmp_path, filter="data")
                        except TypeError:
                            tf.extractall(tmp_path)
                current = home() / "current"
                if current.exists():
                    shutil.rmtree(current)
                shutil.move(str(tmp_path / name), str(current))
                (home() / "version.txt").write_text(v, encoding="utf-8")
            exe = node_exe()
            if exe is None:
                raise RuntimeError("Node.js executable missing after extraction.")
            if not IS_WINDOWS:
                os.chmod(exe, 0o755)
            proc = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=20)
            if proc.returncode != 0:
                raise RuntimeError(f"Node.js does not run: {(proc.stderr or '')[:200]}")
            _state["error"] = ""
            log.info("Node.js %s ready in %s", v, home())
            return True
        except Exception as exc:
            _state["error"] = str(exc)
            log.warning("Could not install Node.js: %s", exc)
            return False
        finally:
            _state["installing"] = False


def _base_command(command: str) -> str:
    name = Path(command).name.lower()
    for suffix in (".cmd", ".exe", ".ps1"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def is_node_command(command: str) -> bool:
    return _base_command(command) in {"node", "npx", "npm"}


def resolve(command: str, args: list[str]) -> list[str] | None:
    try:
        if not is_node_command(command) or not ready():
            return None
        base = _base_command(command)
        exe = node_exe()
        if exe is None:
            return None
        if base == "node":
            return [str(exe), *args]
        script = npm_script(base)
        if script is None:
            return None
        return [str(exe), str(script), *args]
    except Exception:
        return None


def env(base: dict[str, str]) -> dict[str, str]:
    out = dict(base)
    try:
        if not ready():
            return out
        exe = node_exe()
        if exe is None:
            return out
        existing_path = out.get("PATH", "")
        if IS_WINDOWS:
            # Windows env is case-insensitive; keep only PATH.
            alt_path = out.pop("Path", "")
            if not existing_path:
                existing_path = alt_path
        out["PATH"] = str(exe.parent) + os.pathsep + existing_path
        out["npm_config_cache"] = str(home() / "npm-cache")
        out["npm_config_update_notifier"] = "false"
        out["npm_config_fund"] = "false"
        out["npm_config_audit"] = "false"
        return out
    except Exception:
        return dict(base)


def status() -> dict:
    try:
        version = (home() / "version.txt").read_text(encoding="utf-8").strip()
    except OSError:
        version = ""
    return {
        "ready": ready(),
        "version": version,
        "installing": _state["installing"],
        "error": _state["error"],
        "path": str(home()),
    }
