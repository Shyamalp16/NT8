"""Build the bounded Data Analytics report artifact for Phase 6 v2."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def build_phase6_v2_artifact(
    manifest_path: Path = (
        ROOT / "data" / "manifests" / "phase6_v2_manifest.json"
    ),
    ledger_path: Path = (
        ROOT / "results" / "phase6_v2" / "hypothesis_ledger.parquet"
    ),
    observations_path: Path = (
        ROOT / "results" / "phase6_v2" / "event_observations.parquet"
    ),
    stability_path: Path = (
        ROOT / "results" / "phase6_v2" / "hypothesis_year_stability.parquet"
    ),
) -> dict[str, Any]:
    """Return the canonical technical report manifest and bounded snapshot."""
    phase6 = json.loads(manifest_path.read_text(encoding="utf-8"))
    ledger = pd.read_parquet(ledger_path)
    observations = pd.read_parquet(observations_path)
    stability = pd.read_parquet(stability_path)

    ledger = ledger.copy()
    ledger["family_label"] = ledger["family_id"].map(
        {
            "volatility_state_transition": "Volatility-state transition",
            "signed_pressure_burst": "Signed pressure burst",
        }
    )
    ledger["horizon_label"] = (
        ledger["horizon_minutes"].astype(str) + " minutes"
    )
    ledger["result_label"] = ledger["result"].map(
        {
            "does_not_advance": "Does not advance",
            "advances_to_phase7": "Advances to Phase 7",
            "insufficient_sample": "Insufficient sample",
        }
    )
    ledger_table = ledger[
        [
            "family_label",
            "horizon_label",
            "observations",
            "session_clusters",
            "mean_effect_bps",
            "bootstrap_ci_low",
            "bootstrap_ci_high",
            "permutation_p_value",
            "bh_q_value",
            "cumulative_bh_q_value",
            "result_label",
        ]
    ].sort_values(
        ["cumulative_bh_q_value", "permutation_p_value"],
        kind="mergesort",
    )

    volatility = observations.loc[
        observations["family_id"] == "volatility_state_transition"
    ].copy()
    range_by_state = (
        volatility.groupby(
            ["horizon_minutes", "event_state"],
            sort=True,
        )
        .agg(
            mean_forward_range_bps=("forward_range_bps", "mean"),
            median_forward_range_bps=("forward_range_bps", "median"),
            observations=("session_date", "size"),
            sessions=("session_date", "nunique"),
        )
        .reset_index()
    )
    range_by_state["horizon_label"] = (
        range_by_state["horizon_minutes"].astype(str) + " minutes"
    )
    range_by_state["state_label"] = range_by_state["event_state"].map(
        {"high": "High trailing volatility", "low": "Low trailing volatility"}
    )
    range_by_state = range_by_state[
        [
            "horizon_label",
            "state_label",
            "mean_forward_range_bps",
            "median_forward_range_bps",
            "observations",
            "sessions",
        ]
    ]

    annual = stability.loc[
        stability["family_id"] == "volatility_state_transition"
    ].copy()
    annual["year_label"] = annual["calendar_year"].astype(str)
    annual["horizon_label"] = (
        annual["hypothesis_id"]
        .str.extract(r"__(\d+)m$", expand=False)
        .astype(str)
        + " minutes"
    )
    annual = annual[
        [
            "year_label",
            "horizon_label",
            "mean_effect_bps",
            "observations",
            "session_clusters",
        ]
    ]

    v15 = ledger.loc[
        ledger["hypothesis_id"] == "volatility_state_transition__15m"
    ].iloc[0]
    v30 = ledger.loc[
        ledger["hypothesis_id"] == "volatility_state_transition__30m"
    ].iloc[0]
    pressure = ledger.loc[
        ledger["family_id"] == "signed_pressure_burst"
    ]
    headline = [
        {
            "advancing_hypotheses": int(
                phase6["coverage"]["advancing_hypotheses"]
            ),
            "total_hypotheses": int(phase6["coverage"]["hypotheses"]),
            "cumulative_q": float(v15["cumulative_bh_q_value"]),
            "range_gap_15m_bps": float(v15["mean_effect_bps"]),
            "high_range_15m_bps": float(
                v15["high_state_mean_outcome_bps"]
            ),
            "low_range_15m_bps": float(v15["low_state_mean_outcome_bps"]),
            "range_gap_30m_bps": float(v30["mean_effect_bps"]),
            "high_range_30m_bps": float(
                v30["high_state_mean_outcome_bps"]
            ),
            "low_range_30m_bps": float(v30["low_state_mean_outcome_bps"]),
            "later_split_rows_used": 0,
        }
    ]

    sources = _sources()
    title = "MNQ Phase 6 v2 Event Studies"
    manifest = {
        "version": 1,
        "surface": "report",
        "title": title,
        "description": (
            "Development-only preregistered tests of volatility-state "
            "transitions and signed uptick/downtick pressure bursts."
        ),
        "blocks": [
            {"id": "title", "type": "markdown", "body": f"# {title}"},
            {
                "id": "technical_summary",
                "type": "markdown",
                "sourceId": "phase6_v2_manifest",
                "body": (
                    "## Technical summary\n\n"
                    "The genuinely new volatility-state family survived every "
                    "frozen screen: both the **15-minute and 30-minute future-"
                    "range tests advance to Phase 7**. High trailing-volatility "
                    "states preceded ranges wider by **21.14 bps** and "
                    "**29.10 bps**, respectively. Both cumulative q-values are "
                    "**0.0025** after correcting across all 25 Phase 6 v1+v2 "
                    "tests. The signed pressure family failed. This establishes "
                    "conditional volatility persistence, not direction or "
                    "tradability."
                ),
            },
            {
                "id": "headline_metrics",
                "type": "metric-strip",
                "cardIds": [
                    "advancement",
                    "range_gap_15m",
                    "range_gap_30m",
                    "later_split_usage",
                ],
            },
            {
                "id": "range_finding",
                "type": "markdown",
                "sourceId": "hypothesis_ledger",
                "body": (
                    "## High trailing volatility predicts materially wider "
                    "near-term ranges\n\n"
                    "At 15 minutes, high-state events averaged **40.97 bps** of "
                    "future range versus **19.83 bps** in low states. At 30 "
                    "minutes, the comparison was **56.13 bps** versus "
                    "**27.03 bps**. The event feature is the sample standard "
                    "deviation of the prior 30 one-minute log returns, observed "
                    "at the trigger-bar close; the outcome begins at the next "
                    "bar open, so feature and outcome windows do not overlap."
                ),
            },
            {
                "id": "range_chart_block",
                "type": "chart",
                "chartId": "range_chart",
            },
            {
                "id": "annual_finding",
                "type": "markdown",
                "sourceId": "year_stability",
                "body": (
                    "## The volatility-range difference is positive in every "
                    "calendar-year cut\n\n"
                    "The high-minus-low effect remains positive for both "
                    "horizons in 2021, 2022, 2023, and the development portion "
                    "of 2024. These annual cuts are descriptive diagnostics, "
                    "not additional selection tests."
                ),
            },
            {
                "id": "annual_chart_block",
                "type": "chart",
                "chartId": "annual_chart",
            },
            {
                "id": "pressure_finding",
                "type": "markdown",
                "sourceId": "hypothesis_ledger",
                "body": (
                    "## Signed pressure bursts do not predict continuation or "
                    "reversal\n\n"
                    f"The 5-minute direction-aligned mean was "
                    f"**{pressure.loc[pressure['horizon_minutes'] == 5, 'mean_effect_bps'].iloc[0]:.2f} "
                    f"bps** and the 15-minute mean was "
                    f"**{pressure.loc[pressure['horizon_minutes'] == 15, 'mean_effect_bps'].iloc[0]:.2f} "
                    "bps**; both confidence intervals include zero and both "
                    "cumulative q-values are 0.841. The provider fields are "
                    "uptick/downtick volume, not trade-aggressor volume."
                ),
            },
            {
                "id": "ledger_table_block",
                "type": "table",
                "tableId": "ledger_table",
            },
            {
                "id": "scope",
                "type": "markdown",
                "sourceId": "phase6_v2_config",
                "body": (
                    "## Scope, data, and metric definitions\n\n"
                    "- **Population:** 1,021,050 one-minute development bars "
                    "from 744 usable MNQ sessions; later splits are excluded.\n"
                    "- **Volatility state:** current trailing 30-bar realized "
                    "volatility compared with a strictly prior 60-session "
                    "same-clock baseline; low is at or below the lagged 20th "
                    "percentile and high is at or above the lagged 80th.\n"
                    "- **Future range:** interval maximum high minus minimum "
                    "low, divided by entry-bar open, in basis points.\n"
                    "- **Pressure burst:** first five-minute absolute uptick/"
                    "downtick pressure above its lagged same-clock 90th "
                    "percentile with activity above the lagged median.\n"
                    "- **Entry timing:** every event enters its measurement "
                    "window at the bar after the trigger close."
                ),
            },
            {
                "id": "methodology",
                "type": "markdown",
                "sourceId": "phase6_v2_manifest",
                "body": (
                    "## Inference preserves session dependence and the full "
                    "search history\n\n"
                    "Intervals use 5,000 session-cluster bootstrap replicates. "
                    "Volatility labels are permuted within entry-clock strata; "
                    "pressure directions are permuted across sessions. "
                    "Two-sided p-values use the plus-one correction. Advancement "
                    "requires at least 100 sessions, at least 100 observations "
                    "per volatility group, within-family BH q≤0.05, cumulative "
                    "25-test BH q≤0.05, and a 95% cluster interval excluding zero."
                ),
            },
            {
                "id": "limitations",
                "type": "markdown",
                "sourceId": "phase6_v2_manifest",
                "body": (
                    "## The positive result is a volatility forecast, not a "
                    "directional edge\n\n"
                    "- The result is still in-sample development evidence; "
                    "validation and final-test outcomes remain untouched.\n"
                    "- A wider future range does not say whether MNQ will rise "
                    "or fall and cannot directly specify a long or short trade.\n"
                    "- A breakout, straddle-like stop-entry, or volatility-"
                    "conditioned directional rule would add a new mechanism "
                    "that must be frozen and tested with realistic fills and "
                    "costs.\n"
                    "- One-minute bars cannot resolve intrabar order sequence.\n"
                    "- No commissions, slippage, stops, targets, or sizing are "
                    "included here."
                ),
            },
            {
                "id": "next_steps",
                "type": "markdown",
                "body": (
                    "## Recommended next step\n\n"
                    "Proceed to Phase 7 **only for volatility-conditioned "
                    "candidate generation**. Freeze a very small set of "
                    "mechanically distinct entry mechanisms before measuring "
                    "tradable outcomes; preserve the current thresholds and "
                    "clocks, model conservative next-event fills and costs, and "
                    "use validation—not the untouched final test—for candidate "
                    "selection. Drop the signed pressure family."
                ),
            },
            {
                "id": "further_questions",
                "type": "markdown",
                "body": (
                    "## Further questions\n\n"
                    "- Which minimal futures mechanism can exploit range "
                    "expansion without inventing direction after the fact?\n"
                    "- Does the range expansion remain useful after delayed "
                    "entries, stop-entry slippage, commissions, and same-bar "
                    "ambiguity rules?\n"
                    "- Is 15 or 30 minutes preferable once the candidate "
                    "mechanism—not this event study—defines executable risk?"
                ),
            },
        ],
        "cards": [
            {
                "id": "advancement",
                "description": (
                    "Frozen v2 tests that cleared sample, interval, and both "
                    "multiplicity gates."
                ),
                "dataset": "headline",
                "sourceId": "phase6_v2_manifest",
                "metrics": [
                    {
                        "label": "Advancing tests",
                        "field": "advancing_hypotheses",
                        "format": "number",
                    },
                    {
                        "label": "of v2 tests",
                        "field": "total_hypotheses",
                        "format": "number",
                    },
                    {
                        "label": "Cumulative q",
                        "field": "cumulative_q",
                        "format": "number",
                    },
                ],
            },
            {
                "id": "range_gap_15m",
                "description": "High-state minus low-state 15-minute range.",
                "dataset": "headline",
                "sourceId": "hypothesis_ledger",
                "metrics": [
                    {
                        "label": "15m range gap",
                        "field": "range_gap_15m_bps",
                        "format": "number",
                        "unit": "bps",
                        "signed": True,
                    },
                    {
                        "label": "High state",
                        "field": "high_range_15m_bps",
                        "format": "number",
                        "unit": "bps",
                    },
                    {
                        "label": "Low state",
                        "field": "low_range_15m_bps",
                        "format": "number",
                        "unit": "bps",
                    },
                ],
            },
            {
                "id": "range_gap_30m",
                "description": "High-state minus low-state 30-minute range.",
                "dataset": "headline",
                "sourceId": "hypothesis_ledger",
                "metrics": [
                    {
                        "label": "30m range gap",
                        "field": "range_gap_30m_bps",
                        "format": "number",
                        "unit": "bps",
                        "signed": True,
                    },
                    {
                        "label": "High state",
                        "field": "high_range_30m_bps",
                        "format": "number",
                        "unit": "bps",
                    },
                    {
                        "label": "Low state",
                        "field": "low_range_30m_bps",
                        "format": "number",
                        "unit": "bps",
                    },
                ],
            },
            {
                "id": "later_split_usage",
                "description": "Validation and final outcomes remain untouched.",
                "dataset": "headline",
                "sourceId": "phase6_v2_manifest",
                "metrics": [
                    {
                        "label": "Later-split rows",
                        "field": "later_split_rows_used",
                        "format": "number",
                    }
                ],
            },
        ],
        "charts": [
            {
                "id": "range_chart",
                "title": "Forward range by trailing volatility state and horizon",
                "description": (
                    "Mean future high-low range in basis points; 3,109–3,114 "
                    "events across 658 development sessions."
                ),
                "type": "bar",
                "dataset": "range_by_state",
                "sourceId": "event_observations",
                "encodings": {
                    "x": {"field": "horizon_label", "type": "nominal"},
                    "y": {
                        "field": "mean_forward_range_bps",
                        "type": "quantitative",
                    },
                    "color": {"field": "state_label", "type": "nominal"},
                },
                "options": {"grouping": "grouped", "orientation": "vertical"},
                "showDescription": True,
            },
            {
                "id": "annual_chart",
                "title": "High-minus-low range difference by calendar year",
                "description": (
                    "Development-only diagnostic in basis points; 2021 and "
                    "2024 are partial sample years."
                ),
                "type": "bar",
                "dataset": "annual_stability",
                "sourceId": "year_stability",
                "encodings": {
                    "x": {"field": "year_label", "type": "nominal"},
                    "y": {
                        "field": "mean_effect_bps",
                        "type": "quantitative",
                    },
                    "color": {"field": "horizon_label", "type": "nominal"},
                },
                "options": {"grouping": "grouped", "orientation": "vertical"},
                "showDescription": True,
            },
        ],
        "tables": [
            {
                "id": "ledger_table",
                "title": "Complete Phase 6 v2 hypothesis ledger",
                "description": (
                    "All four frozen tests, sorted by cumulative adjusted "
                    "q-value; effects have family-specific meanings."
                ),
                "dataset": "ledger_table",
                "sourceId": "hypothesis_ledger",
                "columns": [
                    {"field": "family_label", "label": "Family", "type": "text"},
                    {"field": "horizon_label", "label": "Horizon", "type": "text"},
                    {
                        "field": "observations",
                        "label": "Events",
                        "type": "number",
                        "format": "number",
                    },
                    {
                        "field": "session_clusters",
                        "label": "Sessions",
                        "type": "number",
                        "format": "number",
                    },
                    {
                        "field": "mean_effect_bps",
                        "label": "Effect",
                        "type": "number",
                        "format": "number",
                        "unit": "bps",
                    },
                    {
                        "field": "bootstrap_ci_low",
                        "label": "95% CI low",
                        "type": "number",
                        "format": "number",
                        "unit": "bps",
                    },
                    {
                        "field": "bootstrap_ci_high",
                        "label": "95% CI high",
                        "type": "number",
                        "format": "number",
                        "unit": "bps",
                    },
                    {
                        "field": "permutation_p_value",
                        "label": "Raw p",
                        "type": "number",
                        "format": "number",
                    },
                    {
                        "field": "bh_q_value",
                        "label": "Family q",
                        "type": "number",
                        "format": "number",
                    },
                    {
                        "field": "cumulative_bh_q_value",
                        "label": "Cumulative q",
                        "type": "number",
                        "format": "number",
                    },
                    {"field": "result_label", "label": "Result", "type": "text"},
                ],
                "defaultSort": {
                    "field": "cumulative_bh_q_value",
                    "direction": "asc",
                },
                "showDescription": True,
            }
        ],
        "sources": sources,
    }
    snapshot = {
        "version": 1,
        "status": "ready",
        "generatedAt": phase6["created_at_utc"],
        "datasets": {
            "headline": headline,
            "range_by_state": _records(range_by_state),
            "annual_stability": _records(annual),
            "ledger_table": _records(ledger_table),
        },
    }
    return {
        "surface": "report",
        "manifest": manifest,
        "snapshot": snapshot,
        "sources": sources,
    }


def _sources() -> list[dict[str, Any]]:
    return [
        {
            "id": "phase6_v2_manifest",
            "label": "Phase 6 v2 analysis manifest",
            "path": "data/manifests/phase6_v2_manifest.json",
            "query": {
                "description": (
                    "Coverage, frozen methods, checksums, findings, and "
                    "validation results."
                ),
                "engine": "duckdb",
                "language": "sql",
                "sql": (
                    "SELECT coverage, method, findings, validation FROM "
                    "read_json_auto('data/manifests/phase6_v2_manifest.json')"
                ),
                "tables_used": [
                    "data/manifests/phase6_v2_manifest.json"
                ],
                "filters": ["input_split = development"],
                "metric_definitions": [
                    "advancing_hypotheses = sample-sufficient tests passing within-family and cumulative BH at 0.05 with a 95% session-cluster interval excluding zero",
                    "event_observations = eligible event-clock-horizon rows",
                ],
            },
        },
        {
            "id": "hypothesis_ledger",
            "label": "Phase 6 v2 complete hypothesis ledger",
            "path": "results/phase6_v2/hypothesis_ledger.parquet",
            "query": {
                "description": (
                    "All four frozen tests with clustered intervals, "
                    "randomized-null p-values, and both multiplicity gates."
                ),
                "engine": "duckdb",
                "language": "sql",
                "sql": (
                    "SELECT * FROM "
                    "read_parquet('results/phase6_v2/hypothesis_ledger.parquet') "
                    "ORDER BY cumulative_bh_q_value, permutation_p_value"
                ),
                "tables_used": [
                    "results/phase6_v2/hypothesis_ledger.parquet"
                ],
                "filters": [
                    "development split only",
                    "all four frozen v2 hypotheses retained",
                ],
                "metric_definitions": [
                    "volatility mean_effect_bps = high-state minus low-state mean future high-low range divided by entry open, in basis points",
                    "pressure mean_effect_bps = mean pressure-direction times future return, in basis points",
                    "bootstrap_ci_low/high = 95% percentile interval from 5,000 session-cluster resamples",
                    "cumulative_bh_q_value = BH adjustment across 21 v1 and 4 v2 randomized-null p-values",
                ],
            },
        },
        {
            "id": "event_observations",
            "label": "Phase 6 v2 event observations",
            "path": "results/phase6_v2/event_observations.parquet",
            "query": {
                "description": (
                    "Event-level states, lagged thresholds, timing, and forward "
                    "outcomes used to compute the v2 ledger."
                ),
                "engine": "duckdb",
                "language": "sql",
                "sql": (
                    "SELECT horizon_minutes, event_state, "
                    "avg(forward_range_bps) AS mean_forward_range_bps, "
                    "median(forward_range_bps) AS median_forward_range_bps, "
                    "count(*) AS observations, "
                    "count(DISTINCT session_date) AS sessions FROM "
                    "read_parquet('results/phase6_v2/event_observations.parquet') "
                    "WHERE family_id = 'volatility_state_transition' GROUP BY 1, 2"
                ),
                "tables_used": [
                    "results/phase6_v2/event_observations.parquet"
                ],
                "filters": [
                    "family_id = volatility_state_transition",
                    "development split only",
                ],
                "metric_definitions": [
                    "forward_range_bps = (maximum interval high - minimum interval low) / entry-bar open * 10,000",
                    "high/low state = current trailing 30-bar realized volatility above lagged same-clock 80th percentile or below lagged 20th percentile",
                ],
            },
        },
        {
            "id": "year_stability",
            "label": "Phase 6 v2 calendar-year stability",
            "path": "results/phase6_v2/hypothesis_year_stability.parquet",
            "query": {
                "description": (
                    "Descriptive calendar-year high-minus-low volatility-range "
                    "differences by frozen horizon."
                ),
                "engine": "duckdb",
                "language": "sql",
                "sql": (
                    "SELECT calendar_year, hypothesis_id, mean_effect_bps, "
                    "observations, session_clusters FROM "
                    "read_parquet('results/phase6_v2/hypothesis_year_stability.parquet') "
                    "WHERE family_id = 'volatility_state_transition' "
                    "ORDER BY calendar_year, hypothesis_id"
                ),
                "tables_used": [
                    "results/phase6_v2/hypothesis_year_stability.parquet"
                ],
                "filters": [
                    "family_id = volatility_state_transition",
                    "calendar-year diagnostics only",
                ],
                "metric_definitions": [
                    "mean_effect_bps = calendar-year high-state mean future range minus low-state mean future range",
                ],
            },
        },
        {
            "id": "phase6_v2_config",
            "label": "Phase 6 v2 frozen preregistration",
            "path": "config/phase6_v2.yaml",
            "query": {
                "description": (
                    "Frozen families, thresholds, outcome semantics, inference, "
                    "and advancement rules."
                ),
                "tables_used": ["config/phase6_v2.yaml"],
                "filters": [
                    "allowed_split = development",
                    "validation and final_untouched_test forbidden",
                ],
                "metric_definitions": [
                    "same-clock baseline = strictly prior 60 usable sessions with a minimum of 40",
                    "volatility thresholds = lagged 20th and 80th percentiles",
                    "pressure thresholds = lagged 90th percentile absolute pressure and median activity",
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
        default=ROOT / "reports" / "phase6_v2_artifact.json",
    )
    args = parser.parse_args()
    artifact = build_phase6_v2_artifact()
    _write_json_atomic(args.output, artifact)
    print(args.output)


if __name__ == "__main__":
    main()
