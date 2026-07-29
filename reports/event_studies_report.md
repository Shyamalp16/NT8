# Phase 6 Event Studies

## Technical summary

The Phase 6 gate passed using the frozen development split only.
No preregistered event passed both the within-family BH gate and the bootstrap interval gate. The full ledger retains all 21
preregistered hypotheses, including failures. Validation and final-test usage
remain zero.

These are event-study screens, not strategies or evidence of tradability.
Positive directional effects mean continuation; negative effects mean reversal.

## Family-level results

- `opening_30_response`: 2 tests, 0 BH rejections, 0 advancing.
- `overnight_gap_response`: 2 tests, 0 BH rejections, 0 advancing.
- `reference_level_break_response`: 4 tests, 0 BH rejections, 0 advancing.
- `rth_fixed_clock_30m`: 13 tests, 0 BH rejections, 0 advancing.

## Events eligible for Phase 7 consideration

- None.

Eligibility requires at least 100
session observations, a within-family Benjamini-Hochberg rejection, and a 95%
session-bootstrap interval that excludes zero. Phase 7 must still freeze an
unambiguous setup, next-event entry, exits, costs, and conservative fill rules.

## Scope and definitions

- Population: 1,021,050 one-minute development bars from
  744 usable sessions.
- Outcome: entry-bar open to the final included bar close, in basis points.
- Fixed-clock events enter at the anchor bar open.
- Opening and gap features are known at their anchor; range breaks require a
  confirming close and enter at the next bar open.
- Directional effect: event direction multiplied by forward return.
- No commissions, slippage, order simulation, stop, target, or sizing model is
  included.

## Inference and multiplicity

- Session-level percentile bootstrap with
  5,000 replicates.
- Session-level randomized nulls with
  5,000 replicates.
- Two-sided p-values with the plus-one correction.
- Benjamini-Hochberg within family at alpha=0.05.
- Deterministic hypothesis-specific random seeds.

## Limitations and robustness boundary

- Phase 5 descriptive findings and Phase 6 inference use the same development
  period. The full 13-block clock family mitigates isolated-extrema selection,
  but this is not independent out-of-sample confirmation.
- Correction is within the four preregistered families, not across every event
  definition that could have been imagined.
- One-minute bars cannot establish within-bar paths or executable fills.
- Annual cuts are diagnostics only and are not additional selection gates.
- Validation and final-test outcomes remain untouched.

## Gate evidence

- PASS: `preregistration_flag_frozen`
- PASS: `development_split_only`
- PASS: `locked_final_rows_absent`
- PASS: `phase4_development_row_count_preserved`
- PASS: `hypothesis_ids_unique`
- PASS: `full_hypothesis_ledger_retained`
- PASS: `no_unregistered_observations`
- PASS: `one_observation_per_session_hypothesis`
- PASS: `event_observations_nonempty`
- PASS: `family_summary_complete`
- PASS: `sample_sizes_visible`
- PASS: `bootstrap_intervals_valid`
- PASS: `permutation_p_values_valid`
- PASS: `bh_q_values_valid`
- PASS: `advancement_rule_enforced`
- PASS: `all_failures_retained`
- PASS: `analysis_is_event_study_not_strategy`
- PASS: `byte_reproducible_rebuild`

## Reproducible artifacts

- `config/phase6.yaml`
- `results/phase6/event_observations.parquet`
- `results/phase6/hypothesis_ledger.parquet`
- `results/phase6/family_summary.parquet`
- `results/phase6/hypothesis_year_stability.parquet`
- `data/manifests/phase6_manifest.json`
- `src/event_studies.py`
- `src/statistics.py`
- `tests/test_event_studies.py`
