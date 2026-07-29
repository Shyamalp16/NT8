"""Build the bounded Data Analytics report artifact for Phase 5."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def build_phase5_artifact(
    manifest_path: Path = ROOT / "data" / "manifests" / "phase5_manifest.json",
    market_clock_path: Path = ROOT / "results" / "phase5" / "market_clock.parquet",
    clock_blocks_path: Path = ROOT / "results" / "phase5" / "report_clock_blocks.parquet",
    stability_path: Path = ROOT / "results" / "phase5" / "market_clock_stability.parquet",
    turning_path: Path = ROOT / "results" / "phase5" / "turning_point_timing.parquet",
) -> dict[str, Any]:
    """Return the canonical report manifest and bounded snapshot."""
    phase5 = json.loads(manifest_path.read_text(encoding="utf-8"))
    market_clock = pd.read_parquet(market_clock_path)
    clock_blocks = pd.read_parquet(clock_blocks_path)
    stability = pd.read_parquet(stability_path)
    turning = pd.read_parquet(turning_path)

    rth_blocks = clock_blocks.loc[clock_blocks["scope"] == "rth"].copy()
    rth_blocks["interval"] = (
        rth_blocks["clock_label"] + "-" + rth_blocks["end_clock_label"]
    )
    rth_blocks = rth_blocks[
        [
            "interval",
            "clock_minute",
            "observations",
            "mean_return_bps",
            "median_return_bps",
            "mean_return_ci_low",
            "mean_return_ci_high",
            "positive_rate",
            "mean_mfe_bps",
            "mean_mae_bps",
        ]
    ].sort_values("clock_minute", kind="mergesort")

    rth_five = market_clock.loc[
        (market_clock["scope"] == "rth")
        & (market_clock["horizon_minutes"] == 5)
    ].copy()
    rth_five = rth_five[
        [
            "clock_label",
            "clock_minute",
            "observations",
            "mean_return_bps",
            "median_return_bps",
            "mean_return_ci_low",
            "mean_return_ci_high",
            "positive_rate",
            "mean_mfe_bps",
            "mean_mae_bps",
        ]
    ].sort_values("clock_minute", kind="mergesort")

    rth_turning = turning.loc[turning["scope"] == "rth"].copy()
    rth_turning["extreme"] = rth_turning["extreme_type"].map(
        {"high": "Session high", "low": "Session low"}
    )
    rth_turning = rth_turning[
        [
            "bin_label",
            "bin_start_minute",
            "extreme",
            "observations",
            "count",
            "share",
            "share_ci_low",
            "share_ci_high",
        ]
    ].sort_values(["bin_start_minute", "extreme"], kind="mergesort")

    strongest = rth_blocks.loc[rth_blocks["mean_return_bps"].idxmax()]
    strongest_minute = int(strongest["clock_minute"])
    strongest_years = stability.loc[
        (stability["scope"] == "rth")
        & (stability["horizon_minutes"] == 30)
        & (stability["clock_minute"] == strongest_minute)
        & (stability["cut_type"] == "calendar_year")
    ].copy()
    strongest_years = strongest_years[
        [
            "cut_value",
            "observations",
            "mean_return_bps",
            "median_return_bps",
            "mean_return_ci_low",
            "mean_return_ci_high",
            "positive_rate",
            "mean_mfe_bps",
            "mean_mae_bps",
        ]
    ].sort_values("cut_value", kind="mergesort")

    findings = phase5["findings"]
    high_bin = findings["rth_modal_high_bin"]
    low_bin = findings["rth_modal_low_bin"]
    year_cis_cross_zero = bool(
        (
            (strongest_years["mean_return_ci_low"] <= 0)
            & (strongest_years["mean_return_ci_high"] >= 0)
        ).all()
    )
    headline = [
        {
            "development_rows": phase5["coverage"]["development_rows"],
            "development_sessions": phase5["coverage"]["development_sessions"],
            "rth_sessions": phase5["coverage"]["rth_sessions"],
            "market_clock_cells": phase5["coverage"]["market_clock_cells"],
            "stability_cells": phase5["coverage"]["stability_cells"],
            "validation_rows_used": 0,
            "final_test_rows_used": 0,
            "strongest_30m_mean_bps": float(strongest["mean_return_bps"]),
            "strongest_30m_ci_low": float(strongest["mean_return_ci_low"]),
            "strongest_30m_ci_high": float(strongest["mean_return_ci_high"]),
        }
    ]

    sources = _sources(strongest_minute)
    title = "MNQ Phase 5 Unconditional Time Analysis"
    manifest = {
        "version": 1,
        "surface": "report",
        "title": title,
        "description": (
            "Development-only descriptive market-clock analysis for MNQ, with "
            "uncertainty, stability cuts, and session-extreme timing."
        ),
        "blocks": [
            {
                "id": "title",
                "type": "markdown",
                "body": f"# {title}",
            },
            {
                "id": "technical_summary",
                "type": "markdown",
                "sourceId": "phase5_manifest",
                "body": (
                    "## Technical summary\n\n"
                    "The Phase 5 gate **passed** on **1.02M development bars** "
                    f"across **{phase5['coverage']['development_sessions']:,} "
                    "Globex sessions**. The pipeline produced "
                    f"**{phase5['coverage']['market_clock_cells']:,} "
                    "clock/horizon cells** and "
                    f"**{phase5['coverage']['stability_cells']:,} stability "
                    "cuts**. Validation and final-test rows used: **zero**. "
                    "This is descriptive research, not a strategy or a "
                    "tradability claim."
                ),
            },
            {
                "id": "headline_metrics",
                "type": "metric-strip",
                "cardIds": [
                    "development_bars",
                    "clock_cells",
                    "locked_rows",
                    "largest_30m_mean",
                ],
            },
            {
                "id": "rth_block_finding",
                "type": "markdown",
                "sourceId": "clock_blocks",
                "body": (
                    "## The late-afternoon block is the clearest descriptive "
                    "RTH feature\n\n"
                    f"Among the 13 non-overlapping 30-minute RTH blocks, "
                    f"**{findings['rth_strongest_30m_block']['start']}-"
                    f"{findings['rth_strongest_30m_block']['end']}** had the "
                    f"largest mean at **{strongest['mean_return_bps']:.2f} "
                    "bps** (95% CI "
                    f"{strongest['mean_return_ci_low']:.2f} to "
                    f"{strongest['mean_return_ci_high']:.2f}; "
                    f"n={int(strongest['observations']):,}). The "
                    f"{findings['rth_weakest_30m_block']['start']}-"
                    f"{findings['rth_weakest_30m_block']['end']} block was "
                    f"lowest at **{findings['rth_weakest_30m_block']['mean_return_bps']:.2f} "
                    "bps**, with an interval that includes zero. These are "
                    "unadjusted development-sample extrema."
                ),
            },
            {
                "id": "rth_blocks_chart_block",
                "type": "chart",
                "chartId": "rth_blocks_chart",
            },
            {
                "id": "rth_clock_finding",
                "type": "markdown",
                "sourceId": "market_clock",
                "body": (
                    "## Five-minute returns are noisy across the RTH clock\n\n"
                    "The finer five-minute profile shows alternating positive "
                    "and negative averages rather than a smooth one-way drift. "
                    "The backing data retain confidence intervals, medians, "
                    "positive rates, observations, and excursions for every "
                    "start time. Overlapping cells are correlated and should "
                    "not be read as independent discoveries."
                ),
            },
            {
                "id": "rth_five_chart_block",
                "type": "chart",
                "chartId": "rth_five_chart",
            },
            {
                "id": "stability_finding",
                "type": "markdown",
                "sourceId": "stability",
                "body": (
                    "## The 14:30 mean keeps its sign by year, but annual "
                    "uncertainty remains wide\n\n"
                    "All four sufficiently populated calendar-year cuts have "
                    "a positive mean for 14:30-15:00. "
                    + (
                        "**Every individual annual 95% interval crosses zero**, "
                        if year_cis_cross_zero
                        else "At least one annual 95% interval excludes zero, "
                    )
                    + "so the pooled result is not enough to establish a "
                    "stable event. Quarter, weekday, and strictly lagged "
                    "regime cuts remain available for Phase 6 design."
                ),
            },
            {
                "id": "year_stability_table_block",
                "type": "table",
                "tableId": "year_stability_table",
            },
            {
                "id": "turning_point_finding",
                "type": "markdown",
                "sourceId": "turning_points",
                "body": (
                    "## RTH extremes cluster at the open and close\n\n"
                    f"The first RTH high appears in **{high_bin['bin_label']}** "
                    f"in **{high_bin['share']:.1%}** of sessions; the first "
                    f"RTH low appears there in **{low_bin['share']:.1%}**. "
                    "The final half-hour is the second-largest bin for both. "
                    "This U-shaped timing concentration describes when "
                    "extremes occur; it does not define an entry."
                ),
            },
            {
                "id": "turning_chart_block",
                "type": "chart",
                "chartId": "turning_chart",
            },
            {
                "id": "scope_definitions",
                "type": "markdown",
                "sourceId": "phase5_config",
                "body": (
                    "## Scope and metric definitions\n\n"
                    "- **Population:** frozen development split only, "
                    "2021-07-29 through 2024-07-26.\n"
                    "- **Full-session clock:** contiguous 18:00-17:00 Eastern "
                    "Globex session.\n"
                    "- **RTH clock:** 09:30-16:00 Eastern, analyzed separately "
                    "but nested inside Globex.\n"
                    "- **Anchors and horizons:** five-minute start grid; 1, 5, "
                    "15, 30, and 60-minute intervals.\n"
                    "- **Return:** fixed-clock bar open to final included bar "
                    "close, in basis points.\n"
                    "- **MFE/MAE:** interval high and low relative to the "
                    "entry-bar open; these are not simulated fills."
                ),
            },
            {
                "id": "methodology",
                "type": "markdown",
                "sourceId": "phase5_manifest",
                "body": (
                    "## Methodology and validation\n\n"
                    "Means use two-sided 95% Student-t intervals; positive "
                    "rates use Wilson intervals. Stability is measured by "
                    "year, quarter, weekday, and strictly lagged trend, "
                    "volatility, and volume regimes, with 30 observations as "
                    "the sufficiency marker. Gate checks cover split lock, "
                    "source-row preservation, scope/horizon completeness, "
                    "unique keys, interval bounds, excursion direction, timing "
                    "shares, and byte-identical Parquet rebuilds."
                ),
            },
            {
                "id": "limitations",
                "type": "markdown",
                "sourceId": "phase5_manifest",
                "body": (
                    "## Limitations keep every finding exploratory\n\n"
                    "- No multiplicity adjustment is applied to the many clock "
                    "cells in Phase 5.\n"
                    "- Overlapping forward horizons are correlated.\n"
                    "- One-minute OHLC bars cannot reveal within-bar path or "
                    "executable fill sequence.\n"
                    "- Early-close sessions enter only complete intervals, so "
                    "late-day sample sizes are smaller.\n"
                    "- Full-session and RTH views overlap.\n"
                    "- No validation or final-test outcome was inspected."
                ),
            },
            {
                "id": "next_steps",
                "type": "markdown",
                "body": (
                    "## Recommended next step\n\n"
                    "Use this map only to preregister a restrained set of "
                    "interpretable Phase 6 event families. Record every test, "
                    "use session-aware bootstrap intervals and permuted nulls, "
                    "and apply Benjamini-Hochberg correction within each family "
                    "before anything advances to candidate generation."
                ),
            },
            {
                "id": "further_questions",
                "type": "markdown",
                "body": (
                    "## Further questions\n\n"
                    "- Does the late-afternoon tendency survive a preregistered "
                    "event definition and within-family multiplicity control?\n"
                    "- Are opening/closing extreme concentrations conditional "
                    "on lagged volatility without being driven by a few years?\n"
                    "- Do any descriptive effects remain large enough to matter "
                    "after Phase 7 costs and conservative execution?"
                ),
            },
        ],
        "cards": [
            {
                "id": "development_bars",
                "description": (
                    "One-minute rows from the development split; later splits "
                    "are prohibited."
                ),
                "dataset": "headline",
                "sourceId": "phase5_manifest",
                "metrics": [
                    {
                        "label": "Development bars",
                        "field": "development_rows",
                        "format": "number",
                    },
                    {
                        "label": "Globex sessions",
                        "field": "development_sessions",
                        "format": "number",
                    },
                    {
                        "label": "RTH sessions",
                        "field": "rth_sessions",
                        "format": "number",
                    },
                ],
            },
            {
                "id": "clock_cells",
                "description": (
                    "Fixed-clock summaries plus calendar and lagged-regime cuts."
                ),
                "dataset": "headline",
                "sourceId": "phase5_manifest",
                "metrics": [
                    {
                        "label": "Clock/horizon cells",
                        "field": "market_clock_cells",
                        "format": "number",
                    },
                    {
                        "label": "Stability cuts",
                        "field": "stability_cells",
                        "format": "number",
                    },
                ],
            },
            {
                "id": "locked_rows",
                "description": (
                    "Rows from later chronological splits used by Phase 5."
                ),
                "dataset": "headline",
                "sourceId": "phase5_manifest",
                "metrics": [
                    {
                        "label": "Final-test rows used",
                        "field": "final_test_rows_used",
                        "format": "number",
                    },
                    {
                        "label": "Validation rows used",
                        "field": "validation_rows_used",
                        "format": "number",
                    },
                ],
            },
            {
                "id": "largest_30m_mean",
                "description": (
                    "Largest mean among non-overlapping 30-minute RTH blocks; "
                    "unadjusted and descriptive."
                ),
                "dataset": "headline",
                "sourceId": "clock_blocks",
                "metrics": [
                    {
                        "label": "Largest 30m mean",
                        "field": "strongest_30m_mean_bps",
                        "format": "number",
                        "unit": "bps",
                    },
                    {
                        "label": "95% CI low",
                        "field": "strongest_30m_ci_low",
                        "format": "number",
                        "unit": "bps",
                    },
                    {
                        "label": "95% CI high",
                        "field": "strongest_30m_ci_high",
                        "format": "number",
                        "unit": "bps",
                    },
                ],
            },
        ],
        "charts": [
            {
                "id": "rth_blocks_chart",
                "title": "Mean return across 30-minute RTH blocks",
                "description": (
                    "Thirteen non-overlapping development-sample blocks; "
                    "14:30-15:00 has the largest mean."
                ),
                "type": "bar",
                "dataset": "rth_blocks",
                "sourceId": "clock_blocks",
                "encodings": {
                    "x": {"field": "interval", "type": "nominal"},
                    "y": {"field": "mean_return_bps", "type": "quantitative"},
                },
                "options": {"grouping": "single", "orientation": "vertical"},
                "showDescription": True,
            },
            {
                "id": "rth_five_chart",
                "title": "Mean five-minute RTH return by start time",
                "description": (
                    "Development sample; the alternating profile is noisy and "
                    "unadjusted for multiple clock-cell inspection."
                ),
                "type": "line",
                "dataset": "rth_five",
                "sourceId": "market_clock",
                "encodings": {
                    "x": {"field": "clock_label", "type": "nominal"},
                    "y": {"field": "mean_return_bps", "type": "quantitative"},
                },
                "options": {"points": "never"},
                "showDescription": True,
            },
            {
                "id": "turning_chart",
                "title": "RTH session-extreme timing",
                "description": (
                    "Share of sessions whose first RTH high or low occurs in "
                    "each 30-minute bin."
                ),
                "type": "bar",
                "dataset": "rth_turning",
                "sourceId": "turning_points",
                "encodings": {
                    "x": {"field": "bin_label", "type": "nominal"},
                    "y": {"field": "share", "type": "quantitative"},
                    "color": {"field": "extreme", "type": "nominal"},
                },
                "options": {"grouping": "grouped", "orientation": "vertical"},
                "showDescription": True,
            },
        ],
        "tables": [
            {
                "id": "year_stability_table",
                "title": "14:30-15:00 RTH return by calendar year",
                "description": (
                    "Development-only year cuts; all annual intervals include zero."
                ),
                "dataset": "strongest_years",
                "sourceId": "stability",
                "columns": [
                    {"field": "cut_value", "label": "Year", "type": "text"},
                    {
                        "field": "observations",
                        "label": "Observations",
                        "type": "number",
                        "format": "number",
                    },
                    {
                        "field": "mean_return_bps",
                        "label": "Mean return",
                        "type": "number",
                        "format": "number",
                        "unit": "bps",
                    },
                    {
                        "field": "mean_return_ci_low",
                        "label": "95% CI low",
                        "type": "number",
                        "format": "number",
                        "unit": "bps",
                    },
                    {
                        "field": "mean_return_ci_high",
                        "label": "95% CI high",
                        "type": "number",
                        "format": "number",
                        "unit": "bps",
                    },
                    {
                        "field": "positive_rate",
                        "label": "Positive rate",
                        "type": "number",
                        "format": "percent",
                    },
                ],
                "defaultSort": {"field": "cut_value", "direction": "asc"},
                "showDescription": True,
            }
        ],
        "sources": sources,
    }
    snapshot = {
        "version": 1,
        "status": "ready",
        "generatedAt": phase5["created_at_utc"],
        "datasets": {
            "headline": headline,
            "rth_blocks": _records(rth_blocks),
            "rth_five": _records(rth_five),
            "strongest_years": _records(strongest_years),
            "rth_turning": _records(rth_turning),
        },
    }
    return {
        "surface": "report",
        "manifest": manifest,
        "snapshot": snapshot,
        "sources": sources,
    }


def _sources(strongest_minute: int) -> list[dict[str, Any]]:
    return [
        {
            "id": "phase5_manifest",
            "label": "Phase 5 analysis manifest",
            "path": "data/manifests/phase5_manifest.json",
            "query": {
                "description": (
                    "Phase 5 coverage, method, gate checks, checksums, and "
                    "descriptive findings."
                ),
                "engine": "duckdb",
                "language": "sql",
                "sql": (
                    "SELECT coverage, method, findings, validation "
                    "FROM read_json_auto('data/manifests/phase5_manifest.json')"
                ),
                "tables_used": ["data/manifests/phase5_manifest.json"],
                "filters": ["input_split = development"],
                "metric_definitions": [
                    "development_rows = one-minute feature rows whose frozen split is development",
                    "market_clock_cells = unique scope, fixed start, and horizon aggregates",
                    "stability_cells = market-clock cells further cut by one calendar or lagged-regime value",
                ],
            },
        },
        {
            "id": "clock_blocks",
            "label": "Phase 5 non-overlapping clock blocks",
            "path": "results/phase5/report_clock_blocks.parquet",
            "query": {
                "description": (
                    "Non-overlapping 30-minute RTH and hourly full-session "
                    "blocks selected from the complete market clock."
                ),
                "engine": "duckdb",
                "language": "sql",
                "sql": (
                    "SELECT clock_label || '-' || end_clock_label AS interval, "
                    "clock_minute, observations, mean_return_bps, "
                    "median_return_bps, mean_return_ci_low, "
                    "mean_return_ci_high, positive_rate, mean_mfe_bps, "
                    "mean_mae_bps FROM "
                    "read_parquet('results/phase5/report_clock_blocks.parquet') "
                    "WHERE scope = 'rth' ORDER BY clock_minute"
                ),
                "tables_used": ["results/phase5/report_clock_blocks.parquet"],
                "filters": [
                    "scope = rth",
                    "horizon_minutes = 30",
                    "non-overlapping 30-minute starts",
                    "development split only",
                ],
                "metric_definitions": [
                    "mean_return_bps = average of (final included bar close / fixed-clock entry bar open - 1) * 10,000",
                    "mean_mfe_bps = average interval maximum high relative to entry open, in basis points",
                    "mean_mae_bps = average interval minimum low relative to entry open, in basis points",
                ],
            },
        },
        {
            "id": "market_clock",
            "label": "Phase 5 complete market-clock aggregates",
            "path": "results/phase5/market_clock.parquet",
            "query": {
                "description": (
                    "Five-minute RTH forward-return profile with sample size, "
                    "uncertainty, positive rates, and excursions."
                ),
                "engine": "duckdb",
                "language": "sql",
                "sql": (
                    "SELECT clock_label, clock_minute, observations, "
                    "mean_return_bps, median_return_bps, mean_return_ci_low, "
                    "mean_return_ci_high, positive_rate, mean_mfe_bps, "
                    "mean_mae_bps FROM "
                    "read_parquet('results/phase5/market_clock.parquet') "
                    "WHERE scope = 'rth' AND horizon_minutes = 5 "
                    "ORDER BY clock_minute"
                ),
                "tables_used": ["results/phase5/market_clock.parquet"],
                "filters": [
                    "scope = rth",
                    "horizon_minutes = 5",
                    "development split only",
                ],
                "metric_definitions": [
                    "mean_return_ci_low/high = two-sided 95% Student-t confidence interval for the cell mean",
                    "positive_rate = observations with return_bps > 0 / observations",
                ],
            },
        },
        {
            "id": "stability",
            "label": "Phase 5 calendar and lagged-regime stability cuts",
            "path": "results/phase5/market_clock_stability.parquet",
            "query": {
                "description": (
                    "Calendar-year cuts for the strongest non-overlapping "
                    "30-minute RTH development-sample block."
                ),
                "engine": "duckdb",
                "language": "sql",
                "sql": (
                    "SELECT cut_value, observations, mean_return_bps, "
                    "median_return_bps, mean_return_ci_low, "
                    "mean_return_ci_high, positive_rate, mean_mfe_bps, "
                    "mean_mae_bps FROM "
                    "read_parquet('results/phase5/market_clock_stability.parquet') "
                    "WHERE scope = 'rth' AND horizon_minutes = 30 "
                    f"AND clock_minute = {strongest_minute} "
                    "AND cut_type = 'calendar_year' ORDER BY cut_value"
                ),
                "tables_used": [
                    "results/phase5/market_clock_stability.parquet"
                ],
                "filters": [
                    "scope = rth",
                    "horizon_minutes = 30",
                    f"clock_minute = {strongest_minute}",
                    "cut_type = calendar_year",
                    "development split only",
                ],
                "metric_definitions": [
                    "sample_sufficient = observations >= 30",
                    "year means use the same fixed-clock return definition as the overall market clock",
                ],
            },
        },
        {
            "id": "turning_points",
            "label": "Phase 5 RTH session-extreme timing",
            "path": "results/phase5/turning_point_timing.parquet",
            "query": {
                "description": (
                    "Distribution of the first RTH session high and low across "
                    "30-minute clock bins."
                ),
                "engine": "duckdb",
                "language": "sql",
                "sql": (
                    "SELECT bin_label, bin_start_minute, "
                    "CASE extreme_type WHEN 'high' THEN 'Session high' "
                    "ELSE 'Session low' END AS extreme, observations, count, "
                    "share, share_ci_low, share_ci_high FROM "
                    "read_parquet('results/phase5/turning_point_timing.parquet') "
                    "WHERE scope = 'rth' ORDER BY bin_start_minute, extreme"
                ),
                "tables_used": [
                    "results/phase5/turning_point_timing.parquet"
                ],
                "filters": [
                    "scope = rth",
                    "30-minute timing bins",
                    "development split only",
                ],
                "metric_definitions": [
                    "share = sessions whose first occurrence of the RTH high or low falls in the bin / RTH sessions",
                    "share_ci_low/high = two-sided 95% Wilson interval",
                ],
            },
        },
        {
            "id": "phase5_config",
            "label": "Phase 5 frozen analysis configuration",
            "path": "config/phase5.yaml",
            "query": {
                "description": (
                    "Frozen scopes, horizons, uncertainty method, stability "
                    "dimensions, and descriptive-only reporting policy."
                ),
                "tables_used": ["config/phase5.yaml"],
                "filters": [
                    "allowed_split = development",
                    "validation and final_untouched_test forbidden",
                ],
                "metric_definitions": [
                    "anchor_step_minutes = 5",
                    "horizons_minutes = 1, 5, 15, 30, and 60",
                ],
            },
        },
    ]


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    clean = frame.copy()
    clean = clean.where(pd.notna(clean), None)
    return clean.to_dict(orient="records")


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "phase5_artifact.json",
    )
    args = parser.parse_args()
    artifact = build_phase5_artifact()
    _write_json_atomic(args.output, artifact)
    print(args.output)


if __name__ == "__main__":
    main()
