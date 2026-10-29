"""Updates: is a release newer, which file is ours, and a checked install."""

import _setup  # noqa: F401
import hashlib
import io
import sys
import tarfile
import tempfile
from pathlib import Path

DL = "https://github.com/mohamedjs/lura/releases/download/v2.3.0/"
NAMES = ["Lura-Setup-2.3.0.exe", "lura-2.3.0-windows-x64-portable.zip", "Lura-2.3.0-macos-arm64.zip",
         "Lura-macos-arm64.zip", "Lura-2.3.0-macos-x86_64.zip", "lura_2.3.0_amd64.deb",
         "lura-2.3.0-linux-x86_64.tar.gz", "SHA256SUMS.txt"]
RELEASE = {"tag_name": "v2.3.0", "html_url": "https://github.com/mohamedjs/lura/releases/tag/v2.3.0",
           "assets": [{"name": n, "browser_download_url": DL + n, "size": 10} for n in NAMES]}


def check_domain():
    from lura.modules.updates.domain import (checksum_for, is_newer, parse_version, pick_asset,
                                             trusted_url)
    assert parse_version("v2.10.1") == (2, 10, 1) and parse_version("x") == ()
    assert is_newer("2.10.0", "2.9.9") and is_newer("v2.2.1", "2.2") and not is_newer("2.2.0", "2.2")
    assert not is_newer("2.1.9", "2.2.0") and not is_newer("", "2.2.0")
    pick = lambda kind, arch="x86_64": (pick_asset(RELEASE, kind, arch) or {}).get("name")
    assert pick("windows-setup") == "Lura-Setup-2.3.0.exe"
    assert pick("mac-app", "arm64") == "Lura-2.3.0-macos-arm64.zip"
    assert pick("mac-app") == "Lura-2.3.0-macos-x86_64.zip"
    assert pick("deb") == "lura_2.3.0_amd64.deb" and pick("tarball") == "lura-2.3.0-linux-x86_64.tar.gz"
    assert pick("source") is None
    sums = f"{'a' * 64}  lura_2.3.0_amd64.deb\n{'b' * 64} *Lura-Setup-2.3.0.exe\n"
    assert checksum_for(sums, "Lura-Setup-2.3.0.exe") == "b" * 64 and checksum_for(sums, "x") is None
    assert trusted_url(DL + "x.exe") and not trusted_url("https://evil.example/lura.exe")
    assert not trusted_url("https://github.com/someone/lura/releases/download/v1/x.exe")


def check_install_flow():
    from lura.modules.updates import UpdateService
    from lura.modules.updates import infrastructure as infra

    payload = b"the new lura"
    good = hashlib.sha256(payload).hexdigest()
    done, installed = [], []
    saved = {k: getattr(infra, k) for k in ("latest_release", "install_kind", "arch", "fetch_text",
                                            "download", "install")}
    tmp = Path(tempfile.mkdtemp())
    try:
        infra.latest_release = lambda: RELEASE
        infra.install_kind = lambda: "windows-setup"
        infra.arch = lambda: "x86_64"

        def download(url, progress):
            progress(5, 10)
            (tmp / "setup.exe").write_bytes(payload)
            return tmp / "setup.exe"

        infra.download = download
        infra.install = lambda kind, file: installed.append((kind, file.name)) or "exit"

        svc = UpdateService("2.2.0", on_done=done.append)
        status = svc.check(force=True)
        assert status["available"] and status["can_install"] and status["latest"] == "2.3.0", status
        # A download that does not match SHA256SUMS.txt is never run.
        infra.fetch_text = lambda url: f"{'0' * 64}  Lura-Setup-2.3.0.exe\n"
        svc.install()
        _wait(svc)
        assert svc.state == "error" and "checksum" in svc.error and not installed and not done
        # The real thing: verified, handed to the installer, then Lura quits.
        infra.fetch_text = lambda url: f"{good}  Lura-Setup-2.3.0.exe\n"
        svc.install()
        _wait(svc)
        assert installed == [("windows-setup", "setup.exe")] and done == ["exit"], (svc.error, installed)
        # Up to date, or installed from source: offered, not installable.
        assert not UpdateService("2.3.0").check(force=True)["available"]
        infra.install_kind = lambda: "source"
        s = UpdateService("2.2.0").check(force=True)
        assert s["available"] and not s["can_install"]
        try:
            UpdateService("2.3.0").install()
            raise AssertionError("installed while up to date")
        except RuntimeError as exc:
            assert "up to date" in str(exc)
    finally:
        for k, v in saved.items():
            setattr(infra, k, v)


def _wait(svc):
    import time
    for _ in range(100):
        if svc.state not in ("downloading", "installing"):
            return
        time.sleep(0.05)


def check_tarball_swap():
    """The tar.gz install is replaced in place; the running files stay valid."""
    from lura.modules.updates import infrastructure as infra

    root = Path(tempfile.mkdtemp())
    home = root / "lura"
    home.mkdir()
    (home / "LuraApp").write_text("old")
    archive = root / "new.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        data = b"new"
        info = tarfile.TarInfo("lura/LuraApp")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    real = sys.executable
    sys.executable = str(home / "LuraApp")
    try:
        assert infra.install("tarball", archive) == "restart"
    finally:
        sys.executable = real
    assert (home / "LuraApp").read_text() == "new" and not (root / "lura.old").exists()


def main() -> int:
    for name, fn in list(globals().items()):
        if name.startswith("check_"):
            fn()
            print(f"  {name[6:]} OK")
    print("updates OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
