# MCP Capability Audit

Date: 2026-07-28  
Connector: `mcp__ninjatrader_demo`  
Mode: demo  
Project boundary: read-only research. No order-entry, account-modification, strategy-enabling, or position-closing tools may be used.

## Executive Summary

The MCP connector exposes enough historical data for the first research stage: individual MNQ/NQ futures contracts, one-minute OHLCV bars, daily bars, and live DOM snapshots. The server enforces a 10,000-bar maximum per `market_history` request, so any multi-year study must be chunked and cached.

Initial probes did not return Tick bars for MNQ, even though `Tick` is listed as a supported `market_history` `barType`. The provider documentation describes `Tick` as range bars aggregated by N ticks, not necessarily raw trade-by-trade tick data. Therefore, the research pipeline must use one-minute bars for broad discovery and reserve granular validation for finalists only, using true ticks if later exposed, otherwise the most granular defensible fallback plus NinjaTrader Strategy Analyzer confirmation.

## Tool Inventory

Discovered tools from the demo NinjaTrader MCP connector:

| Tool | Intended Use | Project Use |
| --- | --- | --- |
| `user_profile` | Identity, MFA, subscription, market-data subscriptions, accounts, add-ons | Capability/entitlement check only |
| `search_contracts` | Find tradable contracts by text, exchange, product type | Allowed for contract discovery |
| `market_history` | Historical OHLC bars | Allowed for read-only historical data |
| `dom_snapshot` | Current depth-of-market snapshot | Allowed for read-only metadata/liquidity inspection |
| `fill_history` | Historical fills | Not needed for market research |
| `order_history` | Historical orders | Not needed for market research |
| `position_history` | Historical positions | Not needed for market research |
| `daily_balance_history` | Historical account balances | Not needed for market research |
| `close_position` | Flatten positions | Prohibited |

The connector description also references order/risk/alert tools in documentation topics, but only the callable tools above were exposed in this session. Do not invent additional tool names.

## Historical Data Tool

Callable tool: `market_history`

Inputs:

| Parameter | Meaning |
| --- | --- |
| `symbol` | Exact contract symbol, for example `MNQU6` |
| `barType` | `Minute`, `Daily`, or `Tick` |
| `barSize` | Minutes for `Minute`, `1` for `Daily`, range size for `Tick` |
| `from` / `to` | ISO-8601 UTC range; must be supplied together |
| `count` | Most-recent bars, mutually exclusive with `from` / `to`; maximum `10000` |
| `closeOnly` | Return timestamp and close only |
| `volumeProfile` | Include per-price-level bid/offer volume histogram |

Output schema:

| Field | Meaning |
| --- | --- |
| `symbol` | Echoed contract symbol |
| `barType` | Echoed aggregation type |
| `barSize` | Echoed aggregation size |
| `bars[].timestamp` | ISO-8601 timestamp, oldest to newest, typically bar start |
| `bars[].open/high/low/close` | OHLC prices |
| `bars[].upVolume/downVolume` | Uptick and downtick volume |
| `bars[].upTicks/downTicks` | Uptick and downtick counts |
| `bars[].histogram[]` | Present only with `volumeProfile=true` |
| `bars[].histogram[].price` | Provider price-level index; requires further normalization before use |
| `bars[].histogram[].bid/offer` | Bid-side and offer-side volume at the level |

Confirmed request limit: `count` must be between 1 and 10,000.

## Instruments Confirmed

`search_contracts` found active MNQ and NQ quarterly futures.

| Symbol | Product | Expiration | Exchange | Tick Size | Tick Value | Point Value |
| --- | --- | --- | --- | ---: | ---: | ---: |
| `MNQU6` | MNQ | 2026-09-18 | CME | 0.25 | $0.50 | $2.00 |
| `NQU6` | NQ | 2026-09-18 | CME | 0.25 | $5.00 | $20.00 |

The provider returned individual contract symbols rather than a continuous contract in the initial search results. Historical research should therefore build an explicit roll policy rather than assuming a back-adjusted continuous series.

## Sample Requests

Small one-minute MNQ sample:

- Request: `MNQU6`, `Minute`, `barSize=1`, `2026-07-27T13:30:00Z` to `2026-07-27T13:40:00Z`
- Result: 11 bars returned
- Timestamp interpretation: UTC timestamps align with 9:30-9:40 AM Eastern RTH open on 2026-07-27
- Sample OHLC integrity: valid in returned bars; highs were greater than or equal to open/close/low and lows were less than or equal to open/close/high

Small daily MNQ sample:

- Request: `MNQU6`, `Daily`, `barSize=1`, `2026-07-20T00:00:00Z` to `2026-07-28T00:00:00Z`
- Result: daily bars returned for 2026-07-20 through 2026-07-28
- Research implication: current-day daily bars may appear before the session is fully complete; the pipeline must explicitly exclude the current incomplete session

Historical depth probe:

- Request: `MNQU6`, `Minute`, 2021 date range
- Result: no bars, as expected for a 2026 contract
- Request: `MNQU1`, `Minute`, `2021-07-27T13:30:00Z` to `2021-07-27T13:35:00Z`
- Result: 6 one-minute bars returned
- Research implication: expired individual contracts are accessible when the exact symbol is known

Tick probe:

- Request: `MNQU6`, `Tick`, `barSize=1`, count-based
- Result: no bars
- Request: `MNQU6`, `Tick`, `barSize=1`, explicit 2026 range
- Result: no bars
- Request: `MNQU1`, `Tick`, `barSize=1` and `barSize=10`, explicit 2021 range
- Result: no bars
- Research implication: Tick bars are documented but not confirmed available for MNQ in this connector. Treat tick-level validation as conditional on later availability.

DOM sample:

- Request: `MNQU6`, depth 3
- Result: current bid/ask ladder returned with ISO-8601 timestamp, best bid, best ask, spread, bid levels, and ask levels
- Research implication: useful for spot-checking live contract metadata, not for historical backtesting

## Data Availability And Limitations

Confirmed:

- Minute bars are available for active and expired individual MNQ contracts when exact symbols are known.
- Daily bars are available.
- DOM snapshots are available for current contracts.
- Volume profile can be requested on minute bars, but the histogram price index needs additional validation before research use.
- Maximum `market_history` response size is 10,000 bars.

Unconfirmed or unavailable in initial probes:

- Raw trade-by-trade ticks
- Historical bid/ask quote series
- Historical order book depth
- Continuous contract symbols
- Provider roll/back-adjustment policy
- Market Replay data access through MCP
- Exact maximum date range per request beyond the 10,000-bar cap
- Historical data entitlement details from `user_profile`, which returned an empty projection in this session

## Required Research Adaptations

1. Use one-minute bars for five-year discovery.
2. Download data in monthly chunks or smaller, with request manifests and checksums.
3. Build a futures roll table from individual contracts and avoid artificial returns across roll gaps.
4. Reserve any tick or granular replay requests for finalist validation only.
5. If true tick data remains unavailable, validate finalists with conservative one-minute intrabar assumptions, optional volume-profile evidence, NinjaTrader Strategy Analyzer high fill resolution where compatible, walk-forward optimization, Monte Carlo, and selected Market Replay or Historical Playback checks.
6. Document every unavailable data source in the final report.

