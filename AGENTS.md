# Project Guardrails

This repository is for read-only NinjaTrader 8 intraday strategy research.

Do not place live, demo, simulated, or account-modifying orders from MCP during this project. The MCP connector is used only for capability inspection, contract lookup, and historical/read-only market-data requests.

Core research constraints:

- Inspect provider capabilities before assuming data depth or granularity.
- Use one-minute bars for broad discovery.
- Use tick data only for finalist validation when true tick data is exposed; otherwise use the most granular defensible provider data and document the limitation.
- Exclude the current incomplete session.
- Keep the final chronological test period untouched until candidate rules are frozen.
- Include commissions, slippage, conservative fill assumptions, and same-bar ambiguity handling in all tradable results.
- Treat "no sufficiently robust edge was found" as a valid project outcome.
- Use NinjaTrader Strategy Analyzer, walk-forward optimization, Monte Carlo, and selected Market Replay or Historical Playback checks as independent confirmation after the Python research pipeline identifies finalists.

