# Project Execution Plan

This plan turns the research brief into twelve gated Git checkpoints. A later
phase may begin only after the preceding gate is met or a documented limitation
forces a conservative fallback.

## Operating rules

- Keep NinjaTrader MCP access read-only.
- Use one-minute data for broad discovery and reserve granular validation for
  frozen finalists.
- Preserve raw provider responses and maintain checksums and request manifests.
- Keep the final chronological test period locked through candidate selection.
- Treat failed hypotheses and a null overall result as first-class outcomes.
- Include realistic costs and adverse same-bar handling in tradable results.

## Phase 1: MCP capability audit

Status: complete at commit `3eadb90`.

Gate: provider tools, schemas, granularity, record caps, timestamps, instrument
specifications, and limitations are documented from small read-only probes.

## Phase 2: Data acquisition

Status: complete as of 2026-07-28. All 265 planned one-minute discovery chunks
and 40 paired daily roll requests succeeded, with immutable raw responses and
verified SHA-256 ledger entries.

Build a restartable request planner and immutable response cache. Inventory each
quarterly MNQ contract in the research period, acquire daily bars around rolls,
select executable rolls from lagged volume crossover when possible, and use a
fixed pre-expiry fallback when it is not. Download one-minute data in chunks
small enough to remain below the provider's 10,000-record limit.

Artifacts:

- `data/manifests/contract_inventory.csv`
- `data/manifests/roll_requests.jsonl`
- `data/manifests/minute_requests.jsonl`
- `data/manifests/download_ledger.jsonl`
- Immutable raw response files with SHA-256 checksums
- `reports/data_acquisition_report.md`

Gate: every expected chunk is successful, empty with a documented reason, or
present in the failed-request log. No period is silently skipped.

## Phase 3: Data-quality audit

Status: complete as of 2026-07-28. All 265 raw minute responses passed checksum
and schema verification. The audit created 1,294 session-quality records,
retained 1,241 usable sessions, and machine-excluded 53 sessions with missing
coverage, out-of-calendar timestamps, or mixed-contract roll boundaries. See
`reports/data_quality_audit.md`.

Normalize timestamps to daylight-saving-aware Eastern time while preserving
UTC. Deduplicate only through explicit deterministic rules. Audit OHLCV
integrity, expected session coverage, missing bars, DST, holidays, early closes,
roll gaps, and MNQ/NQ differences. Produce a machine-readable exclusion list.

Gate: severe issues are corrected or excluded and every session has a quality
record. Gate passed.

## Phase 4: Feature construction

Status: complete as of 2026-07-28. The reproducible pipeline retained all
1,701,735 usable bars and all 1,241 usable sessions, catalogued 101
point-in-time features, rejected deliberate leakage injection, preserved the
locked final-test split, and passed an exact byte-for-byte rebuild check. See
`reports/feature_construction_report.md`.

Build point-in-time calendar, prior-day, overnight, opening, level, volatility,
and regime features. Feature timestamps must state when values become
available. Add deliberate leakage tests that must fail.

Gate: features reproduce from normalized data and pass point-in-time tests.
Gate passed.

## Phase 5: Unconditional time analysis

Status: complete as of 2026-07-28. The development-only pipeline measured
1,280,169 fixed-clock forward outcomes across the full Globex and RTH clocks,
produced 1,734 clock/horizon cells and 43,350 stability cuts, quantified
session-extreme timing, rejected validation and final-test rows, and passed a
byte-for-byte rebuild check. See `reports/unconditional_time_analysis.md`.

Using development data only, create the intraday market clock across prescribed
horizons. Measure returns, excursions, timing, uncertainty, and stability by
year, quarter, weekday, and lagged regime.

Gate: descriptive results are complete, sample sizes are visible, and no result
is presented as a strategy. Gate passed.

## Phase 6: Event studies

Preregister interpretable event families, record every test, estimate bootstrap
intervals and permuted nulls, and apply Benjamini-Hochberg correction within
families.

Gate: the full hypothesis ledger includes winners and failures, with no final
test data used.

## Phase 7: Candidate strategy generation

Freeze simple candidates with objective setup, trigger, next-event entry, stop,
exit, maximum hold, cutoff, and missing-data behavior. Measure raw excursions
before testing a restrained exit grid. Apply conservative fills and costs.

Gate: each survivor has unambiguous pseudocode and passes minimum development
and validation evidence requirements.

## Phase 8: Walk-forward validation

Run chronological anchored or rolling folds with purging and embargo where
holding windows overlap. Rank by validation consistency, net expectancy, cost
tolerance, and simplicity.

Gate: candidate definitions and parameters are frozen. Weak candidates are
rejected without consulting the final test.

## Phase 9: Final untouched test

Record a candidate-freeze checksum, unlock the final period once, and evaluate
the frozen candidates exactly once. Do not retune after viewing results.

Gate: final results and acceptance-gate failures are recorded immutably.

## Phase 10: Robustness, Monte Carlo, and granular validation

For finalists only, test parameter neighborhoods, higher costs, delayed or
missed entries, data removals, regimes, contracts, and roll periods. Run
trade-order and clustered day/week Monte Carlo. Request true ticks only if the
provider exposes them; otherwise use the documented granular fallback and
selected NinjaTrader playback.

Gate: a finalist survives the stated gates, or the project records that no
sufficiently robust edge exists.

## Phase 11: NinjaScript implementation

Implement only the strongest survivor with exposed parameters, explicit session
logic, diagnostics, and simulation-only defaults. Reconcile Python results with
Strategy Analyzer, walk-forward optimization, Monte Carlo, and selected
Historical Playback or Market Replay sessions.

Gate: the strategy compiles in NinjaTrader 8 and discrepancies are explained.

## Phase 12: Final report

Publish the executive conclusion first, followed by data, methods, market-clock
findings, ranked candidates, rejected ideas, limitations, and the most
conservative warranted recommendation.

Gate: every headline claim traces to a machine-readable result and the report
does not recommend live deployment from historical research alone.
