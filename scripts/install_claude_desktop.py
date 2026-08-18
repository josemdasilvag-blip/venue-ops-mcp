#!/usr/bin/env python3
"""Register this server in Claude Desktop, without touching anything else.

Run:  python scripts/install_claude_desktop.py

Makes a timestamped backup first, adds only the `mcpServers.venue-ops` entry,
and leaves every other preference exactly as it was.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY = "venue-ops"


def config_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ["APPDATA"]) / "Claude"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Claude"
    else:
        base = Path.home() / ".config" / "Claude"
    return base / "claude_desktop_config.json"


def main() -> None:
    cfg_file = config_path()
    if not cfg_file.exists():
        print(f"Claude Desktop config not found at {cfg_file}.")
        print("Open Claude Desktop once, then run this again.")
        sys.exit(1)

    cfg = json.loads(cfg_file.read_text(encoding="utf-8"))

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = cfg_file.with_suffix(f".backup-{stamp}.json")
    shutil.copy2(cfg_file, backup)

    servers = cfg.setdefault("mcpServers", {})
    servers[KEY] = {
        "command": sys.executable,
        "args": ["-m", "venue_ops_mcp.server"],
        "cwd": str(ROOT),
    }

    cfg_file.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"backup  : {backup.name}")
    print(f"config  : {cfg_file}")
    print(f"added   : mcpServers.{KEY}")
    print(f"  command {sys.executable}")
    print(f"  cwd     {ROOT}")
    print("\nRestart Claude Desktop, then ask it: 'is anything leaking this week?'")


if __name__ == "__main__":
    main()
