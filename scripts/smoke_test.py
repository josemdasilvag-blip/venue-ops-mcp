#!/usr/bin/env python3
"""Exercise every tool and assert the planted anomalies are actually found.

Run:  python scripts/smoke_test.py

This is not a substitute for a test suite. It is the check that stops a broken
demo going out: if the void drift in Barcelona stops being detected, the whole
point of the server is gone and this fails loudly.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from venue_ops_mcp import data  # noqa: E402
from venue_ops_mcp.server import (  # noqa: E402
    AnomalyInput,
    CompareWeekdayInput,
    DayInput,
    ListVenuesInput,
    PeriodInput,
    ResponseFormat,
    venue_ops_compare_weekday,
    venue_ops_find_anomalies,
    venue_ops_get_day,
    venue_ops_list_venues,
    venue_ops_period_summary,
)

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        failures.append(f"{label} — {detail}")
        print(f"  FAIL  {label} — {detail}")


async def main() -> None:
    print("\n== venues ==")
    out = await venue_ops_list_venues(ListVenuesInput())
    print(out)
    check("lists three venues", out.count("seats") == 3, out[:120])

    print("\n== one day ==")
    out = await venue_ops_get_day(DayInput(venue_id="MAD-01", day="2026-08-15"))
    print(out)
    check("day has a control block", "Control block" in out)

    print("\n== same weekday ==")
    out = await venue_ops_compare_weekday(
        CompareWeekdayInput(venue_id="MAD-01", day="2026-08-15", weeks=4)
    )
    print(out)
    check("compares four weeks", "last 4 Saturdays" in out, out[:200])

    print("\n== period summary ==")
    out = await venue_ops_period_summary(
        PeriodInput(start="2026-07-01", end="2026-07-31")
    )
    check("group total present", "Group total" in out, out[:200])
    print(out[:600] + "\n...")

    print("\n== anomalies: the planted void drift in Barcelona ==")
    out = await venue_ops_find_anomalies(
        AnomalyInput(venue_id="BCN-01", end="2026-07-05", response_format=ResponseFormat.JSON)
    )
    payload = json.loads(out)
    drifts = [f for f in payload["findings"] if f["type"] == "drift" and f["metric"] == "void_rate"]
    check("void drift detected at BCN-01", bool(drifts), out[:300])
    if drifts:
        d = drifts[0]
        print(
            f"  -> voids {d['baseline_rate']:.3%} to {d['recent_rate']:.3%}, "
            f"{d['euros_at_stake']:.2f} EUR at stake"
        )
        check("drift is material", d["euros_at_stake"] > 100, str(d))

    print("\n== anomalies: the planted labour blowout in Chamberi ==")
    out = await venue_ops_find_anomalies(
        AnomalyInput(venue_id="MAD-02", end="2026-04-12", response_format=ResponseFormat.JSON)
    )
    payload = json.loads(out)
    labour = [f for f in payload["findings"] if f["metric"] == "labour_pct"]
    check("labour anomaly detected at MAD-02", bool(labour), out[:300])

    print("\n== anomalies: the planted discount spike at Gran Via ==")
    out = await venue_ops_find_anomalies(
        AnomalyInput(venue_id="MAD-01", end="2026-05-12", response_format=ResponseFormat.JSON)
    )
    payload = json.loads(out)
    spikes = [
        f for f in payload["findings"]
        if f["type"] == "spike" and f["metric"] == "discount_rate" and f.get("date") == "2026-05-09"
    ]
    check("discount spike detected on 2026-05-09", bool(spikes), out[:300])

    print("\n== a quiet window should stay quiet ==")
    out = await venue_ops_find_anomalies(
        AnomalyInput(venue_id="MAD-01", end="2026-03-29", response_format=ResponseFormat.JSON)
    )
    quiet = json.loads(out)["findings"]
    check("no false alarms in a clean window", len(quiet) == 0, json.dumps(quiet)[:300])

    print("\n== errors are actionable ==")
    out = await venue_ops_get_day(DayInput(venue_id="NOPE-99", day="2026-08-15"))
    check("unknown venue lists the valid ones", "Available venues" in out, out)
    out = await venue_ops_get_day(DayInput(venue_id="MAD-01", day="2019-01-01"))
    check("out-of-range date names the range", "outside the dataset" in out, out)

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
