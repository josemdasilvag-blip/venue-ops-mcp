"""Dataset access and the operational maths behind the tools.

Everything the server exposes is computed here so the tool layer stays thin
and the thresholds live in one place. The thresholds are the opinionated part:
they come from running venues, not from a statistics textbook, and they are
stated as constants so anyone can argue with them.
"""

from __future__ import annotations

import json
import statistics
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "operations.json"

# --- thresholds ----------------------------------------------------------
# A single day above baseline is noise. What matters is a run of them, which
# is why the drift check compares a recent window against a longer one.
DRIFT_RECENT_DAYS = 7
DRIFT_BASELINE_DAYS = 28
DRIFT_MIN_RELATIVE_INCREASE = 0.5  # +50% against the trailing baseline
SPIKE_Z_SCORE = 2.5

# Below these the money involved is too small to be worth a phone call.
MIN_MATERIAL_EUR = 40.0

CONTROL_METRICS = ("discount_rate", "comp_rate", "void_rate")
WATCHED_METRICS = CONTROL_METRICS + ("labour_pct",)

METRIC_LABELS = {
    "discount_rate": "discounts",
    "comp_rate": "comped items",
    "void_rate": "voided tickets",
    "labour_pct": "labour cost",
}


class DatasetError(RuntimeError):
    """Raised when the bundled dataset is missing or unusable."""


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    if not DATA_FILE.exists():
        raise DatasetError(
            f"Dataset not found at {DATA_FILE}. "
            "Run 'python scripts/generate_data.py' to build it."
        )
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - corrupted file
        raise DatasetError(f"Dataset at {DATA_FILE} is not valid JSON: {exc}") from exc


def venues() -> list[dict[str, Any]]:
    return _load()["venues"]


def venue_ids() -> list[str]:
    return [v["venue_id"] for v in venues()]


def period() -> tuple[date, date]:
    p = _load()["period"]
    return date.fromisoformat(p["start"]), date.fromisoformat(p["end"])


def currency() -> str:
    return _load().get("currency", "EUR")


def _rows() -> list[dict[str, Any]]:
    return _load()["days"]


def resolve_venue(venue_id: str) -> str:
    """Return the canonical venue id, or raise with the valid options listed."""
    for vid in venue_ids():
        if vid.lower() == venue_id.lower():
            return vid
    raise KeyError(
        f"Unknown venue '{venue_id}'. Available venues: {', '.join(venue_ids())}."
    )


def venue_name(venue_id: str) -> str:
    for v in venues():
        if v["venue_id"] == venue_id:
            return v["name"]
    return venue_id


# --- derived metrics -----------------------------------------------------

def enrich(row: dict[str, Any]) -> dict[str, Any]:
    """Add the derived figures. Gross is net plus everything taken off it."""
    net = row["net_sales"]
    control_total = row["discounts"] + row["comps"] + row["voids"]
    gross = net + control_total

    out = dict(row)
    out["gross_sales"] = round(gross, 2)
    out["control_total"] = round(control_total, 2)
    out["average_ticket"] = round(net / row["transactions"], 2) if row["transactions"] else 0.0
    out["spend_per_cover"] = round(row["dine_in_sales"] / row["covers"], 2) if row["covers"] else 0.0
    out["discount_rate"] = round(row["discounts"] / gross, 5) if gross else 0.0
    out["comp_rate"] = round(row["comps"] / gross, 5) if gross else 0.0
    out["void_rate"] = round(row["voids"] / gross, 5) if gross else 0.0
    out["labour_pct"] = round(row["labour_cost"] / net, 5) if net else 0.0
    out["delivery_share"] = round(row["delivery_sales"] / net, 5) if net else 0.0
    return out


def get_day(venue_id: str, day: date) -> dict[str, Any] | None:
    iso = day.isoformat()
    for row in _rows():
        if row["venue_id"] == venue_id and row["date"] == iso:
            return enrich(row)
    return None


def range_rows(
    venue_id: str | None, start: date, end: date
) -> list[dict[str, Any]]:
    lo, hi = start.isoformat(), end.isoformat()
    return [
        enrich(r)
        for r in _rows()
        if lo <= r["date"] <= hi and (venue_id is None or r["venue_id"] == venue_id)
    ]


