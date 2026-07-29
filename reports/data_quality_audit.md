# Phase 3 Data-Quality Audit

Status: complete. The Phase 3 gate passed on 2026-07-28.

## Technical summary

The one-minute MNQ dataset is suitable for Phase 4 feature construction only
when downstream code filters to `session_usable = true`. The audit produced
1,769,795 normalized in-scope bars, of which 1,701,735 bars across 1,241
sessions are usable. Fifty-three of 1,294 expected sessions are excluded; the
excluded bars remain in the normalized Parquet file with explicit flags for
traceability.

All 265 source responses passed their SHA-256 checks. No invalid OHLCV rows,
off-tick prices, negative volumes, duplicate timestamps, or conflicting
duplicate bars were found. The final chronological test period was inspected
only for structural data quality; no returns, strategies, or performance
results were calculated.

## Most sessions are usable, with exclusions concentrated in known failure modes

| Split | Quality records | Usable sessions | Excluded sessions |
| --- | ---: | ---: | ---: |
| Development | 777 | 744 | 33 |
| Validation | 258 | 250 | 8 |
| Final untouched test | 259 | 247 | 12 |
| **Total** | **1,294** | **1,241** | **53** |

The usable-session rate is 95.9%, and the usable-row rate is 96.2%. Exclusions
are deliberately conservative:

- Twenty roll-date sessions are excluded because Phase 2 switched from the
  front contract to the next contract at midnight Eastern rather than at the
  18:00 Eastern session open. Seventeen of these sessions otherwise have full
  minute coverage; three also contain a missing or out-of-calendar minute.
- Thirty non-roll sessions are excluded for missing expected minutes.
- Three additional non-roll sessions contain a timestamp outside the expected
  calendar interval.

No gaps were imputed and no roll adjustment was applied.

## Missing data are fully enumerated

Across expected sessions, 2,969 minutes are missing. Only 30 of those minutes
fall in regular trading hours, across five sessions. The largest exclusions
are:

- March 29, 2024 and April 18, 2025: no provider bars for two calendar-scheduled
  915-minute Good Friday sessions.
- November 28, 2025: 645 missing overnight minutes in an early-close session;
  regular trading hours are intact.
- July 28, 2021: the first research-date session begins at midnight Eastern and
  is missing its first 360 minutes, so the first usable session is July 29.
- January 9, 2025 initially appeared to be missing 450 regular-hours minutes.
  CME's National Day of Mourning schedule instead closed U.S. equity-index
  products at 08:30 Central / 09:30 Eastern. The audit applies this documented
  one-off close and the observed 930 minutes then match exactly.

The one-off January 9 close is supported by
[CME Special Executive Report 9499R](https://www.cmegroup.com/content/dam/cmegroup/notices/ser/2025/01/ser-9499r.pdf).
Regular and holiday sessions use the product-specific `CME Globex Equity`
calendar, consistent with [CME's published trading-hours framework](https://www.cmegroup.com/trading-hours.html).

Every missing run is stored in
`data/manifests/missing_minute_runs.jsonl`; every excluded session and its
action are stored in `data/manifests/exclusions.jsonl`.

## Scope and definitions

- Source scope: 265 immutable `Minute(1)` MNQ responses and 1,770,155 raw rows.
- Research trade dates: July 28, 2021 through July 27, 2026.
- Session definition: CME Globex Equity trade date, normally 18:00 Eastern on
  the prior calendar day through 17:00 Eastern on the trade date.
- Bar timestamp definition: start of the half-open interval
  `[timestamp, timestamp + 1 minute)`.
- RTH definition: 09:30 through 15:59 Eastern.
- Primary key: `symbol + timestamp_utc`; the stitched series is additionally
  checked for multiple symbols within a trade date.
- Usable session: an expected session with complete calendar coverage, no
  invalid bars, no conflicting duplicate keys, no timestamps outside the
  expected interval, and no mixed-contract roll boundary.

The raw cache remains unchanged. The normalized file preserves a UTC timestamp
and an Eastern ISO-8601 timestamp with its `-04:00` or `-05:00` offset.

## Methodology

1. Recalculate and verify each raw file's SHA-256 checksum against the immutable
   download ledger.
2. Validate response symbol, aggregation, row count, required fields, numeric
   finiteness, OHLC ordering, positive prices, non-negative volume/ticks,
   one-minute alignment, and 0.25-point tick alignment.
3. Sort all bars chronologically and assign daylight-saving-aware Eastern
   timestamps and CME trade dates.
4. Detect exact and conflicting duplicate keys. The deterministic correction
   rule, if needed, keeps the lexicographically first request ID and source row;
   no duplicates were present in this dataset.
5. Compare every expected session with the CME Globex Equity calendar,
   including early closes and the documented January 9, 2025 override.
6. Audit contract transitions and price gaps at every selected roll date.
7. Retain excluded rows for traceability, attach `session_usable`,
   `session_quality_status`, and `exclusion_reasons`, and write a Zstandard
   compressed Parquet artifact.

## Limitations and robustness boundaries

- NQ confirmation data were not acquired in Phase 2. MNQ/NQ differences
  therefore remain deferred until MNQ produces a strong frozen candidate.
- The two empty Good Friday sessions are treated as provider gaps relative to
  the product calendar and excluded. The audit does not infer synthetic bars.
- All roll-date sessions are excluded. If a later finalist depends materially
  on roll weeks, session-aligned front and next contract requests should be
  acquired and validated separately.
- The final-test interval was structurally audited because quality flags must
  exist for every session. It remains locked against signal discovery,
  candidate selection, or performance inspection.
- A usable session can follow an excluded session. Phase 4 features that depend
  on the prior day or overnight history must invalidate themselves when their
  required predecessor session is excluded.

## Phase 4 requirements

Phase 4 may begin with the following mandatory rules:

1. Filter research inputs to `session_usable = true`.
2. Keep the finalized chronological split labels unchanged.
3. Make every feature's availability timestamp explicit.
4. Invalidate prior-session and overnight features when their prerequisite
   session is excluded.
5. Preserve the locked final-test flag and prohibit any feature-analysis or
   candidate-selection output from consulting that split.

## Open questions

- Should the 20 roll sessions be reacquired with session-aligned requests now,
  or only if a later finalist trades near roll dates? The conservative current
  answer is to defer and retain the exclusions.
- If a candidate relies on overnight behavior, should the 30 non-roll
  missing-data sessions be re-requested from the provider before finalist
  validation? That decision can be made without changing the frozen split
  boundaries.

## Reproducible artifacts

- `data/normalized/mnq_1m.parquet`
- `data/manifests/normalization_manifest.json`
- `data/manifests/data_quality_summary.json`
- `data/manifests/request_quality.jsonl`
- `data/manifests/session_quality.csv`
- `data/manifests/exclusions.jsonl`
- `data/manifests/missing_minute_runs.jsonl`
- `data/manifests/roll_boundary_audit.csv`
- `data/manifests/data_quality_issues.jsonl`

Run the audit with:

```powershell
python -m src.data_audit run
```
