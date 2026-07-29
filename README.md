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

Phases 1 through 5 are complete. Phase 5 used the development split only to
build separate full-session and RTH market clocks at five-minute anchors across
1, 5, 15, 30, and 60-minute horizons. It produced 1,734 unconditional
clock/horizon cells, 43,350 year/quarter/weekday/lagged-regime stability cuts,
and session high/low timing distributions. All outputs passed split-lock,
coverage, interval, uniqueness, and byte-reproducibility gates. No validation
or final-test outcomes were inspected, and no strategy or tradable performance
was produced. Phase 6 event studies are next. The full sequence and phase gates
are documented in `reports/project_execution_plan.md`.
