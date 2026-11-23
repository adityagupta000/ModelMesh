# Phase 10 completion report

> **Historical phase result.** The test counts and measurements below are from Phase 10. Phase 10 was subsequently merged into `dev`; the Phase 11 report records the latest full-suite result and current smoke evidence.

## Scope

Phase 10 metrics pipeline work was completed on branch `phase-10-metrics`.

## Release metadata

- Implementation commits: `07977ff`, `6c12f37`, `b704755`, `0d946d4`.
- Completion tag: `phase-10-complete` (retained after merge).
- Merge into `dev`: commit `3188a6d`; the local phase branch was removed after merging.

## Implemented

- Both worker metrics clients use configurable ClickHouse host and port.
- Worker metric inserts run off the event loop and back off for 60 seconds after a failure.
- Each job reaching a final outcome writes one row; retry attempts that continue do not.
- Removed the unused `gateway/metrics.py` module; retained `clickhouse-connect` in gateway requirements for the planned Phase 11 guard.
- Updated the Phase 5 observability claims and `SETUP.md` queries and ClickHouse configuration documentation.

## Verification

- Full test suite: `64 passed, 6 warnings` (re-run during Phase 10 recheck).
- `scripts/smoke_test.sh --latency 5`: `SMOKE: PASS`; all checks passed.
- Latency p95: doc-ocr `2.118s` against baseline `16.665s`; ASR `2.120s` against baseline `2.142s`. Both are within the 25% regression limit.
- With temporary ClickHouse running, 20 doc-ocr and 20 ASR jobs succeeded. ClickHouse queries verified 20 success rows per model.
- One invalid OCR job ended in failure and produced exactly one error row.
- With ClickHouse stopped, five jobs per worker succeeded. The measured median time ratios (down/up) were doc-ocr `0.897` and ASR `0.868`; one insert-failure warning was observed per worker.
- Worker metrics helper diff is empty. `gateway/metrics.py` is absent.
- Temporary `ch-verify` container was removed.
- Gateway `/ready` returned HTTP 200 after verification.

## Exit criteria

- [x] Regression smoke checks pass; latency p95 is within 25% of baseline.
- [x] 20 OCR and 20 ASR jobs produced metrics for both workers.
- [x] One failed job produced exactly one error row.
- [x] Jobs succeeded with ClickHouse stopped, without added median delay or per-job warning flooding.
- [x] Full pytest suite is green.
- [x] Unused gateway metrics module is removed.
- [x] Worker metrics helpers are identical.
- [x] Temporary ClickHouse container is removed.
- [x] Phase 5 documentation and `SETUP.md` are updated.

## Not run

- Kubernetes-side metrics query: `NOT RUN (no cluster was reachable; kubectl could not connect to localhost:8080)`.

## Deviations and open items

- The Phase 9 invalid-image timing gap was explicitly carried forward. The recheck now observed a failed OCR upload in 3 seconds with the specific `Invalid image data` error, rather than the old 115-second generic failure in the baseline.
- No Phase 10 exit criteria remain open. No secrets were included in this report.

## Review follow-up verification

The later review identified that plain Compose has no ClickHouse service. Worker metrics are now disabled when `CLICKHOUSE_HOST` is unset; when configured, connections and inserts use bounded timeouts, and failures retain the 60-second back-off. Tests cover both the disabled configuration and client timeout arguments. The current full suite reports `102 passed, 8 warnings`; live Compose OCR jobs also completed in 2 seconds and, after a 65-second gap, 3 seconds with ClickHouse unset.
