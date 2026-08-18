#!/usr/bin/env python3
"""Generate the synthetic dataset bundled with venue-ops-mcp.

Run:  python scripts/generate_data.py

The numbers are invented, but the *shape* is not. Weekday seasonality, the
ratio between dine-in and delivery, discount and void rates and labour
percentage all sit inside the bands you actually see in casual dining. Three
anomalies are planted on purpose so the anomaly tool has something real to
find; they are documented in README.md so nobody mistakes a demo for a
discovery.

Deterministic: same seed, same file, so evaluations stay stable.
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

SEED = 20260818
DAYS = 182  # 26 weeks
END = date(2026, 8, 17)  # last full day in the dataset
START = END - timedelta(days=DAYS - 1)

OUT = Path(__file__).resolve().parents[1] / "data" / "operations.json"

# Monday=0 ... Sunday=6. Multipliers against the venue's baseline day.
WEEKDAY_CURVE = [0.62, 0.68, 0.78, 0.94, 1.38, 1.62, 1.10]

VENUES = [
    {
        "venue_id": "MAD-01",
        "name": "Gran Via",
        "city": "Madrid",
        "seats": 120,
        "baseline_net_sales": 4200.0,
        "delivery_share": 0.18,
        "spend_per_cover": 21.5,
        "labour_pct": 0.295,
    },
    {
        "venue_id": "MAD-02",
        "name": "Chamberi",
        "city": "Madrid",
        "seats": 74,
        "baseline_net_sales": 2600.0,
        "delivery_share": 0.31,
        "spend_per_cover": 18.0,
        "labour_pct": 0.312,
    },
    {
        "venue_id": "BCN-01",
        "name": "Eixample",
        "city": "Barcelona",
        "seats": 96,
        "baseline_net_sales": 3350.0,
        "delivery_share": 0.24,
        "spend_per_cover": 19.8,
        "labour_pct": 0.288,
    },
]

# --- planted anomalies, documented in the README -------------------------
# 1. BCN-01 void rate drifts upward over three weeks and then stops. This is
#    the shape a till problem makes: not a spike, a slope.
VOID_DRIFT_VENUE = "BCN-01"
VOID_DRIFT_START = date(2026, 6, 15)
VOID_DRIFT_END = date(2026, 7, 5)
VOID_DRIFT_PEAK = 0.052  # from a ~0.9% baseline

# 2. MAD-02 labour percentage blows out for one week (a rota gone wrong).
LABOUR_SPIKE_VENUE = "MAD-02"
LABOUR_SPIKE_START = date(2026, 4, 6)
LABOUR_SPIKE_END = date(2026, 4, 12)
LABOUR_SPIKE_PCT = 0.41

# 3. MAD-01 has two isolated discount days (a promotion nobody logged).
DISCOUNT_DAYS = {
    "MAD-01": [date(2026, 5, 9), date(2026, 7, 18)],
}
DISCOUNT_DAY_RATE = 0.14


def _money(x: float) -> float:
    return round(x, 2)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def build() -> dict:
    rng = random.Random(SEED)
    rows: list[dict] = []

    for venue in VENUES:
        # Slow organic growth across the period, plus per-venue noise.
        for i in range(DAYS):
            day = START + timedelta(days=i)
            wd = day.weekday()

            trend = _lerp(0.94, 1.07, i / (DAYS - 1))
            noise = rng.gauss(1.0, 0.075)
            net_sales = venue["baseline_net_sales"] * WEEKDAY_CURVE[wd] * trend * noise
            net_sales = max(net_sales, 250.0)

            delivery_share = min(
                0.6, max(0.05, venue["delivery_share"] + rng.gauss(0.0, 0.03))
            )
            delivery_sales = net_sales * delivery_share
            dine_in_sales = net_sales - delivery_sales

            spend_per_cover = venue["spend_per_cover"] * rng.gauss(1.0, 0.05)
            covers = max(1, round(dine_in_sales / max(spend_per_cover, 1.0)))
            # Delivery orders are separate transactions, smaller basket.
            delivery_orders = max(0, round(delivery_sales / (spend_per_cover * 1.35)))
            transactions = max(1, round(covers / 1.9) + delivery_orders)

            # --- control block ---------------------------------------------
            discount_rate = abs(rng.gauss(0.028, 0.009))
            if day in DISCOUNT_DAYS.get(venue["venue_id"], []):
                discount_rate = DISCOUNT_DAY_RATE

            comp_rate = abs(rng.gauss(0.005, 0.002))

            void_rate = abs(rng.gauss(0.009, 0.003))
            if (
                venue["venue_id"] == VOID_DRIFT_VENUE
                and VOID_DRIFT_START <= day <= VOID_DRIFT_END
            ):
                span = (VOID_DRIFT_END - VOID_DRIFT_START).days
                t = (day - VOID_DRIFT_START).days / max(span, 1)
                void_rate = _lerp(0.011, VOID_DRIFT_PEAK, t) * rng.gauss(1.0, 0.08)

            gross = net_sales / (1 - discount_rate - comp_rate - void_rate)
            discounts = gross * discount_rate
            comps = gross * comp_rate
            voids = gross * void_rate

            # --- labour ----------------------------------------------------
            labour_pct = venue["labour_pct"] * rng.gauss(1.0, 0.06)
            if (
                venue["venue_id"] == LABOUR_SPIKE_VENUE
                and LABOUR_SPIKE_START <= day <= LABOUR_SPIKE_END
            ):
                labour_pct = LABOUR_SPIKE_PCT * rng.gauss(1.0, 0.03)
            labour_cost = net_sales * labour_pct
            labour_hours = labour_cost / rng.gauss(12.4, 0.4)

            rows.append(
                {
                    "venue_id": venue["venue_id"],
                    "date": day.isoformat(),
                    "weekday": day.strftime("%A"),
                    "net_sales": _money(net_sales),
                    "dine_in_sales": _money(dine_in_sales),
                    "delivery_sales": _money(delivery_sales),
                    "covers": covers,
                    "transactions": transactions,
                    "discounts": _money(discounts),
                    "comps": _money(comps),
                    "voids": _money(voids),
                    "labour_cost": _money(labour_cost),
                    "labour_hours": round(labour_hours, 1),
                }
            )

    rows.sort(key=lambda r: (r["date"], r["venue_id"]))
    return {
        "generated_by": "scripts/generate_data.py",
        "seed": SEED,
        "currency": "EUR",
        "note": "Synthetic demonstration data. Not a real business.",
        "period": {"start": START.isoformat(), "end": END.isoformat()},
        "venues": [
            {k: v for k, v in ven.items() if k in ("venue_id", "name", "city", "seats")}
            for ven in VENUES
        ],
        "days": rows,
    }


def main() -> None:
    payload = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"{len(payload['days'])} rows -> {OUT}")
    print(f"period {payload['period']['start']} .. {payload['period']['end']}")


if __name__ == "__main__":
    main()
