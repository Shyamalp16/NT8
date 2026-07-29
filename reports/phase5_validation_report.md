# Phase 5 Validation Report

## Overall assessment: Ready to share for Phase 6 preregistration

Phase 5 answers the intended descriptive question using the frozen development
split only. Its outputs are suitable for designing preregistered Phase 6 event
families. They are not suitable for strategy selection or a claim of
tradability.

## Methodology review

- Population: 1,021,050 one-minute development bars from 744 usable Globex
  sessions, including 743 sessions with at least one RTH observation.
- Period: 2021-07-29 through 2024-07-26, Eastern session-date convention.
- Clocks: full 18:00-17:00 Globex session and nested 09:30-16:00 RTH clock.
- Fixed starts: five-minute anchors with 1, 5, 15, 30, and 60-minute horizons.
- Return: entry-bar open to the final included bar close.
- Excursions: interval high and low relative to the entry-bar open.
- Uncertainty: 95% Student-t intervals for means and Wilson intervals for
  positive-return rates.
- Stability cuts: year, quarter, weekday, and strictly lagged trend,
  volatility, and volume regimes. A cut is marked sufficiently populated at 30
  observations.

The design is descriptive. Overlapping horizons remain correlated and no
multiple-testing correction is applied until Phase 6.

## Issues found

1. **Low severity — multiplicity:** Many clock cells are inspected, so isolated
   confidence intervals excluding zero can occur by chance. All reader-facing
   findings are explicitly labeled unadjusted and exploratory.
2. **Low severity — incomplete RTH sessions:** Early-close sessions contribute
   only where a complete horizon exists. Sample sizes are stored for every
   cell; for example, 14:30-15:00 has 721 observations rather than 743.
3. **Low severity — bar granularity:** One-minute OHLC data cannot determine
   within-bar path or executable fills. Phase 5 reports excursions rather than
   simulated trades.
4. **Informational — nested scopes:** The full Globex session contains RTH.
   Results from the two scopes are separate views, not independent samples.

No material issue requires revision before Phase 6.

## Calculation spot-checks

- **14:30-15:00 RTH mean return:** independently recomputed from the Phase 4
  feature table as 2.469246 basis points across 721 complete intervals, matching
  the Phase 5 aggregate.
- **14:30-15:00 RTH median and positive rate:** independently recomputed as
  3.048780 basis points and 56.172%, matching the saved result.
- **14:30-15:00 RTH excursions:** independently recomputed as +19.689819 mean
  favorable basis points and -18.003570 mean adverse basis points.
- **Split boundaries:** session timing outputs span 2021-07-29 through
  2024-07-26 only. No validation or final-test session appears.
- **Turning-point distributions:** high-bin and low-bin shares each sum to
  100% within both scopes.
- **Reproducibility:** all six Parquet artifacts rebuilt to exactly the same
  SHA-256 bytes.

## Visualization review

The durable report uses:

- A zero-baseline bar chart for non-overlapping 30-minute RTH mean returns.
- A line chart for the five-minute RTH market-clock profile.
- A grouped bar chart for RTH high/low timing, with the extreme type visibly
  encoded.
- A table for exact calendar-year stability values.

Charts retain observations, intervals, medians, positive rates, and excursions
in their backing datasets so the plotted means are not presented without sample
or dispersion context.

## Required caveats for readers

- Results are descriptive, exploratory, and unadjusted for multiplicity.
- A confidence interval excluding zero in Phase 5 is not a candidate strategy.
- Overlapping horizons are correlated.
- One-minute bars do not establish tradable fill sequences.
- Validation and final-test data remain untouched.

## Phase 6 requirement

Phase 6 should preregister a restrained set of interpretable event families,
record every hypothesis, use session-aware bootstrap intervals and permuted
nulls, and apply Benjamini-Hochberg correction within families before any
result advances to candidate generation.
