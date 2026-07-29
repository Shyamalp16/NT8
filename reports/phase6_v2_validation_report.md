# Phase 6 v2 Validation Report

## Overall assessment: Share with caveats

The Phase 6 v2 analysis is methodologically sound and reproducible as a
development-sample event study. Both volatility-state hypotheses legitimately
clear the frozen statistical gate. The result is ready to support a narrow
Phase 7 candidate-generation decision, but it is not a directional or tradable
edge by itself.

## Methodology review

- The exact four-hypothesis configuration was checksum-registered before the
  first v2 outcome run.
- Only the frozen development split was loaded: 1,021,050 one-minute bars from
  744 sessions. Validation and final-test row usage is zero.
- Every feature is observed at a trigger-bar close and every outcome begins at
  the next bar open. The trailing 30-bar volatility feature and future outcome
  therefore do not overlap.
- State thresholds use only the strictly prior 60 same-clock usable sessions,
  with at least 40 historical observations.
- Multiple same-session clock events are preserved inside session-cluster
  bootstrap resamples. State labels are permuted within entry-clock strata.
- Multiplicity is controlled both within each new family and cumulatively
  across all 21 v1 plus 4 v2 p-values.

## Issues found

1. **Medium — the survivor is non-directional.** A wider future high-low range
   does not identify a long or short futures position. Phase 7 may generate a
   small set of volatility-conditioned mechanisms, but it may not label this
   event study itself a strategy.
2. **Medium — all inferential evidence is still development-sample evidence.**
   The positive result has not yet been confirmed on the frozen validation
   period. The final chronological test remains locked.
3. **Low — candidate fills will be path-sensitive.** Breakout or paired
   stop-entry mechanisms can be materially distorted by one-minute OHLC
   ambiguity. Phase 7 must impose next-event entry, conservative same-bar
   ordering, slippage, and commissions before reporting tradable results.
4. **Low — pressure semantics are limited.** `up_volume` and `down_volume` are
   provider-described uptick/downtick volume, not historical bid/ask aggressor
   volume. That family failed and should be dropped.

## Calculation spot-checks

- **15-minute volatility effect: verified.** High-state mean future range is
  40.9651 bps and low-state mean is 19.8275 bps; the independently recomputed
  difference is 21.1375 bps. The 95% session-cluster interval is 18.7550 to
  23.5507 bps.
- **30-minute volatility effect: verified.** High-state mean future range is
  56.1342 bps and low-state mean is 27.0325 bps; the independently recomputed
  difference is 29.1018 bps. The 95% session-cluster interval is 25.7366 to
  32.5111 bps.
- **Multiplicity: verified.** Both volatility tests have raw and within-family
  q-values of 0.000200 and cumulative 25-test q-values of 0.002500.
- **Annual sign stability: verified.** Both horizon effects are positive in
  every 2021–2024 calendar-year diagnostic.
- **Structural integrity: verified.** There are no duplicate event keys, no
  non-finite outcomes, all output rebuilds are byte-identical, and all 42
  repository tests pass.

## Visualization review

The validated report uses grouped bars for the high-versus-low range
comparison and calendar-year diagnostics. Both charts use zero-based magnitude
comparisons, explicit basis-point definitions, meaningful state/horizon
grouping, and source-backed reviewed datasets. The four-row audit table keeps
event counts, session clusters, intervals, raw p-values, both q-values, and
gate results visible.

## Required caveats

- This is evidence of conditional volatility persistence, not return direction,
  causality, executable expectancy, or profitability.
- Validation and final-test outcomes remain untouched.
- Any Phase 7 mechanism must be frozen before its outcomes are measured and
  must include realistic costs and conservative fill assumptions.
- NinjaTrader Strategy Analyzer, walk-forward, Monte Carlo, and selected
  playback checks remain later independent confirmation steps.

## Reproducibility

- `config/phase6_v2.yaml`
- `data/manifests/phase6_v2_preregistration.json`
- `src/event_studies_v2.py`
- `src/statistics.py`
- `results/phase6_v2/`
- `data/manifests/phase6_v2_manifest.json`
- `reports/phase6_v2_artifact.json`
- `tests/test_event_studies_v2.py`
- `tests/test_phase6_v2_artifact.py`
