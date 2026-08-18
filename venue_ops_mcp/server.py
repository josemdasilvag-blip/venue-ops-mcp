#!/usr/bin/env python3
"""MCP server exposing multi-site venue operations data.

Gives an assistant direct, read-only access to daily trading figures for a
group of restaurants: sales, covers, average ticket, and the control block of
discounts, comped items and voided tickets that is where money leaves without
anyone writing it down.

Transport: stdio. Run with `python -m venue_ops_mcp.server`.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from enum import Enum
from typing import Any, Optional

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import data

mcp = MCPServer("venue_ops_mcp")


def _read_only(title: str) -> ToolAnnotations:
    """Every tool here only reads the bundled dataset. Saying so explicitly
    lets a client decide what it can call without asking permission."""
    return ToolAnnotations(
        title=title,
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )


class ResponseFormat(str, Enum):
    """Output format for tool responses."""

    MARKDOWN = "markdown"
    JSON = "json"


# --- shared helpers ------------------------------------------------------

def _fail(message: str) -> str:
    return f"Error: {message}"


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"'{value}' is not a valid date. Use YYYY-MM-DD, e.g. '2026-08-17'."
        ) from exc


def _check_in_period(day: date) -> None:
    start, end = data.period()
    if not start <= day <= end:
        raise ValueError(
            f"{day.isoformat()} is outside the dataset, which covers "
            f"{start.isoformat()} to {end.isoformat()}."
        )


def _pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def _eur(x: float) -> str:
    return f"{x:,.2f} {data.currency()}"


def _day_lines(row: dict[str, Any]) -> list[str]:
    """One day rendered the way an operator reads it: trade first, then the
    control block, then labour."""
    return [
        f"- Net sales: **{_eur(row['net_sales'])}**",
        f"- Dine-in {_eur(row['dine_in_sales'])} · delivery {_eur(row['delivery_sales'])}"
        f" ({_pct(row['delivery_share'])} delivery)",
        f"- Covers: {row['covers']} · spend per cover {_eur(row['spend_per_cover'])}",
        f"- Transactions: {row['transactions']} · average ticket {_eur(row['average_ticket'])}",
        "",
        "**Control block**",
        f"- Discounts: {_eur(row['discounts'])} ({_pct(row['discount_rate'])} of gross)",
        f"- Comps: {_eur(row['comps'])} ({_pct(row['comp_rate'])} of gross)",
        f"- Voids: {_eur(row['voids'])} ({_pct(row['void_rate'])} of gross)",
        f"- Control total: {_eur(row['control_total'])}",
        "",
        f"**Labour**: {_eur(row['labour_cost'])} ({_pct(row['labour_pct'])} of net)"
        f" · {row['labour_hours']} h",
    ]


def _render(payload: dict[str, Any], markdown: list[str], fmt: ResponseFormat) -> str:
    if fmt == ResponseFormat.JSON:
        return json.dumps(payload, indent=2, ensure_ascii=False)
    return "\n".join(markdown)


# --- input models --------------------------------------------------------

class ListVenuesInput(BaseModel):
    """Input model for listing venues."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for reading, 'json' for processing",
    )


class DayInput(BaseModel):
    """Input model for a single trading day at one venue."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    venue_id: str = Field(
        ...,
        description="Venue identifier, e.g. 'MAD-01'. Use venue_ops_list_venues to see them all.",
        min_length=1,
        max_length=32,
    )
    day: str = Field(
        ..., description="Trading day in YYYY-MM-DD format, e.g. '2026-08-14'"
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)

    @field_validator("day")
    @classmethod
    def _valid_day(cls, v: str) -> str:
        _parse_date(v)
        return v


class CompareWeekdayInput(BaseModel):
    """Input model for comparing a day against the same weekday before it."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    venue_id: str = Field(..., description="Venue identifier, e.g. 'MAD-01'", min_length=1)
    day: str = Field(..., description="Trading day in YYYY-MM-DD format")
    weeks: int = Field(
        default=4,
        description="How many previous weeks of the same weekday to compare against",
        ge=1,
        le=12,
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)

    @field_validator("day")
    @classmethod
    def _valid_day(cls, v: str) -> str:
        _parse_date(v)
        return v


