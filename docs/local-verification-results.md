# Local Verification Results

This document summarizes the recorded verification campaigns for ModelMesh. It describes tests and observations, not a production-readiness guarantee. The run reports and raw files are the evidence of record; this summary is not a substitute for them.

The campaigns were run on November 22–23, 2026, from commit `d6fd970b76ed5e0a747be709e0f62e7a009d31da`. The worktree was dirty during the runs. See the [verification rules](../verification/RULES.md), [environment record](../verification/00_environment.md), and [consolidated evidence review](../verification/EVIDENCE.md) for scope, provenance, and caveats.

> **Evidence availability:** The `verification/` directory is ignored by the current Git configuration. Links to the detailed reports and raw data below resolve in the working copy only. Preserve or distribute that evidence archive alongside this document when reproducibility is required.

## Environment and test baseline

The live checks used Windows 11 with Docker Desktop and a local Docker Compose stack. The host had an Intel Core i5-1340P (12 physical / 16 logical cores) and 16,025 MB RAM. Docker Engine was 29.9.2, Docker Compose 5.6.1, Python 3.12.10, PostgreSQL 16.16, and Redis 7.4.12. Compose containers had no CPU or memory limits. The Docker daemon was initially unavailable during environment capture; Docker Desktop was started, and the engine became reachable. WSL2 Docker integration was unavailable at initial capture.

The baseline test suite was run three consecutive times against a scratch PostgreSQL database and Redis DB 1:

| Runs | Result per run | Warnings per run | Pytest duration: median / p95 / spread | Wall duration: median / p95 / spread |
| ---- | -------------- | ---------------: | -------------------------------------: | -----------------------------------: |
| 3    | 102 passed     |                8 |                   5.36 / 5.92 / 1.55 s |              9.116 / 9.781 / 2.650 s |

`pytest --collect-only -q` collected 102 tests in 11 files. Three later runs with complete start/end metadata also each passed all 102 tests with 8 warnings; pytest duration median was 5.65 s, p95 6.01 s, spread 0.56 s. Some earlier test-run wrappers did not record end timestamps. These are local test results, not service-availability measurements. Details and per-file test counts are in the [baseline report](../verification/runs/01_baseline_tests/README.md).

## Source audit

The source and Git-history audit found:

- Registry lookup queries Postgres on the REST inference and WebSocket request paths; the audit found no in-memory registry cache in the gateway.
- REST inference uses canary selection when no version is pinned. The WebSocket path resolves the requested version directly and does not use canary selection.
- REST queue submission stores the worker name and does not use registry `protocol` or `endpoint`; direct HTTP/gRPC routing by those fields occurs in the WebSocket path.
- Canary selection chooses the highest numeric version as canary and the lowest as stable.
- `inference.proto` declares unary `Predict` and bidirectional streaming `PredictStream`. The gateway receives the WebSocket file as one binary message and sends one final gRPC chunk; this path does not demonstrate incremental upload.
- Both Redis consumers use consumer groups, retry waits of 2, 4, and 8 seconds, a dead-letter stream, and `XAUTOCLAIM` for stale-message reclaim.
- API keys are hashed with SHA-256, passwords use bcrypt, and the rate-limit path uses a transactional Redis pipeline.
- Kafka modules are present but were not found imported or deployed in the application entrypoints. Git history contained implementation commits for retry/reclaim, but no evidence of a measured message-delivery failure rate or before/after measurement.

The [static-audit report](../verification/runs/02_static_audit/README.md) contains source locations, quotes, and the exact Git-message search results.

## WebSocket HTTP-versus-gRPC comparison

The end-to-end comparison used a full Compose stack and a separate scratch gateway. Five separate processes each performed 20 warm-ups and 100 measured requests per arm and scenario, sequentially, with one WebSocket connection per request. Each arm's latency statistics include successful measured requests only. Confidence intervals bootstrap independent run clusters; the reported Mann–Whitney p-values are request-level and should be interpreted cautiously because requests within a run are not independent.

