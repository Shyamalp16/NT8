"""Build the bounded Data Analytics report artifact for Phase 6."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def build_phase6_artifact(
    manifest_path: Path = ROOT / "data" / "manifests" / "phase6_manifest.json",
    ledger_path: Path = ROOT / "results" / "phase6" / "hypothesis_ledger.parquet",
    family_path: Path = ROOT / "results" / "phase6" / "family_summary.parquet",
) -> dict[str, Any]:
    """Return the canonical technical report manifest and bounded snapshot."""
    phase6 = json.loads(manifest_path.read_text(encoding="utf-8"))
    ledger = pd.read_parquet(ledger_path)
    families = pd.read_parquet(family_path)

    ledger = ledger.copy()
    ledger["short_label"] = ledger["hypothesis_label"].replace(
        {
            "overnight gap response over 30 minutes": "Overnight gap · 30m",
            "overnight gap response over 60 minutes": "Overnight gap · 60m",
            "opening 30 response over 30 minutes": "Opening 30 · 30m",
            "opening 30 response over 60 minutes": "Opening 30 · 60m",
            "first prior rth range break, 15 minutes": "Prior RTH break · 15m",
            "first prior rth range break, 30 minutes": "Prior RTH break · 30m",
            "first overnight range break, 15 minutes": "Overnight break · 15m",
            "first overnight range break, 30 minutes": "Overnight break · 30m",
        }
    )
    ledger["family_label"] = ledger["family_id"].map(
        {
            "rth_fixed_clock_30m": "RTH clock blocks",
            "overnight_gap_response": "Overnight gaps",
            "opening_30_response": "Opening response",
            "reference_level_break_response": "Reference breaks",
        }
    )
    ledger["result_label"] = ledger["result"].map(
        {
            "does_not_advance": "Does not advance",
            "advances_to_phase7": "Advances",
            "insufficient_sample": "Insufficient sample",
        }
    )
    ledger["q_rank"] = ledger["bh_q_value"].rank(
        method="first",
        ascending=True,
    ).astype(int)
    ledger_view = ledger[
        [
            "short_label",
            "family_label",
            "horizon_minutes",
            "observations",
            "mean_effect_bps",
            "median_effect_bps",
            "positive_effect_rate",
            "bootstrap_ci_low",
            "bootstrap_ci_high",
            "permutation_p_value",
            "bh_q_value",
            "q_rank",
            "result_label",
        ]
    ].sort_values("mean_effect_bps", kind="mergesort")

    table_view = ledger_view.sort_values(
        ["bh_q_value", "permutation_p_value"],
        kind="mergesort",
    )
    families = families.copy()
    families["family_label"] = families["family_id"].map(
        {
            "rth_fixed_clock_30m": "RTH clock blocks",
            "overnight_gap_response": "Overnight gaps",
            "opening_30_response": "Opening response",
            "reference_level_break_response": "Reference breaks",
        }
    )
    family_view = families[
        [
            "family_label",
            "hypotheses",
            "sample_sufficient_hypotheses",
            "bh_rejections",
            "advancing_hypotheses",
            "minimum_p_value",
            "minimum_q_value",
            "minimum_observations",
            "maximum_observations",
        ]
    ].sort_values("family_label", kind="mergesort")

    closest = ledger.sort_values(
        ["bh_q_value", "permutation_p_value"],
        kind="mergesort",
    ).iloc[0]
    headline = [
        {
            "hypotheses": phase6["coverage"]["hypotheses"],
            "families": phase6["preregistration"]["families"],
            "event_observations": phase6["coverage"]["event_observations"],
            "development_sessions": phase6["coverage"]["development_sessions"],
            "bh_rejections": phase6["coverage"]["bh_rejections"],
            "advancing_hypotheses": phase6["coverage"]["advancing_hypotheses"],
            "validation_rows_used": phase6["coverage"]["validation_rows_used"],
            "final_test_rows_used": phase6["coverage"]["final_test_rows_used"],
            "closest_mean_bps": float(closest["mean_effect_bps"]),
            "closest_raw_p": float(closest["permutation_p_value"]),
            "closest_q": float(closest["bh_q_value"]),
        }
    ]

    sources = _sources()
    title = "MNQ Phase 6 Event Studies"
    manifest = {
        "version": 1,
        "surface": "report",
        "title": title,
        "description": (
            "Development-only preregistered MNQ event studies with "
            "session-aware inference and within-family multiplicity control."
        ),
        "blocks": [
            {"id": "title", "type": "markdown", "body": f"# {title}"},
            {
                "id": "technical_summary",
                "type": "markdown",
                "sourceId": "phase6_manifest",
                "body": (
                    "## Technical summary\n\n"
                    "Phase 6 **passed its process gate but found no event that "
                    "can advance**. All **21 preregistered hypotheses** were "
                    "sufficiently populated, yet **zero survived within-family "
                    "Benjamini–Hochberg correction**. Validation and final-test "
                    "rows used: **zero**. The defensible conclusion is a null "
                    "event-study result, not a strategy candidate."
                ),
            },
            {
                "id": "headline_metrics",
                "type": "metric-strip",
                "cardIds": [
                    "preregistered_tests",
                    "event_coverage",
                    "multiplicity_result",
                    "later_split_usage",
                ],
            },
            {
                "id": "effect_finding",
                "type": "markdown",
                "sourceId": "hypothesis_ledger",
                "body": (
                    "## Apparent effects are small or too uncertain after the "
                    "prespecified family correction\n\n"
                    "The signed means span continuation and reversal, but none "
                    "has a q-value at or below 0.05. Positive directional bars "
                    "mean continuation; negative bars mean reversal. Fixed-clock "
                    "bars retain their raw up/down return sign. The backing data "
                    "keep sample size, bootstrap bounds, raw p-values, and "
                    "adjusted q-values beside every plotted mean."
                ),
            },
            {
                "id": "effect_chart_block",
                "type": "chart",
                "chartId": "effect_chart",
            },
            {
                "id": "near_miss",
                "type": "markdown",
                "sourceId": "hypothesis_ledger",
                "body": (
                    "## The 14:30–15:00 block is a multiplicity-adjusted failure\n\n"
                    f"Its mean is **{closest['mean_effect_bps']:.2f} bps** "
                    f"(95% bootstrap interval {closest['bootstrap_ci_low']:.2f} "
                    f"to {closest['bootstrap_ci_high']:.2f}; "
                    f"n={int(closest['observations']):,}). The unadjusted "
                    f"randomization p-value is **{closest['permutation_p_value']:.3f}**, "
                    f"but the within-family q-value is **{closest['bh_q_value']:.3f}**. "
                    "Narrowing the family around this result after seeing it "
                    "would be cherry-picking."
                ),
            },
            {
                "id": "ledger_table_block",
                "type": "table",
                "tableId": "ledger_table",
            },
            {
                "id": "family_finding",
                "type": "markdown",
                "sourceId": "family_summary",
                "body": (
                    "## Every preregistered family ends with zero survivors\n\n"
                    "The null conclusion is not caused by missing samples: all "
                    "21 tests exceed the 100-session minimum. It is caused by "
                    "weak or inconsistent effects relative to their prespecified "
                    "family multiplicity. The complete family summary prevents "
                    "failed families from disappearing from the record."
                ),
            },
            {
                "id": "family_table_block",
                "type": "table",
                "tableId": "family_table",
            },
            {
                "id": "scope",
                "type": "markdown",
                "sourceId": "phase6_config",
                "body": (
                    "## Scope, data, and effect definitions\n\n"
                    "- **Population:** 1,021,050 one-minute development bars "
                    "from 744 usable sessions, 2021-07-29 through 2024-07-26.\n"
                    "- **Fixed-clock family:** all 13 non-overlapping 30-minute "
                    "RTH blocks, not only the Phase 5 extrema.\n"
                    "- **Directional families:** material overnight gaps, "
                    "first-30-minute moves, and first closes beyond prior-RTH "
                    "or overnight ranges.\n"
                    "- **Entry semantics:** anchor-bar open when the event is "
                    "known at the anchor; next-bar open after a confirming range "
                    "break close.\n"
                    "- **Effect:** basis-point forward return; for directional "
                    "events, positive means continuation and negative means "
                    "reversal."
                ),
            },
            {
                "id": "methodology",
                "type": "markdown",
                "sourceId": "phase6_manifest",
                "body": (
                    "## Inference is session-aware, deterministic, and family-scoped\n\n"
                    "Each hypothesis uses 5,000 session-bootstrap replicates and "
                    "5,000 randomized-null replicates. Fixed-clock means use a "
                    "Rademacher sign-flip null; directional events permute event "
                    "directions across sessions. Two-sided p-values use the "
                    "plus-one correction. Benjamini–Hochberg correction is "
                    "applied within each frozen family at 5%, with deterministic "
                    "hypothesis-specific seeds."
                ),
            },
            {
                "id": "limitations",
                "type": "markdown",
                "sourceId": "phase6_manifest",
                "body": (
                    "## The null result does not test tradability\n\n"
                    "- Phase 5 and Phase 6 use the same development period; this "
                    "is not independent out-of-sample confirmation.\n"
                    "- Correction covers the four preregistered families, not "
                    "every event definition that could be imagined.\n"
                    "- One-minute bars cannot reveal within-bar path or "
                    "executable fill quality.\n"
                    "- No commissions, slippage, orders, stops, targets, sizing, "
                    "or same-bar fill model is part of Phase 6.\n"
                    "- Validation and final-test outcomes remain untouched."
                ),
            },
            {
                "id": "next_steps",
                "type": "markdown",
                "body": (
                    "## Recommended next step\n\n"
                    "Do **not** manufacture a Phase 7 candidate from this ledger. "
                    "Either close this branch with “no sufficiently robust edge "
                    "found,” or remain in Phase 6 and preregister a genuinely new "
                    "second wave of domain-motivated event families. A second "
                    "wave must retain every test and may not redefine the clock "
                    "family around the observed 14:30 result."
                ),
            },
            {
                "id": "further_questions",
                "type": "markdown",
                "body": (
                    "## Further questions\n\n"
                    "- Are there genuinely new event families justified before "
                    "looking at more outcomes, rather than variants of these "
                    "failed tests?\n"
                    "- Would external, pre-known event timestamps such as "
                    "scheduled macro releases support a defensible Phase 6 v2?\n"
                    "- If not, is the strongest conclusion to stop strategy "
                    "generation and preserve the untouched later splits?"
                ),
            },
        ],
        "cards": [
            {
                "id": "preregistered_tests",
                "description": "Complete frozen hypothesis inventory.",
                "dataset": "headline",
                "sourceId": "phase6_manifest",
                "metrics": [
                    {
                        "label": "Preregistered tests",
                        "field": "hypotheses",
                        "format": "number",
                    },
                    {
                        "label": "Families",
                        "field": "families",
                        "format": "number",
                    },
                ],
            },
            {
                "id": "event_coverage",
                "description": "One observation per eligible session and hypothesis.",
                "dataset": "headline",
                "sourceId": "phase6_manifest",
                "metrics": [
                    {
                        "label": "Event observations",
                        "field": "event_observations",
                        "format": "number",
                    },
                    {
                        "label": "Development sessions",
                        "field": "development_sessions",
                        "format": "number",
                    },
                ],
            },
            {
                "id": "multiplicity_result",
                "description": "Tests surviving all Phase 6 statistical gates.",
                "dataset": "headline",
                "sourceId": "phase6_manifest",
                "metrics": [
                    {
                        "label": "BH rejections",
                        "field": "bh_rejections",
                        "format": "number",
                    },
                    {
                        "label": "Advancing tests",
                        "field": "advancing_hypotheses",
                        "format": "number",
                    },
                ],
            },
            {
                "id": "later_split_usage",
                "description": "Later chronological outcomes remain untouched.",
                "dataset": "headline",
                "sourceId": "phase6_manifest",
                "metrics": [
                    {
                        "label": "Later-split rows used",
                        "field": "final_test_rows_used",
                        "format": "number",
                    },
                    {
                        "label": "Validation rows",
                        "field": "validation_rows_used",
                        "format": "number",
                    },
                ],
            },
        ],
        "charts": [
            {
                "id": "effect_chart",
                "title": "Mean effect across preregistered event studies",
                "description": (
                    "Development-only signed effects in basis points; none "
                    "survives within-family BH correction."
                ),
                "type": "bar",
                "dataset": "ledger",
                "sourceId": "hypothesis_ledger",
                "encodings": {
                    "x": {"field": "short_label", "type": "nominal"},
                    "y": {"field": "mean_effect_bps", "type": "quantitative"},
                },
                "options": {"grouping": "single", "orientation": "horizontal"},
                "showDescription": True,
            }
        ],
        "tables": [
            {
                "id": "ledger_table",
                "title": "Complete Phase 6 hypothesis ledger",
                "description": (
                    "All 21 preregistered tests, sorted by adjusted q-value."
                ),
                "dataset": "ledger_table",
                "sourceId": "hypothesis_ledger",
                "columns": [
                    {"field": "short_label", "label": "Hypothesis", "type": "text"},
                    {"field": "family_label", "label": "Family", "type": "text"},
                    {
                        "field": "observations",
                        "label": "n",
                        "type": "number",
                        "format": "number",
                    },
                    {
                        "field": "mean_effect_bps",
                        "label": "Mean effect",
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
                        "label": "BH q",
                        "type": "number",
                        "format": "number",
                    },
                    {"field": "result_label", "label": "Result", "type": "text"},
                ],
                "defaultSort": {"field": "bh_q_value", "direction": "asc"},
                "showDescription": True,
            },
            {
                "id": "family_table",
                "title": "Phase 6 results by preregistered family",
                "description": (
                    "Every family is sufficiently populated and has zero survivors."
                ),
                "dataset": "families",
                "sourceId": "family_summary",
                "columns": [
                    {"field": "family_label", "label": "Family", "type": "text"},
                    {
                        "field": "hypotheses",
                        "label": "Tests",
                        "type": "number",
                        "format": "number",
                    },
                    {
                        "field": "sample_sufficient_hypotheses",
                        "label": "Sufficient",
                        "type": "number",
                        "format": "number",
                    },
                    {
                        "field": "bh_rejections",
                        "label": "BH rejections",
                        "type": "number",
                        "format": "number",
                    },
                    {
                        "field": "minimum_p_value",
                        "label": "Minimum p",
                        "type": "number",
                        "format": "number",
                    },
                    {
                        "field": "minimum_q_value",
                        "label": "Minimum q",
                        "type": "number",
                        "format": "number",
                    },
                ],
                "defaultSort": {"field": "minimum_q_value", "direction": "asc"},
                "showDescription": True,
            },
        ],
        "sources": sources,
    }
    snapshot = {
        "version": 1,
        "status": "ready",
        "generatedAt": phase6["created_at_utc"],
        "datasets": {
            "headline": headline,
            "ledger": _records(ledger_view),
            "ledger_table": _records(table_view),
            "families": _records(family_view),
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
            "id": "phase6_manifest",
            "label": "Phase 6 analysis manifest",
            "path": "data/manifests/phase6_manifest.json",
            "query": {
                "description": (
                    "Phase 6 coverage, method, gate checks, checksums, and null result."
                ),
                "engine": "duckdb",
                "language": "sql",
                "sql": (
                    "SELECT coverage, method, findings, validation "
                    "FROM read_json_auto('data/manifests/phase6_manifest.json')"
                ),
                "tables_used": ["data/manifests/phase6_manifest.json"],
                "filters": ["input_split = development"],
                "metric_definitions": [
                    "bh_rejections = hypotheses whose within-family Benjamini-Hochberg q-value is at most 0.05",
                    "advancing_hypotheses = sample-sufficient BH rejections whose 95% session-bootstrap interval excludes zero",
                    "event_observations = eligible hypothesis-session outcome rows",
                ],
            },
        },
        {
            "id": "hypothesis_ledger",
            "label": "Phase 6 complete hypothesis ledger",
            "path": "results/phase6/hypothesis_ledger.parquet",
            "query": {
                "description": (
                    "All preregistered tests with effect estimates, session-bootstrap "
                    "intervals, randomized-null p-values, adjusted q-values, and outcomes."
                ),
                "engine": "duckdb",
                "language": "sql",
                "sql": (
                    "SELECT hypothesis_label, family_id, horizon_minutes, "
                    "observations, mean_effect_bps, median_effect_bps, "
                    "positive_effect_rate, bootstrap_ci_low, bootstrap_ci_high, "
                    "permutation_p_value, bh_q_value, result FROM "
                    "read_parquet('results/phase6/hypothesis_ledger.parquet') "
                    "ORDER BY bh_q_value, permutation_p_value"
                ),
                "tables_used": ["results/phase6/hypothesis_ledger.parquet"],
                "filters": [
                    "development split only",
                    "all preregistered hypotheses retained",
                ],
                "metric_definitions": [
                    "mean_effect_bps = average raw forward return for fixed-clock tests or average event-direction times forward return for directional tests",
                    "bootstrap_ci_low/high = 95% percentile interval from 5,000 session resamples",
                    "permutation_p_value = plus-one two-sided randomized-null p-value from 5,000 replicates",
                    "bh_q_value = Benjamini-Hochberg adjusted p-value within the preregistered family",
                ],
            },
        },
        {
            "id": "family_summary",
            "label": "Phase 6 family summary",
            "path": "results/phase6/family_summary.parquet",
            "query": {
                "description": (
                    "Hypothesis, sample-sufficiency, rejection, and survivor "
                    "counts by preregistered family."
                ),
                "engine": "duckdb",
                "language": "sql",
                "sql": (
                    "SELECT * FROM "
                    "read_parquet('results/phase6/family_summary.parquet') "
                    "ORDER BY minimum_q_value"
                ),
                "tables_used": ["results/phase6/family_summary.parquet"],
                "filters": ["all four preregistered families"],
                "metric_definitions": [
                    "sample_sufficient_hypotheses = family tests with at least 100 eligible sessions",
                    "minimum_q_value = smallest within-family adjusted q-value",
                ],
            },
        },
        {
            "id": "phase6_config",
            "label": "Phase 6 frozen preregistration",
            "path": "config/phase6.yaml",
            "query": {
                "description": (
                    "Frozen event families, hypotheses, outcome semantics, "
                    "inference rules, and advancement gate."
                ),
                "tables_used": ["config/phase6.yaml"],
                "filters": [
                    "allowed_split = development",
                    "validation and final_untouched_test forbidden",
                ],
                "metric_definitions": [
                    "minimum_observations = 100",
                    "bootstrap_replicates = 5,000",
                    "permutation_replicates = 5,000",
                    "multiple-testing alpha = 0.05 within family",
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
        default=ROOT / "reports" / "phase6_artifact.json",
    )
    args = parser.parse_args()
    artifact = build_phase6_artifact()
    _write_json_atomic(args.output, artifact)
    print(args.output)


if __name__ == "__main__":
    main()
