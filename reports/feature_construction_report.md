# Phase 4 Feature Construction Report

## Outcome

The Phase 4 gate **passed**. The pipeline built point-in-time session and
one-minute MNQ features from the immutable Phase 3 normalized dataset. It
retained 1,701,735 usable bars across
1,241 usable sessions.

No strategy, candidate, or performance analysis was run. The locked final-test
split was constructed only so the frozen feature rules can later be applied
without reinterpretation; the analysis loader rejects that split unless an
explicit final-evaluation unlock is supplied.

## Feature contract

- Families: calendar, level, opening, overnight, prior_session, regime, volatility
- Catalogued features: 101
- Bar timestamps denote opens; bar-derived features become available one minute
  later and are suitable only for next-bar-open decisions.
- Prior-session and overnight values use the immediate expected predecessor.
  They do not skip over excluded sessions.
- Rolling regimes are strictly lagged and require complete expected-session
  windows.

## Output coverage

- Development rows: 1,021,050
- Validation rows: 342,195
- Locked final-test rows: 338,490

## Gate evidence

- PASS: `usable_row_count_preserved`
- PASS: `bar_timestamps_unique`
- PASS: `usable_session_count_preserved`
- PASS: `frozen_split_labels_preserved`
- PASS: `excluded_predecessors_invalidate_features`
- PASS: `point_in_time_availability`
- PASS: `catalog_matches_bar_schema`
- PASS: `bar_close_features_delayed_one_minute`
- PASS: `calculation_spot_checks`
- PASS: `research_boundaries_preserved`
- PASS: `deliberate_leakage_injection_rejected`
- PASS: `locked_final_split_analysis_rejected`
- PASS: `byte_reproducible_rebuild`

## Reproducible artifacts

- `data/features/mnq_session_features.parquet`
- `data/features/mnq_1m_features.parquet`
- `data/manifests/feature_catalog.json`
- `data/manifests/feature_manifest.json`
- `config/features.yaml`
- `tests/test_features.py`

## Known limitations

- Opening-window features are null on sessions that close before the window ends.
- Prior-session, overnight, and lagged-regime features are null whenever required expected predecessor sessions are excluded.
- One-minute bars imply next-bar-open availability for bar-close-derived features.