| Payload / model arm               |               HTTP p50 / p95 / spread (n) |              gRPC p50 / p95 / spread (n) |       gRPC − HTTP p50 |        95% run-cluster bootstrap CI (ms / %) |                      Paired runs favoring gRPC |
| --------------------------------- | ----------------------------------------: | ---------------------------------------: | --------------------: | -------------------------------------------: | ---------------------------------------------: |
| Existing image, OCR unary         |         356.45 / 459.37 / 406.13 ms (500) |      350.76 / 470.18 / 3,362.88 ms (500) |     −5.69 ms (−1.60%) |       [−32.37, +30.59] ms / [−8.30%, +9.60%] |                                            3/5 |
| Page-like image, OCR unary        | 27,683.42 / 30,177.53 / 28,518.32 ms (86) | 26,200.42 / 27,907.99 / 4,496.96 ms (15) | −1,483.00 ms (−5.36%) | [−1,483.00, −1,138.05] ms / [−5.36%, −4.31%] | 2/5 paired; only 2/5 runs had paired successes |
| Existing audio, ASR gRPC stream   |         712.01 / 951.54 / 475.78 ms (500) |        698.39 / 940.13 / 769.97 ms (500) |    −13.62 ms (−1.91%) |        [−40.29, +2.60] ms / [−4.96%, +0.36%] |                                            4/5 |
| Existing audio, ASR gRPC unary    |         704.63 / 967.68 / 595.95 ms (500) |        691.97 / 962.52 / 914.64 ms (500) |    −12.66 ms (−1.80%) |       [−25.04, +10.47] ms / [−3.49%, +1.55%] |                                            3/5 |
| Generated speech, ASR gRPC stream |   1,756.68 / 2,437.42 / 2,836.74 ms (500) |  1,743.96 / 2,404.32 / 3,112.30 ms (500) |    −12.72 ms (−0.72%) |      [−24.17, +143.35] ms / [−1.40%, +7.56%] |                                            3/5 |
| Generated speech, ASR gRPC unary  |   2,086.45 / 3,045.83 / 5,158.57 ms (500) |  2,054.86 / 3,113.64 / 3,966.73 ms (500) |    −31.59 ms (−1.51%) |       [−95.75, +28.88] ms / [−4.43%, +1.33%] |                                            3/5 |

The existing OCR and ASR fixture comparisons did not establish a measurable p50 difference: their run-cluster confidence intervals include zero. The page-like OCR comparison is not a valid five-run performance comparison: only 134/500 HTTP and 15/500 gRPC measured requests succeeded, and only two run clusters had paired successes. One ASR gRPC-stream capture contained one final response, and the entire audio file was sent in one WebSocket binary message. The optional no-op transport-overhead measurement was **NOT RUN**. See the [full benchmark report](../verification/runs/03_grpc_vs_http/README.md) and its [analysis](../verification/runs/03_grpc_vs_http/analysis.md).

## Queue recovery after worker termination

The corrected small-fixture campaign used the 2,113-byte repository OCR image and a separate Compose worker. It completed seven SIGKILL trials: five with `RECLAIM_IDLE_MS=15000`, one with the default `300000`, and one with two successive kills. A separate trial submitted jobs while the worker was down.

| Scenario                                            | Jobs accepted | Success | Failed / lost / non-terminal | Distinct job IDs logged processing more than once |
| --------------------------------------------------- | ------------: | ------: | ---------------------------: | ------------------------------------------------: |
| SIGKILL trials, `RECLAIM_IDLE_MS=15000` (5 trials)  |           300 |     300 |                    0 / 0 / 0 |                                                 5 |
| SIGKILL, default `RECLAIM_IDLE_MS=300000` (1 trial) |            60 |      60 |                    0 / 0 / 0 |                                                 1 |
| Two successive SIGKILLs (1 trial)                   |            60 |      60 |                    0 / 0 / 0 |                                                 1 |
| Total SIGKILL trials (7 trials, 8 kill events)      |           420 |     420 |                    0 / 0 / 0 |                                                 7 |
| Jobs submitted while worker was down (1 trial)      |            60 |      60 |                    0 / 0 / 0 |                                                 0 |

