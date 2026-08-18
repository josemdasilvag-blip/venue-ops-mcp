#!/usr/bin/env python3
"""Speak JSON-RPC to the server over stdio, the way a real client does.

Run:  python scripts/stdio_check.py

The smoke test calls the tool functions directly, which proves the maths but
not the wiring. This launches the server as a subprocess, performs the MCP
handshake and asks for the tool list, which is exactly what Claude Desktop
does when it starts.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def rpc(msg: dict) -> str:
    return json.dumps(msg) + "\n"


def main() -> None:
    proc = subprocess.Popen(
        [sys.executable, "-m", "venue_ops_mcp.server"],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )

    handshake = (
        rpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "stdio_check", "version": "0"},
                },
            }
        )
        + rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
        + rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    )

    try:
        out, err = proc.communicate(handshake, timeout=25)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()

    tools: list[str] = []
    server_name = ""
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        result = msg.get("result", {})
        if "serverInfo" in result:
            server_name = result["serverInfo"].get("name", "")
        if "tools" in result:
            tools = [t["name"] for t in result["tools"]]

    print(f"server name : {server_name or '(none)'}")
    print(f"tools       : {len(tools)}")
    for t in sorted(tools):
        print(f"  - {t}")

    if err.strip():
        print("\nstderr:")
        print(err.strip()[:800])

    expected = {
        "venue_ops_list_venues",
        "venue_ops_get_day",
        "venue_ops_compare_weekday",
        "venue_ops_period_summary",
        "venue_ops_find_anomalies",
    }
    missing = expected - set(tools)
    if server_name != "venue_ops_mcp" or missing:
        print(f"\nFAILED. missing tools: {sorted(missing) or 'none'}")
        sys.exit(1)
    print("\nHandshake OK, all five tools advertised.")


if __name__ == "__main__":
    main()