def same_weekday_before(
    venue_id: str, day: date, weeks: int
) -> list[dict[str, Any]]:
    """The same weekday for the N weeks before `day`, most recent first."""
    out = []
    for w in range(1, weeks + 1):
        row = get_day(venue_id, day - timedelta(weeks=w))
        if row:
            out.append(row)
    return out


# --- aggregation ---------------------------------------------------------

_SUMMABLE = (
    "net_sales",
    "dine_in_sales",
    "delivery_sales",
    "covers",
    "transactions",
    "discounts",
    "comps",
    "voids",
    "labour_cost",
    "labour_hours",
)


def aggregate(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    if not rows:
        return {}
    total: dict[str, Any] = {k: round(sum(r[k] for r in rows), 2) for k in _SUMMABLE}
    total["days"] = len(rows)
    return enrich(
        {
            **total,
            "venue_id": rows[0]["venue_id"] if len({r["venue_id"] for r in rows}) == 1 else "ALL",
            "date": f"{rows[0]['date']}..{rows[-1]['date']}",
            "weekday": "",
        }
    ) | {"days": len(rows)}


# --- anomaly detection ---------------------------------------------------

def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _stdev(values: list[float]) -> float:
    return statistics.pstdev(values) if len(values) > 1 else 0.0


def _euros_at_stake(metric: str, rate_delta: float, rows: list[dict[str, Any]]) -> float:
    """Translate a rate deviation into money, which is the only unit that
    makes someone act."""
    base = sum(r["gross_sales"] if metric != "labour_pct" else r["net_sales"] for r in rows)
    return round(rate_delta * base, 2)


def find_anomalies(
    venue_id: str,
    end: date,
    metrics: tuple[str, ...] = WATCHED_METRICS,
) -> list[dict[str, Any]]:
    """Compare the last week against the previous four for each metric.

    Two shapes are reported. A *drift* is a sustained rise across the recent
    window; that is what a till problem or a rota that never got fixed looks
    like. A *spike* is one day far outside the baseline, which is usually a
    promotion nobody logged.
    """
    recent_start = end - timedelta(days=DRIFT_RECENT_DAYS - 1)
    base_end = recent_start - timedelta(days=1)
    base_start = base_end - timedelta(days=DRIFT_BASELINE_DAYS - 1)

    recent = range_rows(venue_id, recent_start, end)
    baseline = range_rows(venue_id, base_start, base_end)
    if not recent or not baseline:
        return []

    findings: list[dict[str, Any]] = []

    for metric in metrics:
        base_values = [r[metric] for r in baseline]
        recent_values = [r[metric] for r in recent]
        base_mean, base_sd = _mean(base_values), _stdev(base_values)
        recent_mean = _mean(recent_values)
        if base_mean <= 0:
            continue

        # Drift: the whole recent window has moved.
        relative = (recent_mean - base_mean) / base_mean
        if relative >= DRIFT_MIN_RELATIVE_INCREASE:
            money = _euros_at_stake(metric, recent_mean - base_mean, recent)
            if abs(money) >= MIN_MATERIAL_EUR:
                findings.append(
                    {
                        "type": "drift",
                        "metric": metric,
                        "label": METRIC_LABELS[metric],
                        "venue_id": venue_id,
                        "window": f"{recent_start.isoformat()}..{end.isoformat()}",
                        "baseline_window": f"{base_start.isoformat()}..{base_end.isoformat()}",
                        "baseline_rate": round(base_mean, 5),
                        "recent_rate": round(recent_mean, 5),
                        "relative_increase": round(relative, 4),
                        "euros_at_stake": money,
                    }
                )

        # Spikes: individual days far outside the baseline.
        if base_sd > 0:
            for row in recent:
                z = (row[metric] - base_mean) / base_sd
                if z >= SPIKE_Z_SCORE:
                    money = _euros_at_stake(metric, row[metric] - base_mean, [row])
                    if abs(money) >= MIN_MATERIAL_EUR:
                        findings.append(
                            {
                                "type": "spike",
                                "metric": metric,
                                "label": METRIC_LABELS[metric],
                                "venue_id": venue_id,
                                "date": row["date"],
                                "weekday": row["weekday"],
                                "baseline_rate": round(base_mean, 5),
                                "day_rate": round(row[metric], 5),
                                "z_score": round(z, 2),
                                "euros_at_stake": money,
                            }
                        )

    findings.sort(key=lambda f: abs(f["euros_at_stake"]), reverse=True)
    return findings
