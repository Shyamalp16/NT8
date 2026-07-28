# Data Acquisition Report

Status: Phase 2 in progress as of 2026-07-28.

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
- Completed discovery chunks: 7
- Pending discovery chunks: 258
- Completed contract segment: `MNQU1`, 2021-07-28 through the selected
  2021-09-14 roll boundary
- Rows in the completed MNQU1 segment: 46,678
- Duplicate timestamps reported during ingestion: 0
- Provider-order breaks reported during ingestion: 34
- Pilot session rows: 1,380

The raw responses are deliberately not sorted. The provider returns each
futures session in session order and can wrap timestamps within a calendar
request, producing one order break per returned session. For example, the pilot
placed the `22:00-03:59 UTC` block before the `04:00-20:59 UTC` block. Phase 3
will sort and audit timestamps while retaining the immutable provider response.

One seven-day chunk returned 6,899 rather than 6,900 rows, and the Labor Day
week returned fewer bars. These differences are not silently repaired during
acquisition; Phase 3 will classify the exact missing minute and expected holiday
closures.

## Current limitations and remaining work

The full one-minute acquisition is intentionally incomplete. Raw responses for
the roll study, pilot, and first contract segment are cached locally; large raw
JSON files are excluded from Git, while their request manifests, row counts,
timestamp bounds, paths, and checksums are tracked.

Phase 2 completes only when all remaining minute chunks are successful, empty
for a documented reason, or present in the failure log after retries. NQ
confirmation data will be requested later for strong candidates rather than
doubling the broad discovery download before an MNQ edge exists.

