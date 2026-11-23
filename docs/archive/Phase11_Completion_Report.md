# Phase 11 completion report

> **Historical merged result.** Phase 11 was merged into `dev` by commit `d6fd970`; the local feature branch was removed after the merge. The `phase-11-complete` tag remains. The full suite at that point reported 75 passed, 6 warnings. Subsequent review follow-up verification is recorded below.

## Branch / last commit / tag

- Original branch: `phase-11-canary-guard` (merged into `dev`; local branch removed)
- Last commit: the completion-report commit
- Tag: `phase-11-complete`
- Implementation commits: `972d7de`, `c075da6`, `b19fe28`, `b85545d`

## Tasks

- 11.1 — done: standalone dry-run-by-default canary guard.
- 11.2 — done: decision and numeric-version selection tests.
- 11.3 — done: no-canary dry run was silent and left the registry unchanged.
- 11.4 — done: starvation and synthetic-error demos; cleanup verified.
- 11.5 — skipped: optional Kubernetes CronJob was not requested; no cluster run.
- 11.6 — done: Phase 5, setup, and README documentation updated.

## Files changed

- `gateway/canary_guard.py`
- `tests/test_canary_guard.py`
- `README.md`
- `SETUP.md`
- `docs/Phase5-Observability-Canary.md`
- `docs/Phase11_Completion_Report.md`

## Commands run and key output

- `pytest -q tests/test_canary_guard.py`: `11 passed, 6 warnings`.
- `pytest -q`: `75 passed, 6 warnings`.
- Baseline no-canary run: `exit=0 output_bytes=0 registry_unchanged=True`.
- Phase 11 smoke gate, `scripts/smoke_test.sh --latency 5`: `SMOKE: PASS`. Health/readiness, models, auth, OCR, ASR, both WebSockets, rate limit, and admin patch checks passed. The bad-upload probe returned `Invalid image data` in 2 seconds, consistent with the Phase 9 intended behavior.
- Latency p95: doc-ocr `2.154s` versus baseline `16.665s`; ASR `2.118s` versus baseline `2.142s`. Both are within the 25% regression limit.
- ClickHouse verification showed ASR `85` rows (`85` successes, `0` errors) and doc-ocr `18` rows (`16` successes, `2` errors). Both workers use environment-configured `WORKER_NAME`.

### Demo A — real starvation

Submitted 90 ASR jobs at a 20% canary split: 73 assigned to stable `v1`, 17 to the nonexistent `asr-worker-v2`.

1. Dry run: stable `n=73, err=0.0`; canary `n=0, err=0.0`; decision `rollback`; reason `canary starved: 0 completed vs about 18 expected`; `applied=false`. A registry read confirmed the dry run left the canary at 20%.
2. `--apply`: same rollback decision; `applied=true`. A registry read confirmed `canary_percent=0`.
3. Repeat: decision `noop`; reason `canary already at 0`; `applied=false`.

### Demo B — synthetic error spike

Inserted 10 **synthetic** `error` rows for `asr:v2`. The guard observed stable `n=73, err=0.0` and canary `n=10, err=1.0`, then returned `rollback` with reason `canary error rate 1.00 vs stable 0.00`; `applied=false`.

Cleanup set v2's canary percentage to 0, disabled the demo v2 model, and deleted all 10 synthetic rows. Verified final state: v2 disabled, percentage 0, synthetic-row count 0. The temporary `ch-verify` container was removed.

## Exit criteria

- [x] Regression gate passed; p95 for both models stayed within 25% of baseline.
- [x] Phase 6–10 reports and `phase-10-complete` tag exist; full test suite is green.
- [x] Human authorized Phase 11.
- [x] ClickHouse has metric rows from both workers.
- [x] Redis worker names are environment-configurable.
- [x] An admin key was used successfully for registry operations; no key value is included here.
- [x] All required `decide()` cases and model-selection tests pass.
- [x] Dry-run behavior was checked without registry changes.
- [x] Demo A rollback, apply, and noop runs passed.
- [x] Demo B returned rollback using explicitly synthetic rows.
- [x] Demo v2 and synthetic rows were cleaned up.
- [x] Guard PATCHes only `{"canary_percent": 0}`; it does not disable or delete a model.
- [x] Documentation updated; repository-file scan found no local admin-key prefix.

## Deviations from the prompt

- To make Demo A's required post-rollback `noop` observable, model selection retains a pair with two active versions even when the highest version's percentage is 0. In that case the guard skips ClickHouse and reports `noop`. This extends the initial positive-canary filter without changing request routing or registry behavior.
- Demo B used synthetic ClickHouse rows, as specified by the plan; they were removed after verification.
- An accidentally exposed local admin credential was rotated; no credential value is recorded here.

## Open questions

- None. The optional Kubernetes CronJob was not requested.

## Review follow-up verification

The canary operating guidance now requires waiting one full guard window after enabling or increasing a canary before using `--apply`; the guard still has no canary-change timestamp and this warm-up rule is documentation, not a code-level timer. The WebSocket client now exits nonzero on server errors and timeouts and reports success only after a final result. Uvicorn access/error logs redact API-key query parameters. Additional focused coverage protects API-key rate-limit bounds, model/job privacy, and readiness error redaction.

The latest recorded full suite reports `102 passed, 8 warnings`. This was automated local verification, not a new live canary demo. Subsequent Minikube pod, OCR, and recovery checks are recorded in the [Phase 9 completion report](Phase9_Completion_Report.md).
