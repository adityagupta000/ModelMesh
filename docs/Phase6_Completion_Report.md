# Phase 6 completion report

## Scope

Phase 6 schema, documentation, cleanup, and baseline-verification work is complete.

## Independent verification

- Migration `migrations/004_canary_percent_and_constraints.sql` was applied twice to the normal database without error.
- The normal `models` table contains `canary_percent` and the `models_protocol_chk`, `models_status_chk`, and `models_canary_chk` constraints.
- A throwaway Compose project named `modelmesh_phase6_audit` was started with brand-new volumes.
- The throwaway registry returned HTTP 200 and listed `doc-ocr` and `asr`.
- Its fresh `models` table contained `canary_percent` and all three CHECK constraints.
- The throwaway project was removed with `down -v`, and only its throwaway volumes were deleted.
- The normal stack was restarted; the baseline login succeeded and the normal database retained the migration.
- The generated PostgreSQL ConfigMap exactly matches `db/init.sql`.
- `scripts/smoke_test.sh` and `docs/baseline-smoke.txt` now exist and the repeatability gate is evidence-backed.
- `gateway/queue.py` is absent and no source references remain.
- `gateway/queue.py`, the dead gateway symbols, Kafka dormancy comments, export-script rename, Docker ignore rules, and git ignores were all completed.
- ClickHouse DDL exists and is marked reconstructed because no live ClickHouse instance was available.

## Open decision

Decision D7 remains unchanged: no change was made to `docs/Prerequisites.md` or the public-repository interview-talking-point wording.
