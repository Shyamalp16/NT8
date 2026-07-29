# Phase 5 Unconditional Time Analysis

## Technical summary

The Phase 5 gate **passed** using development data only:
1,021,050 one-minute bars across
744 usable Globex sessions and
743 sessions with RTH observations. The
pipeline measured 1,734 unconditional
clock/horizon cells and 43,350 calendar and
lagged-regime cuts. It did not inspect validation or final-test outcomes and did
not create a strategy.

Among non-overlapping 30-minute RTH blocks, the largest development-sample mean
was 14:30-15:00 at
2.47 bps (95% CI
0.33 to
4.61; n=721).
The smallest was 12:00-12:30 at
-1.66 bps (95% CI
-3.58 to
0.27; n=743).
These are unadjusted descriptive extrema, not signals or evidence of
tradability.

## Key descriptive findings

- The strongest 30-minute block was positive in
  4 of 4 sufficiently
  populated calendar-year cuts; the weakest was negative in
  2 of 4.
- The most common first RTH session-high bin was 09:30-10:00
  (29.9% of 743 sessions).
- The most common first RTH session-low bin was 09:30-10:00
  (32.6% of 743 sessions).
- The RTH high occurred before the RTH low in
  48.0% of sessions. This is descriptive
  ordering, not an executable entry rule.

## Scope and metric definitions

- **Development population:** the frozen development split only. Validation and
  final-test rows are prohibited.
- **ETH full-session clock:** the contiguous 18:00-17:00 Eastern Globex
  session.
- **RTH clock:** 09:30-16:00 Eastern, analyzed separately and nested inside the
  full-session clock.
- **Anchors:** fixed five-minute clock starts.
- **Horizons:** 1, 5, 15, 30, and 60 minutes.
- **Return:** final included one-minute bar close divided by the fixed-clock
  entry bar open, minus one, reported in basis points.
- **MFE/MAE:** highest high and lowest low during the interval relative to the
  entry bar open. These are descriptive bar excursions, not simulated fills.
- **Uncertainty:** two-sided 95% Student-t intervals for the mean and Wilson
  intervals for the positive-return rate.

## Stability analysis

Every clock/horizon cell was cut by calendar year, calendar quarter, weekday,
and the strictly lagged trend, volatility, and volume regimes produced in Phase
4. A cut is marked sufficiently populated at 30 observations. Sample sizes,
means, medians, dispersion, return quartiles, positive rates, excursions, and
intervals remain available in the machine-readable outputs. Overlapping
horizons are correlated, and no multiple-testing correction is applied in this
descriptive phase.

## Gate evidence

- PASS: `development_split_only`
- PASS: `locked_final_rows_absent`
- PASS: `phase4_development_row_count_preserved`
- PASS: `outcomes_nonempty`
- PASS: `required_scopes_complete`
- PASS: `required_horizons_complete`
- PASS: `outcome_keys_unique`
- PASS: `market_clock_keys_unique`
- PASS: `stability_keys_unique`
- PASS: `stability_dimensions_complete`
- PASS: `sample_sizes_visible_and_bounded`
- PASS: `mean_confidence_intervals_valid`
- PASS: `positive_rate_intervals_valid`
- PASS: `excursion_direction_valid`
- PASS: `timing_session_counts_valid`
- PASS: `turning_point_shares_sum_to_one`
- PASS: `analysis_is_descriptive_only`
- PASS: `byte_reproducible_rebuild`

## Reproducible artifacts

- `results/phase5/market_clock.parquet`
- `results/phase5/market_clock_stability.parquet`
- `results/phase5/market_clock_stability_summary.parquet`
- `results/phase5/session_timing.parquet`
- `results/phase5/turning_point_timing.parquet`
- `results/phase5/report_clock_blocks.parquet`
- `data/manifests/phase5_manifest.json`
- `config/phase5.yaml`
- `src/pattern_scan.py`
- `src/statistics.py`
- `tests/test_pattern_scan.py`

## Limitations and Phase 6 boundary

- All results are exploratory, descriptive, and unadjusted for the many clock cells examined.
- Overlapping forward horizons are correlated and must not be treated as independent tests.
- One-minute OHLC bars do not reveal within-bar path or executable fill sequence.
- The full-session and RTH clocks overlap because RTH is nested inside the Globex session.
- Early-close sessions contribute only to clock cells whose entire horizon is observed.
- No validation or final-test outcomes were inspected, and no strategy or tradable performance was produced.

Phase 6 may use these descriptive maps to preregister a limited set of
interpretable event families. It must retain failures, use bootstrap and
permuted-null inference, and correct within-family multiplicity before any
event is considered for candidate generation.
