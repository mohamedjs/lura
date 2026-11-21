"""Checks for Lura's own Node.js runtime and StdioClient wiring."""

import os
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

_tmp = tempfile.mkdtemp(prefix="lura-node-test-")
os.environ["LURA_DATA_DIR"] = _tmp

from lura.modules.connectors.infrastructure import node  # noqa: E402
from lura.modules.connectors.infrastructure.clients import StdioClient  # noqa: E402
from lura.shared.platform import IS_WINDOWS  # noqa: E402


def main() -> int:
    assert node.platform_triplet("Darwin", "arm64") == ("darwin", "arm64", "tar.xz")
    assert node.platform_triplet("Darwin", "x86_64") == ("darwin", "x64", "tar.xz")
    assert node.platform_triplet("Windows", "AMD64") == ("win", "x64", "zip")
    assert node.platform_triplet("Linux", "aarch64") == ("linux", "arm64", "tar.xz")
    assert node.platform_triplet("SunOS", "sparc") is None

    assert node.pick_version([
        {"version": "v23.1.0", "lts": False},
        {"version": "v22.12.0", "lts": "Jod"},
        {"version": "v22.11.0", "lts": "Jod"},
    ]) == "v22.12.0"
    assert node.pick_version([]) is None

    assert node.parse_shasums(
        "abc  node-v22.12.0-darwin-arm64.tar.xz\nDEF  other.zip\n",
        "other.zip",
    ) == "def"
    assert node.parse_shasums("abc  node.tar.xz\n", "missing.zip") is None

    # Fake install in LURA_DATA_DIR
    cur = Path(_tmp) / "node" / "current"
    if IS_WINDOWS:
        node_bin = cur / "node.exe"
        npm_bin = cur / "node_modules" / "npm" / "bin"
    else:
        node_bin = cur / "bin" / "node"
        npm_bin = cur / "lib" / "node_modules" / "npm" / "bin"
    node_bin.parent.mkdir(parents=True, exist_ok=True)
    node_bin.write_text("", encoding="utf-8")
    npm_bin.mkdir(parents=True, exist_ok=True)
    npx_cli = npm_bin / "npx-cli.js"
    npm_cli = npm_bin / "npm-cli.js"
    npx_cli.write_text("", encoding="utf-8")
    npm_cli.write_text("", encoding="utf-8")

    assert node.ready() is True
    assert node.resolve("npx", ["-y", "pkg"]) == [str(node_bin), str(npx_cli), "-y", "pkg"]
    assert node.resolve("npx.cmd", []) == [str(node_bin), str(npx_cli)]
    assert node.resolve("/usr/bin/node", ["a"]) == [str(node_bin), "a"]
    assert node.resolve("python", []) is None

    resolved_env = node.env({"PATH": "/usr/bin"})
    assert resolved_env["PATH"].startswith(str(node_bin.parent) + os.pathsep)
    assert resolved_env["npm_config_cache"].endswith("npm-cache")
    assert node.is_node_command("NPX.CMD") is True

    client = StdioClient("npx", ["-y", "x"], {"A": "1"})
    assert client._argv[0] == str(node_bin)
    assert client._env["A"] == "1"

    print("node runtime OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