For the five 15-second reclaim trials, kill-to-last-completion was n=5, median 45.33 s, p95 54.05 s, spread 11.95 s. The default-config trial took 317.88 s and the double-kill trial took 39.30 s from the final kill; both are n=1 observations. Across the eight corrected trials, successful worker processing durations had n=480, median 370.0 ms, p95 690.6 ms, and spread 1,407 ms.

Seven IDs appearing in processing logs more than once is evidence of repeated processing attempts, not proof that duplicate results reached callers. The trials do not establish exactly-once processing or a general loss-free guarantee. An earlier series used a 135,371-byte image and is retained separately, but excluded from these primary results. See the [corrected queue report](../verification/runs/13_queue_worker_kill_small/README.md).

## Retry, dead-letter, reclaim, and invalid-input checks

The harness loaded both current `consumer.py` files with stubbed inference and metrics, using real Redis DB 1 without loading model weights:

- Permanent `ValueError`: one attempt, no delay, failed status retained the specific invalid-input error, one DLQ entry, and zero pending entries.
- Always-transient error: four attempts with waits of 2/4/8 seconds, then failed to the DLQ with zero pending entries. Real wall-clock duration was 14.332 s for OCR and 14.254 s for ASR (one observation each).
- Fails twice, then succeeds: three attempts, 2/4-second waits, success, no DLQ entry, zero pending entries.
- Seeded scenarios: for each worker, 1,500 jobs across three seeds produced 1,377 successes, 123 failures/DLQ entries, 2,102 attempts, and zero jobs left non-terminal or pending. These are synthetic outcomes, not a production failure-rate estimate.
- Reclaim: 20 ghost-consumer entries per worker were reclaimed and succeeded; pending count went from 20 to zero and DLQ remained empty.

On the live isolated stack, 10 invalid image uploads and 10 invalid audio uploads all failed with specific errors. OCR submit-to-first-observed-terminal time was n=10, median 61.951 ms, p95 71.079 ms, spread 40.953 ms. ASR was n=10, median 168.972 ms, p95 560.426 ms, spread 644.956 ms. Polling was immediate first, then at 100 ms intervals; these are end-to-end observed job times, not pure worker execution times.

A one-sample-per-model pre-split consumer-source overlay produced generic `Processing failed` errors after four logged attempts: OCR 14,117.178 ms and ASR 15,215.839 ms (n=1 each; p95 equals the observation; spread zero). This was not a full historical stack or paired before/after test. The first harness attempt was invalid because its inference stub was not bound into the imported consumers; it is preserved and excluded. Two live setup attempts also failed before submitting jobs (invalid email domain; API-key rate limit above the 1,000/minute schema maximum). See the [retry and invalid-upload report](../verification/runs/15_retry_backoff_dlq_corrected/README.md).

## Enqueue latency, canary selection, and live updates

These are local single-machine measurements. The request generator and Docker Desktop shared the Windows host. The benchmark used a 102,400-byte payload, 20 round-robin API keys at the accepted maximum of 1,000 requests/minute, and a dummy worker that consumed no jobs. Each target-rate and path combination ran three separate 60-second runs. All 18 runs returned HTTP 200 for every request; no 401, 422, 429, or 500 responses occurred.

| Path         | Target req/s | Pooled n / runs | p50 ms | p95 ms | p99 ms |   max ms | spread ms |
| ------------ | -----------: | --------------: | -----: | -----: | -----: | -------: | --------: |
| Enqueue      |           10 |       1,800 / 3 |  17.81 |  80.87 | 169.06 | 1,107.40 |  1,102.94 |
| Enqueue      |           50 |       9,000 / 3 |  15.35 |  44.90 | 268.90 |   786.87 |    783.06 |
| Enqueue      |          100 |      18,000 / 3 |  22.09 |  76.39 | 218.53 |   751.42 |    746.96 |
| Registry GET |           10 |       1,800 / 3 |   6.79 |  30.51 |  65.17 |   138.79 |    136.59 |
| Registry GET |           50 |       9,000 / 3 |   6.94 |  12.11 |  26.10 |    87.68 |     85.91 |
| Registry GET |          100 |      18,000 / 3 |   8.29 |  23.44 |  72.78 |   321.76 |    319.87 |