class PeriodInput(BaseModel):
    """Input model for aggregating a date range."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    start: str = Field(..., description="First day of the range, YYYY-MM-DD")
    end: str = Field(..., description="Last day of the range, inclusive, YYYY-MM-DD")
    venue_id: Optional[str] = Field(
        default=None,
        description="Venue to report on. Omit to get every venue plus a group total.",
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


class AnomalyInput(BaseModel):
    """Input model for the anomaly scan."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    venue_id: Optional[str] = Field(
        default=None, description="Venue to scan. Omit to scan every venue."
    )
    end: Optional[str] = Field(
        default=None,
        description="Last day of the window, YYYY-MM-DD. Defaults to the last day in the dataset.",
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


# --- tools ---------------------------------------------------------------

@mcp.tool(
    name="venue_ops_list_venues",
    annotations=_read_only("List Venues and Period"),
)
async def venue_ops_list_venues(params: ListVenuesInput) -> str:
    """List every venue in the dataset and the period the data covers.

    Call this first when you do not know which venue ids exist. It is cheap and
    it tells you the date range every other tool will accept.

    Args:
        params (ListVenuesInput): Validated parameters containing:
            - response_format (ResponseFormat): 'markdown' or 'json'

    Returns:
        str: Venue list and covered period.

        JSON schema:
        {
            "currency": str,               # e.g. "EUR"
            "period": {"start": str, "end": str},
            "venues": [
                {"venue_id": str, "name": str, "city": str, "seats": int}
            ]
        }

    Examples:
        - Use when: "Which sites do we have data for?"
        - Don't use when: You already have a venue id and want figures
          (use venue_ops_get_day).
    """
    try:
        start, end = data.period()
        payload = {
            "currency": data.currency(),
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "venues": data.venues(),
        }
        lines = [
            "# Venues",
            "",
            f"Data covers **{start.isoformat()}** to **{end.isoformat()}** "
            f"({(end - start).days + 1} days), in {data.currency()}.",
            "",
        ]
        for v in data.venues():
            lines.append(
                f"- **{v['venue_id']}** — {v['name']} ({v['city']}), {v['seats']} seats"
            )
        return _render(payload, lines, params.response_format)
    except data.DatasetError as exc:
        return _fail(str(exc))


@mcp.tool(
    name="venue_ops_get_day",
    annotations=_read_only("Get One Trading Day"),
)
async def venue_ops_get_day(params: DayInput) -> str:
    """Return the full trading figures for one venue on one day.

    Gives sales split by channel, covers, average ticket, the control block
    (discounts, comps, voids) and labour cost, with every rate expressed
    against gross so the percentages are comparable across venues.

    Args:
        params (DayInput): Validated parameters containing:
            - venue_id (str): Venue identifier, e.g. 'MAD-01'
            - day (str): Trading day, YYYY-MM-DD
            - response_format (ResponseFormat): 'markdown' or 'json'

    Returns:
        str: The day's figures.

        JSON keys include: net_sales, dine_in_sales, delivery_sales, covers,
        transactions, average_ticket, spend_per_cover, discounts, comps, voids,
        control_total, gross_sales, discount_rate, comp_rate, void_rate,
        labour_cost, labour_hours, labour_pct (float rates are fractions of 1).

        Error response: "Error: <message>" naming the valid venues or dates.

    Examples:
        - Use when: "How did Gran Via do on 14 August?"
        - Don't use when: You want to know whether that day was normal
          (use venue_ops_compare_weekday).
    """
    try:
        venue = data.resolve_venue(params.venue_id)
        day = _parse_date(params.day)
        _check_in_period(day)
        row = data.get_day(venue, day)
        if row is None:
            return _fail(f"No data for {venue} on {day.isoformat()}.")

        lines = [
            f"# {data.venue_name(venue)} ({venue}) — {row['weekday']} {row['date']}",
            "",
            *_day_lines(row),
        ]
        return _render(row, lines, params.response_format)
    except (KeyError, ValueError) as exc:
        return _fail(str(exc).strip("'"))
    except data.DatasetError as exc:
        return _fail(str(exc))


@mcp.tool(
    name="venue_ops_compare_weekday",
    annotations=_read_only("Compare Against the Same Weekday"),
)
async def venue_ops_compare_weekday(params: CompareWeekdayInput) -> str:
    """Compare a day against the same weekday in previous weeks.

    This is the only fair comparison in hospitality. Measuring a Monday against
    the Sunday before it makes every Monday look like a collapse and every
    Friday like a triumph; comparing like weekday with like weekday is what
    tells you whether something actually changed.

    Args:
        params (CompareWeekdayInput): Validated parameters containing:
            - venue_id (str): Venue identifier
            - day (str): The day being judged, YYYY-MM-DD
            - weeks (int): How many previous same-weekdays to average, 1-12 (default 4)
            - response_format (ResponseFormat): 'markdown' or 'json'

    Returns:
        str: The day, the same-weekday average, and the variance between them.

        JSON schema:
        {
            "venue_id": str,
            "day": {...},                    # same shape as venue_ops_get_day
            "comparison_days": [str],        # dates averaged
            "averages": {"net_sales": float, "covers": float,
                         "average_ticket": float, "labour_pct": float,
                         "discount_rate": float, "void_rate": float},
            "variance": {...}                # same keys, as fractions of 1
        }

    Examples:
        - Use when: "Was last Saturday actually bad, or does it just feel bad?"
        - Don't use when: You want a whole month's totals
          (use venue_ops_period_summary).
    """
    try:
        venue = data.resolve_venue(params.venue_id)
        day = _parse_date(params.day)
        _check_in_period(day)
        row = data.get_day(venue, day)
        if row is None:
            return _fail(f"No data for {venue} on {day.isoformat()}.")

        history = data.same_weekday_before(venue, day, params.weeks)
        if not history:
            return _fail(
                f"No earlier {day.strftime('%A')}s available before {day.isoformat()} "
                "inside the dataset. Try a later date or fewer weeks."
            )

        keys = (
            "net_sales",
            "covers",
            "average_ticket",
            "labour_pct",
            "discount_rate",
            "void_rate",
        )
        averages = {k: sum(h[k] for h in history) / len(history) for k in keys}
        variance = {
            k: (row[k] - averages[k]) / averages[k] if averages[k] else 0.0 for k in keys
        }

        payload = {
            "venue_id": venue,
            "day": row,
            "comparison_days": [h["date"] for h in history],
            "averages": {k: round(v, 5) for k, v in averages.items()},
            "variance": {k: round(v, 4) for k, v in variance.items()},
        }

        def arrow(v: float) -> str:
            return "up" if v > 0 else "down"

        lines = [
            f"# {data.venue_name(venue)} ({venue}) — {row['weekday']} {row['date']}",
            "",
            f"Compared against the last {len(history)} {row['weekday']}s "
            f"({', '.join(h['date'] for h in history)}).",
            "",
            f"- Net sales {_eur(row['net_sales'])} vs {_eur(averages['net_sales'])} "
            f"average — **{arrow(variance['net_sales'])} {_pct(abs(variance['net_sales']))}**",
            f"- Covers {row['covers']} vs {averages['covers']:.0f} — "
            f"{arrow(variance['covers'])} {_pct(abs(variance['covers']))}",
            f"- Average ticket {_eur(row['average_ticket'])} vs {_eur(averages['average_ticket'])} — "
            f"{arrow(variance['average_ticket'])} {_pct(abs(variance['average_ticket']))}",
            f"- Labour {_pct(row['labour_pct'])} vs {_pct(averages['labour_pct'])}",
            f"- Discounts {_pct(row['discount_rate'])} vs {_pct(averages['discount_rate'])}",
            f"- Voids {_pct(row['void_rate'])} vs {_pct(averages['void_rate'])}",
        ]
        return _render(payload, lines, params.response_format)
    except (KeyError, ValueError) as exc:
        return _fail(str(exc).strip("'"))
    except data.DatasetError as exc:
        return _fail(str(exc))


@mcp.tool(
    name="venue_ops_period_summary",
    annotations=_read_only("Summarise a Date Range"),
)
async def venue_ops_period_summary(params: PeriodInput) -> str:
    """Total up a date range, for one venue or for the whole group.

    Args:
        params (PeriodInput): Validated parameters containing:
            - start (str): First day, YYYY-MM-DD
            - end (str): Last day inclusive, YYYY-MM-DD
            - venue_id (Optional[str]): One venue, or omit for all venues plus a total
            - response_format (ResponseFormat): 'markdown' or 'json'

    Returns:
        str: Totals and derived rates for the range.

        JSON schema:
        {
            "period": {"start": str, "end": str},
            "venues": {venue_id: {...aggregated figures...}},
            "group_total": {...}     # present only when venue_id was omitted
        }

    Examples:
        - Use when: "What did the group do in July?"
        - Use when: "Give me Chamberi's numbers for the last two weeks."
        - Don't use when: You want a single day (use venue_ops_get_day).
    """
    try:
        start = _parse_date(params.start)
        end = _parse_date(params.end)
        if start > end:
            return _fail(
                f"start ({params.start}) is after end ({params.end}). Swap them."
            )
        _check_in_period(start)
        _check_in_period(end)

        targets = (
            [data.resolve_venue(params.venue_id)] if params.venue_id else data.venue_ids()
        )

        per_venue: dict[str, Any] = {}
        for vid in targets:
            rows = data.range_rows(vid, start, end)
            if rows:
                per_venue[vid] = data.aggregate(rows)

        if not per_venue:
            return _fail(
                f"No data between {start.isoformat()} and {end.isoformat()}."
            )

        payload: dict[str, Any] = {
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "venues": per_venue,
        }

        lines = [
            f"# {start.isoformat()} to {end.isoformat()}",
            "",
        ]
        for vid, agg in per_venue.items():
            lines += [
                f"## {data.venue_name(vid)} ({vid}) — {agg['days']} days",
                "",
                *_day_lines(agg),
                "",
            ]

        if not params.venue_id and len(per_venue) > 1:
            all_rows = data.range_rows(None, start, end)
            total = data.aggregate(all_rows)
            payload["group_total"] = total
            lines += ["## Group total", "", *_day_lines(total)]

        return _render(payload, lines, params.response_format)
    except (KeyError, ValueError) as exc:
        return _fail(str(exc).strip("'"))
    except data.DatasetError as exc:
        return _fail(str(exc))


@mcp.tool(
    name="venue_ops_find_anomalies",
    annotations=_read_only("Flag Control-Block Anomalies"),
)
async def venue_ops_find_anomalies(params: AnomalyInput) -> str:
    """Flag discounts, comps, voids or labour moving against their own baseline.

    Compares the last 7 days against the 28 before them and reports two shapes.
    A *drift* is a sustained rise across the whole recent window, which is what
    a till problem or an unfixed rota looks like. A *spike* is one day far
    outside the baseline, usually a promotion nobody logged. Findings below
    40 EUR are dropped: they are real but not worth a phone call.

    Args:
        params (AnomalyInput): Validated parameters containing:
            - venue_id (Optional[str]): One venue, or omit to scan all
            - end (Optional[str]): Last day of the window, defaults to the dataset end
            - response_format (ResponseFormat): 'markdown' or 'json'

    Returns:
        str: Findings sorted by money at stake, largest first.

        JSON schema:
        {
            "window_end": str,
            "findings": [
                {
                    "type": "drift" | "spike",
                    "metric": str,           # discount_rate | comp_rate | void_rate | labour_pct
                    "label": str,            # human wording, e.g. "voided tickets"
                    "venue_id": str,
                    "baseline_rate": float,  # fraction of 1
                    "euros_at_stake": float,
                    # drift only: window, baseline_window, recent_rate, relative_increase
                    # spike only: date, weekday, day_rate, z_score
                }
            ]
        }

    Examples:
        - Use when: "Is anything leaking this week?"
        - Use when: "Check Barcelona for problems ending 5 July."
        - Don't use when: You want the day's trading figures (use venue_ops_get_day).
    """
    try:
        _, dataset_end = data.period()
        end = _parse_date(params.end) if params.end else dataset_end
        _check_in_period(end)

        targets = (
            [data.resolve_venue(params.venue_id)] if params.venue_id else data.venue_ids()
        )

        findings: list[dict[str, Any]] = []
        for vid in targets:
            findings += data.find_anomalies(vid, end)
        findings.sort(key=lambda f: abs(f["euros_at_stake"]), reverse=True)

        payload = {"window_end": end.isoformat(), "findings": findings}

        if not findings:
            lines = [
                f"# Anomaly scan to {end.isoformat()}",
                "",
                "Nothing above the reporting threshold. The control block is "
                "moving inside its own baseline for every venue scanned.",
            ]
            return _render(payload, lines, params.response_format)

        lines = [
            f"# Anomaly scan to {end.isoformat()}",
            "",
            f"{len(findings)} finding(s), largest first.",
            "",
        ]
        for f in findings:
            site = f"{data.venue_name(f['venue_id'])} ({f['venue_id']})"
            if f["type"] == "drift":
                lines += [
                    f"## Drift — {f['label']} at {site}",
                    f"- Window {f['window']} against baseline {f['baseline_window']}",
                    f"- Rate {_pct(f['baseline_rate'])} to {_pct(f['recent_rate'])} "
                    f"(+{_pct(f['relative_increase'])} relative)",
                    f"- **{_eur(f['euros_at_stake'])} at stake over the window**",
                    "",
                ]
            else:
                lines += [
                    f"## Spike — {f['label']} at {site}",
                    f"- {f['weekday']} {f['date']}: {_pct(f['day_rate'])} "
                    f"against a {_pct(f['baseline_rate'])} baseline (z={f['z_score']})",
                    f"- **{_eur(f['euros_at_stake'])} on the day**",
                    "",
                ]
        return _render(payload, lines, params.response_format)
    except (KeyError, ValueError) as exc:
        return _fail(str(exc).strip("'"))
    except data.DatasetError as exc:
        return _fail(str(exc))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
