# Phase 7 completion report

> **Historical phase result.** The 20-pass/1-xfail count below was the Phase 7 result before Phase 8. The latest recorded review-follow-up full-suite result is 102 passed, 8 warnings; see the [Phase 11 completion report](Phase11_Completion_Report.md).

## Scope

Phase 7 test-harness, worker-test isolation, Compose test ports, dependency, mutation, and documentation work is complete.

## Verification

- The normal test environment passed twice: `20 passed, 1 xfailed`.
- A throwaway environment installed only `tests/requirements.txt` and passed: `20 passed, 1 xfailed`.
- The xfail is `test_disabled_model_returns_503`, explicitly named as fixed in Phase 8.
- The suite was rerun after the independent smoke-test additions; the test count remains `20 passed, 1 xfailed`.
- Admin authorization mutation: a temporary removal of `record.is_admin` made the admin-only test fail with HTTP 200 instead of 403, then passed after restoration.
- Rate-limit mutation: a temporary removal of the 429 branch made the rate-limit test fail with HTTP 200 on the third request, then passed after restoration.
- Obsolete `_key_dep`, `_admin_dep`, and `make_verify` names are absent.
- The test harness uses the application's database and Redis singletons, Redis DB 1, unique emails, and shared-state cleanup.
- Worker tests stub EasyOCR/Whisper, keep real NumPy and OpenCV, and load modules by file path.
- At Phase 7, test dependencies used NumPy 2.4.4 and OpenCV headless 5.0.0.93. They were later aligned with the worker images at NumPy 1.26.4 and OpenCV headless 4.10.0.84 (see the [Phase 9 completion report](Phase9_Completion_Report.md)).
- `docker-compose.test.yml`, `SETUP.md`, and `RUNNING.md` document the scratch database and test-only host ports.

## Smoke regression gate

- `scripts/smoke_test.sh` was run twice and both runs returned `SMOKE: PASS`.
- Both runs had identical `CHECK` statuses.
- `docs/baseline-smoke.txt` was generated with `--latency 20`.
