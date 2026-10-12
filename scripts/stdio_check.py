#!/usr/bin/env python3
"""Check the MCP handshake and tool discovery over a real stdio subprocess.

Run: python scripts/stdio_check.py

The smoke test calls functions directly. This script exercises the protocol
boundary used by MCP clients, including the ordered initialization exchange.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def rpc(message: dict) -> bytes:
    return (json.dumps(message) + "\n").encode("utf-8")


async def response_for(process: asyncio.subprocess.Process, request_id: int) -> dict:
    assert process.stdout is not None
    while True:
        line = await asyncio.wait_for(process.stdout.readline(), timeout=25)
        if not line:
            raise RuntimeError(f"Server closed stdout before response {request_id}")
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("id") != request_id:
            continue
        if "error" in message:
            raise RuntimeError(f"JSON-RPC error for {request_id}: {message['error']}")
        return message.get("result", {})


async def main() -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "venue_ops_mcp.server",
        cwd=ROOT,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stderr is not None

    try:
        process.stdin.write(
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
        )
        await process.stdin.drain()
        initialized = await response_for(process, 1)

        process.stdin.write(rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        process.stdin.write(rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))
        await process.stdin.drain()
        listed = await response_for(process, 2)
    finally:
        process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    server_name = initialized.get("serverInfo", {}).get("name", "")
    tools = [item["name"] for item in listed.get("tools", [])]
    print(f"server name : {server_name or '(none)'}")
    print(f"tools       : {len(tools)}")
    for tool in sorted(tools):
        print(f"  - {tool}")

    expected = {
        "venue_ops_list_venues",
        "venue_ops_get_day",
        "venue_ops_compare_weekday",
        "venue_ops_period_summary",
        "venue_ops_find_anomalies",
    }
    missing = expected - set(tools)
    if server_name != "venue_ops_mcp" or missing:
        error_output = (await process.stderr.read()).decode("utf-8", errors="replace")
        if error_output.strip():
            print("\nstderr:\n" + error_output[:800])
        print(f"\nFAILED. missing tools: {sorted(missing) or 'none'}")
        sys.exit(1)
    print("\nHandshake OK, all five tools advertised.")


if __name__ == "__main__":
    asyncio.run(main())
