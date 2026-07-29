# Phase 6 Validation Report

## Overall assessment: Ready to share as a null event-study result

Phase 6 answers the preregistered development-sample question and retains all
21 hypotheses across four families. No hypothesis passes the within-family
Benjamini-Hochberg gate, so no event is eligible for Phase 7 candidate
generation. This is a valid research outcome rather than a pipeline failure.

## Methodology review

- Population: 1,021,050 one-minute development bars from 744 usable sessions,
  spanning 2021-07-29 through 2024-07-26.
- Families: 13 non-overlapping 30-minute RTH blocks, two overnight-gap response
  horizons, two opening-30 response horizons, and four reference-range break
  response hypotheses.
- Outcomes: anchor-bar open for fixed-clock, gap, and opening events; next-bar
  open after the confirming close for range breaks; final included bar close
  at the stated horizon.
- Directional statistic: event direction multiplied by forward return, so a
  positive mean indicates continuation and a negative mean indicates reversal.
- Inference: 5,000 session-bootstrap replicates, 5,000 randomized-null
  replicates, plus-one two-sided p-values, and Benjamini-Hochberg correction
  within each family at 5%.
- Advancement: at least 100 observations, BH rejection, and a 95% bootstrap
  interval excluding zero.

The design is inferential but remains an event study. It does not simulate
orders, costs, stops, targets, sizing, or fills.

## Issues found

1. **Low severity — same-sample lineage:** Phase 5 described the development
   period and Phase 6 tests the same period. Keeping all 13 fixed-clock blocks
   in the family reduces isolated-extrema cherry-picking, but the Phase 6 result
   is not independent out-of-sample confirmation.
2. **Low severity — family-scoped multiplicity:** BH correction is applied
   within each preregistered family, not across every event definition that
   could have been imagined before or after this phase.
3. **Informational — one-minute granularity:** Close-confirmed break events use
   the next minute's open, but one-minute OHLC bars cannot establish within-bar
   path or execution quality.

No issue requires revision before sharing the Phase 6 null conclusion.

## Calculation spot-checks

- **Fixed-clock reconciliation:** all 13 Phase 6 observation counts exactly
  match the corresponding Phase 5 non-overlapping 30-minute RTH blocks. The
  maximum absolute mean difference is 4.44e-16 bps.
- **14:30-15:00 RTH:** independently recomputed from 721 Phase 6 observations as
  2.469246 bps mean and 3.048780 bps median, matching Phase 5. Its randomized
  p-value is 0.023995, but its within-family q-value is 0.311938, so it does not
  advance.
- **Opening-30, 60-minute response:** independently recomputed from 596 events
  as 1.372722 bps by both the saved effect and
  `event_direction * forward_return`.
- **Prior-RTH break, 15-minute response:** 600 events have zero next-bar timing
  violations; the mean signed effect is 0.259975 bps.
- **Effect identity:** every directional observation exactly satisfies signed
  effect = event direction × forward return.
- **Keys and boundaries:** no duplicate hypothesis/session rows occur; event
  dates run only from 2021-07-29 through 2024-07-26.
- **Multiplicity and advancement:** the ledger contains all 21 preregistered
  hypotheses, with zero BH rejections and zero advancing events.
- **Reproducibility:** all four Parquet artifacts rebuild to byte-identical
  SHA-256 hashes.

## Visualization review

The reader-facing artifact uses a zero-context signed bar chart for mean event
effects, with exact sample sizes, bootstrap bounds, p-values, and q-values
retained in the backing data. A complete hypothesis table is sorted by q-value
so the strongest apparent result is visible without hiding failures.

## Required caveats

- No Phase 6 result establishes a tradable strategy.
- The 14:30-15:00 raw mean is a multiplicity-adjusted failure despite its
  unadjusted p-value and bootstrap interval.
- Validation and final-test outcomes remain untouched.
- With no advancing event, Phase 7 should not manufacture a candidate from this
  Phase 6 specification.
