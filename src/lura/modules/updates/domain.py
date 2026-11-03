"""Is there a newer Lura, and which file of the release is this computer's.

Pure: the GitHub release comes in as a dict, nothing here touches the
network or the disk.
"""

from __future__ import annotations

import re

REPO = "mohamedjs/lura"
RELEASES_URL = f"https://github.com/{REPO}/releases"
#: Long enough not to bother GitHub's 60-requests-an-hour anonymous limit.
CHECK_EVERY = 6 * 3600

#: How this copy of Lura was installed, which decides how it updates itself.
WINDOWS_SETUP, MAC_APP, DEB, TARBALL, SOURCE = "windows-setup", "mac-app", "deb", "tarball", "source"
SELF_INSTALL = {WINDOWS_SETUP, MAC_APP, DEB, TARBALL}


def parse_version(text: str) -> tuple[int, ...]:
    """"v2.10.1" → (2, 10, 1). Anything after the numbers (-rc1) is ignored."""
    match = re.match(r"v?(\d+(?:\.\d+)*)", (text or "").strip())
    return tuple(int(p) for p in match.group(1).split(".")) if match else ()


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    width = max(len(a), len(b))
    return bool(a) and a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def asset_pattern(kind: str, arch: str) -> re.Pattern | None:
    """The release file this kind of install updates from."""
    if kind == WINDOWS_SETUP:
        return re.compile(r"^Lura-Setup-[\d.]+\.exe$")
    if kind == MAC_APP:
        return re.compile(rf"^Lura-[\d.]+-macos-{re.escape(arch)}\.zip$")
    if kind == DEB:
        return re.compile(r"^lura_[\d.]+_amd64\.deb$")
    if kind == TARBALL:
        return re.compile(r"^lura-[\d.]+-linux-x86_64\.tar\.gz$")
    return None


def pick_asset(release: dict, kind: str, arch: str) -> dict | None:
    """``{"name", "url", "size"}`` of this computer's file in the release, or None."""
    pattern = asset_pattern(kind, arch)
    if pattern is None:
        return None
    for asset in release.get("assets") or []:
        if pattern.match(asset.get("name", "")):
            return {"name": asset["name"], "url": asset.get("browser_download_url", ""),
                    "size": int(asset.get("size") or 0)}
    return None


def checksum_url(release: dict) -> str | None:
    for asset in release.get("assets") or []:
        if asset.get("name") == "SHA256SUMS.txt":
            return asset.get("browser_download_url")
    return None


def checksum_for(sums: str, name: str) -> str | None:
    """The sha256 that ``SHA256SUMS.txt`` lists for ``name`` (``sha256sum`` format)."""
    for line in sums.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == name and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    return None


def trusted_url(url: str) -> bool:
    """Only this repository's own release downloads are ever fetched and run."""
    return url.startswith(f"https://github.com/{REPO}/releases/download/")
