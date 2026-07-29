# NinjaTrader 8 Intraday Pattern Research

This repository investigates whether repeatable, time-dependent intraday patterns exist in MNQ/NQ futures and whether any survive realistic costs, conservative execution assumptions, and chronological out-of-sample testing.

The project deliberately uses a staged data plan:

1. Inspect provider capabilities before large requests.
2. Use one-minute bars for broad market-clock discovery and candidate generation.
3. Validate only finalist candidates with true tick data if available, or the most granular defensible fallback if not.
4. Confirm finalists independently in NinjaTrader 8 Strategy Analyzer using backtesting, walk-forward optimization, Monte Carlo workflows, and selected Market Replay or Historical Playback checks.

The current MCP audit found usable minute and daily bars for individual MNQ contracts, a 10,000-bar request cap, and no returned Tick bars in the initial probes. See `reports/mcp_capability_audit.md`.

## Layout

- `config/` central assumptions and chronological splits
- `data/raw/` immutable provider responses and request manifests
- `data/normalized/` normalized research series
- `data/features/` feature tables built without look-ahead
- `data/manifests/` request logs, checksums, row counts, and exclusions
- `src/` research pipeline modules
- `tests/` leakage, schema, and data-quality tests
- `notebooks/` exploratory notebooks
- `reports/` human-readable audit and research reports
- `results/` machine-readable discovery and validation outputs
- `ninjascript/` NinjaTrader strategy implementations for finalists

## Current Phase

Phases 1 through 6 are complete. Phase 6 v1 retained all 21 development-only
tests and found no multiplicity-adjusted directional-return event. A separately
preregistered v2 then tested four genuinely new hypotheses: two
volatility-state transitions and two signed uptick/downtick pressure bursts.
Both volatility-state horizons survived within-family and cumulative 25-test
Benjamini-Hochberg correction plus session-cluster interval gates; both pressure
tests failed. Validation and final-test outcomes remain untouched.

Phase 7 is now narrowly unblocked for volatility-conditioned candidate
generation. The Phase 6 v2 result forecasts wider future ranges, not direction,
so it does not itself define a long or short strategy. Any breakout,
stop-entry, or other executable mechanism must be frozen before testing and
must include costs, conservative next-event fills, and same-bar ambiguity
rules. See `reports/event_studies_v2_report.md` and
`reports/project_execution_plan.md`.
