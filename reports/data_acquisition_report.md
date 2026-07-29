# Data Acquisition Report

Status: Phase 2 acquisition complete as of 2026-07-28.

## Acquisition design

Broad discovery uses one-minute MNQ bars. Tick-level or otherwise granular
requests remain prohibited for the full five-year period and are reserved for
frozen finalists. The provider limit is 10,000 bars per call, so minute
requests are capped at seven calendar days. A seven-day interval can contain at
most 9,660 futures-session minutes before holidays and maintenance gaps.

Every request has a deterministic ID. Saved responses are validated against the
requested symbol and aggregation, written immutably, checksummed with SHA-256,
and recorded in `data/manifests/download_ledger.jsonl`. Re-running an identical
response verifies the cache; a changed payload for the same request is rejected.
The status command derives pending work from the request manifest and ledger,
which makes acquisition resumable. Validation failures are written to
`data/manifests/failed_requests.jsonl` when any occur.

## Contract and roll acquisition

- Quarterly MNQ inventory: 21 contracts from `MNQU1` through `MNQU6`
- Paired daily roll requests: 40
- Successful paired daily responses: 40
- Missing or failed roll responses: 0
- Roll rule: two consecutive completed sessions with next-contract volume above
  front-contract volume, then activate the next contract on the following
  weekday
- Confirmed volume-crossover rolls: 20
- Fallback pre-expiry rolls: 0

The provider's current contract-search endpoint does not list expired symbols,
but exact expired symbols return historical daily and minute data. Contract year
is therefore stored separately from the provider's one-digit compact symbol to
avoid decade ambiguity.

## Minute acquisition progress

- Planned minute chunks: 265
- Completed discovery chunks: 265
- Pending discovery chunks: 0
- Successful responses: 265
- Empty or failed responses: 0
- Completed contract span: `MNQU1` through `MNQU6`, from
  `2021-07-28T04:00:00Z` through `2026-07-28T03:59:00Z`
- Total discovery rows: 1,770,155
- Duplicate timestamps reported during ingestion: 0
- Provider-order breaks reported during ingestion: 1,290
- Immutable raw response files: 265
- Raw response bytes: 279,543,350
- Pilot session rows: 1,380

The raw responses are deliberately not sorted. The provider returns each
futures session in session order and can wrap timestamps within a calendar
request, producing one order break per returned session. For example, the pilot
placed the `22:00-03:59 UTC` block before the `04:00-20:59 UTC` block. Phase 3
will sort and audit timestamps while retaining the immutable provider response.

Seven-day chunk row counts range from 1,380 to 6,901 because the manifest
contains partial contract-boundary intervals, holiday weeks, maintenance gaps,
and provider variations. These differences are not silently repaired during
acquisition; Phase 3 will classify exact missing minutes and expected closures.

## Current limitations and remaining work

The full planned one-minute MNQ discovery acquisition is complete. Raw
responses for the roll study, pilot, and all 265 discovery chunks are cached
locally. Large raw JSON files are excluded from Git, while request manifests,
row counts, timestamp bounds, paths, and checksums are tracked.

Phase 3 must sort and normalize the provider's session-ordered bars, audit
calendar and roll-boundary gaps, and construct the chronological development,
validation, and untouched final-test datasets without leakage. NQ confirmation
data remains deferred until strong MNQ candidates exist rather than doubling
the broad discovery download before an edge is demonstrated.