Peak sampled Redis memory for enqueue was 71,271,872 bytes at 10 req/s, 348,838,048 bytes at 50 req/s, and 689,315,352 bytes at 100 req/s; all stayed below the 900,000,000-byte safety threshold. Redis DB 1 returned to zero keys after each run. After external cleanup, process-wide Redis memory was 41,840 bytes above preflight, so it was close but not byte-identical.

Canary allocation was sampled once per configured share with 2,000 unpinned requests. The intervals are 95% Wilson intervals; p-values are Pearson chi-square tests against the configured share.

| Versions / setting       |                       Observed canary share | 95% Wilson interval |     Chi-square p |
| ------------------------ | ------------------------------------------: | ------------------: | ---------------: |
| v1/v2, 10%               |                          219/2,000 (10.95%) |        9.66%–12.39% |           0.1567 |
| v1/v2, 30%               |                          590/2,000 (29.50%) |       27.54%–31.54% |           0.6256 |
| v1/v2, 50%               |                          998/2,000 (49.90%) |       47.71%–52.09% |           0.9287 |
| v1/v2/v9/v10, 50% on v10 | v10: 1,019 (50.95%); v1: 981; v2/v9: 0 each |  v10: 48.76%–53.14% | 0.3955 for 50/50 |

All 200 explicit pin requests matched the requested version (50 each for v1, v2, v9, and v10); all returned HTTP 200. Zero observed requests to v2 and v9 in the four-version sample do not prove those versions can never be selected.

The live-update loop made 108 HTTP requests at 20.09 req/s: 107 returned 200 and one expected request returned 503 while both test versions were disabled. Each transition below is one observation (n=1; p50 and p95 equal the value; spread zero):

| Change                             | Delay to first observed behavior |
| ---------------------------------- | -------------------------------: |
| Canary 0 → 100                     |                         15.82 ms |
| Disable v2                         |                         42.17 ms |
| Disable v1 (both disabled)         |                         52.44 ms |
| Re-enable v1                       |                         40.73 ms |
| Re-enable v2                       |                         11.68 ms |
| WebSocket protocol/endpoint update |                         48.50 ms |

Both gateway containers retained their container IDs during the live updates; the original registry snapshot was unchanged after cleanup. The WebSocket probe latency itself was n=40, median 35.16 ms, p95 44.90 ms, p99 50.01 ms, max 51.61 ms, spread 30.60 ms. These are observed local timings, not a propagation guarantee. Full request distributions, sampled CPU, and raw evidence are in the [registry/canary report](../verification/runs/19_registry_latency_canary/README.md).

## Failures, skips, and limits

- The historical gRPC and enqueue figures have no saved raw request evidence in the reviewed verification archive; they are not recreated or treated as verified here.
- The page-like OCR comparison had severe failures and only two paired run clusters. Five failed requests recorded elapsed values beyond the 90-second receive timeout; the maximum was 17,573,222 ms. A host suspension or clock discontinuity was suspected, not established.
- Three run-03 analysis attempts failed (IndentationError, KeyError, TypeError). Earlier generated analysis outputs were overwritten; request-level raw CSVs remain.
- The first queue series used the wrong, larger image; it is retained but excluded from the corrected results. The corrected series still recorded seven IDs processed more than once.
- Retry run 14 is invalid and excluded. The initial invalid-email and excessive-rate-limit live attempts are retained; neither submitted inference jobs.
- Registry pilots 16 and 17 failed during harness execution, and pilot 18 was reduced. They are preserved but excluded from the full run 19 results. Schedule-lag summaries in run 19 included negative medians and should not be used as evidence.
- The optional no-op HTTP/gRPC transport benchmark was **NOT RUN**. Kubernetes was **NOT VERIFIED** in the environment pass. Other host workloads were not continuously profiled (**NOT VERIFIED**).
- The verification worktree was dirty, and the `verification/` archive is ignored by Git. This document and all measurements describe this local environment; they do not establish production capacity, availability, or correctness under untested workloads.

Detailed commands, run metadata, cleanup checks, per-run records, and preserved failures are available in the linked reports and their `raw/` subdirectories.
