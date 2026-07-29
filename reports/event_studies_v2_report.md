# Phase 6 v2 Event Studies

## Decision

At least one frozen v2 hypothesis cleared every gate. The ledger retains all four tests, including failures.
Validation and final-test usage remain zero. These are event-study screens, not
strategies or evidence of tradability.

## Complete hypothesis ledger

- `volatility_state_transition__15m`: effect 21.138 bps, 95% cluster CI [18.755, 23.551], p=0.000200, within-family q=0.000200, cumulative q=0.002500, n=3114 observations / 658 sessions — advances to phase7.
- `volatility_state_transition__30m`: effect 29.102 bps, 95% cluster CI [25.737, 32.511], p=0.000200, within-family q=0.000200, cumulative q=0.002500, n=3109 observations / 658 sessions — advances to phase7.
- `signed_pressure_burst__15m`: effect 0.593 bps, 95% cluster CI [-1.553, 2.734], p=0.594881, within-family q=0.600480, cumulative q=0.841221, n=523 observations / 523 sessions — does not advance.
- `signed_pressure_burst__5m`: effect -0.369 bps, 95% cluster CI [-1.766, 0.921], p=0.600480, within-family q=0.600480, cumulative q=0.841221, n=523 observations / 523 sessions — does not advance.

## What is genuinely new

- **Volatility-state transition:** conditions on an extreme, strictly lagged
  same-clock volatility state and measures future range, not mean direction.
- **Signed pressure burst:** conditions on the first extreme five-minute
  uptick/downtick volume-pressure proxy and measures direction-aligned return.
  The proxy is not historical bid/ask aggressor imbalance.

## Frozen inference and advancement gate

- Session-cluster percentile bootstrap: 5,000 replicates.
- Randomized nulls: 5,000 replicates with two-sided plus-one p-values.
- Primary correction: Benjamini-Hochberg within each new family.
- Sensitivity correction: Benjamini-Hochberg across all 21 v1 and 4 v2 tests.
- Advancement requires minimum samples, both BH gates, and a cluster-bootstrap
  interval excluding zero.

## Gate evidence

- PASS: `preregistration_hash_matches_receipt`
- PASS: `preregistered_before_outcomes`
- PASS: `development_split_only`
- PASS: `locked_final_rows_absent`
- PASS: `development_row_count_preserved`
- PASS: `exactly_four_registered_hypotheses`
- PASS: `only_registered_observations`
- PASS: `next_bar_entry_enforced`
- PASS: `strictly_lagged_minimum_history_enforced`
- PASS: `volatility_state_thresholds_enforced`
- PASS: `pressure_thresholds_enforced`
- PASS: `first_pressure_event_per_session`
- PASS: `volatility_clock_events_unique`
- PASS: `forward_outcomes_finite`
- PASS: `family_summary_complete`
- PASS: `sample_sizes_visible`
- PASS: `bootstrap_intervals_valid`
- PASS: `permutation_p_values_valid`
- PASS: `within_family_q_values_valid`
- PASS: `cumulative_q_values_valid`
- PASS: `advancement_rule_enforced`
- PASS: `analysis_is_event_study_not_strategy`
- PASS: `byte_reproducible_rebuild`

## Scope boundary

- Development bars: 1,021,050 across
  744 sessions.
- Event observations: 7,269.
- Validation rows used: 0.
- Final-test rows used: 0.
- No commissions, slippage, orders, stops, targets, sizing, or fill simulation.
