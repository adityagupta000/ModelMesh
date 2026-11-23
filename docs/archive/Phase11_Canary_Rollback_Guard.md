# Phase 11: Canary Rollback Guard (optional, gated)

> **Historical implementation prompt.** Phase 11 has been completed and merged into `dev`. This file describes the original gated task, not a current completion checklist. See the [Phase 11 completion report](Phase11_Completion_Report.md) and the current [SETUP instructions](../SETUP.md#canary-rollback-guard).

> Paste this whole file into your coding assistant, inside the ModelMesh repo.
> Global rules and the report format are in `00_Run_The_Project_Prompt.md`, Section A. They apply here.

**This is the only phase that adds a capability.** It is optional (decision D5). If you skip it, Phases 6 to 10 are still a complete plan.

**Goal:** if a canary version is clearly worse than the stable one, set its `canary_percent` to 0 automatically.
**Why this feature:** it uses only data and endpoints that already exist (ClickHouse metrics, `GET /v1/models`, `PATCH /v1/models/{id}`), and your Phase 5 doc already lists it under "what would change with more time".
**Effort:** 1.5 to 2 days.

## Gate: check before writing any code

Stop and tell the human if any of these is false. Do not start the phase without all of them.

- [ ] Phases 6 to 10 reports exist, all exit criteria met, tag `phase-10-complete`, `pytest -q` green.
- [ ] The human has confirmed they want Phase 11 (decision D5).
- [ ] `request_metrics` has rows for **both** workers (Phase 10 verification).
- [ ] `WORKER_NAME` comes from the environment (Phase 9, Task 9.5), so a v2 worker can run from the same image.
- [ ] You have an **admin API key** for the gateway (Phase 0, step 0.6).

**Scope fence:** one new script, its tests, docs, and an optional CronJob. **Explicitly out of scope:** latency-based rules, auto-promote, statistical significance tests, notifications, a UI, multi-model orchestration, changes to gateway request handling, any change to the registry schema or API. Decision D6 stands: the rollback action is `canary_percent = 0` **only**. Never disable or delete v2.

### Intended behaviour changes (everything else must stay identical)

- New: the guard script. It changes nothing unless run with `--apply`, and then only sets a canary's `canary_percent` to 0.
- The gateway request path, the workers and the registry API are **not** touched, so the smoke `CHECK` lines must be identical to the baseline.

**Branch:** `phase-11-canary-guard`

---

## Design (follow it; do not redesign)

A standalone script `gateway/canary_guard.py`. It lives in `gateway/` so it ships inside the existing gateway image, which already has `httpx` and `clickhouse-connect` in its requirements. It is **not** imported by the app.

### Inputs

| Name                   | Source                                   | Default                             |
| ---------------------- | ---------------------------------------- | ----------------------------------- |
| Gateway URL            | env `GATEWAY_URL`                        | `http://localhost:8000`             |
| Admin API key          | env `ADMIN_KEY`                          | none (required only with `--apply`) |
| ClickHouse host / port | env `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT` | `clickhouse` / `8123`               |
| Window                 | `--window-min`                           | 10                                  |
| Minimum requests       | `--min-requests`                         | 30                                  |
| Error-rate margin      | `--error-delta`                          | 0.10                                |
| Apply changes          | `--apply`                                | off (dry-run)                       |

Never print the admin key. Never pass it on the command line.

### Steps per run

1. `GET {GATEWAY_URL}/v1/models` (public endpoint). Group rows by model `name`. For each name that has **two or more active versions** and at least one with `canary_percent > 0`: the **canary** is the highest numeric version (same rule as the gateway: the first number found in the version string), the **stable** is the lowest. Versions in between are ignored. Use the canary row's `canary_percent`.
2. Query ClickHouse once per model:

```sql
SELECT model_version, count() AS n, countIf(status = 'error') / count() AS err
FROM request_metrics
WHERE model_name = {name:String} AND timestamp > now() - INTERVAL {window:UInt32} MINUTE
GROUP BY model_version
```

with `parameters={"name": ..., "window": ...}`. A version with no rows has `Stats(n=0, err=0.0)`. 3. Decide with a **pure function** (no I/O):

```python
from dataclasses import dataclass

@dataclass
class Stats:
    n: int
    err: float

@dataclass
class Config:
    min_requests: int = 30
    error_delta: float = 0.10

def decide(stable: Stats, canary: Stats, canary_percent: int, cfg: Config) -> tuple[str, str]:
    if canary_percent == 0:
        return "noop", "canary already at 0"
    if stable.n < cfg.min_requests:
        return "hold", "not enough stable traffic"
    expected = stable.n * canary_percent / max(1, 100 - canary_percent)
    if expected >= cfg.min_requests and canary.n < 0.2 * expected:
        return "rollback", f"canary starved: {canary.n} completed vs about {expected:.0f} expected"
    if canary.n < cfg.min_requests:
        return "hold", "not enough canary traffic"
    if canary.err - stable.err > cfg.error_delta:
        return "rollback", f"canary error rate {canary.err:.2f} vs stable {stable.err:.2f}"
    return "hold", "healthy"
```

4. On `rollback` **and** `--apply`: `PATCH {GATEWAY_URL}/v1/models/{canary_id}` with `{"canary_percent": 0}` and header `Authorization: Bearer $ADMIN_KEY`. On any other decision, or without `--apply`, change nothing.
5. Print **one JSON line per model per run** and nothing else on stdout:

```json
{
  "ts": "...",
  "model": "asr",
  "stable": "v1",
  "canary": "v2",
  "canary_percent": 20,
  "stable_stats": { "n": 140, "err": 0.01 },
  "canary_stats": { "n": 2, "err": 0.0 },
  "decision": "rollback",
  "reason": "canary starved: ...",
  "applied": true
}
```

Exit code 0 normally. Exit non-zero only when the guard itself fails (gateway or ClickHouse unreachable, PATCH rejected). A failure must never leave a half-applied change.

### Why these two rules

- **Starvation rule.** With the async queue, a canary whose worker is down or crashed does **not** produce errors. Its jobs sit in the stream unprocessed, so an error-rate-only guard would never fire. The starvation rule compares the canary's _completed_ count with what the configured percentage predicts from stable traffic (`stable * p / (100 - p)`).
- **Delta, not an absolute threshold.** If both versions are failing (for example Redis trouble), rolling back the canary would not help, so the guard compares against stable.

### Known limitations (put these in the docs)

- A rolled-back canary's queued jobs stay unprocessed until their payload and status keys expire; clients polling those job ids eventually see a 404.
- The window is a sliding look-back, not an exact count since the canary started.
- If stable traffic is thin the guard holds; it never rolls back on thin data.

---

## Task 11.1: Write `gateway/canary_guard.py`

Requirements:

- `decide()`, `Stats` and `Config` exactly as above, importable without any third-party module.
- Import `clickhouse_connect` **inside** the function that queries ClickHouse, so the unit tests do not need it.
- A small `main()` with `argparse` for the flags above. Dry-run is the default.
- Use `httpx` for the two HTTP calls with a 10-second timeout.
- No retries, no loops, no sleeping: one run, then exit. Repetition is the CronJob's job.
- No new third-party dependency.

Commit: `phase11: canary guard script`.

## Task 11.2: Unit tests for `decide()`

Create `tests/test_canary_guard.py`. The gateway directory is already on `sys.path` in `conftest.py`, so `import canary_guard` works. These tests need no database.

Required cases (use defaults `min_requests=30`, `error_delta=0.10` unless stated):

| Case                         | stable          | canary         | percent                      | Expected decision                                   |
| ---------------------------- | --------------- | -------------- | ---------------------------- | --------------------------------------------------- |
| Already rolled back          | any             | any            | 0                            | `noop`                                              |
| Thin stable traffic          | n=10            | n=10           | 20                           | `hold`                                              |
| Healthy                      | n=200, err 0.01 | n=50, err 0.02 | 20                           | `hold`                                              |
| Error spike                  | n=200, err 0.01 | n=50, err 0.30 | 20                           | `rollback`                                          |
| Starved canary (worker down) | n=200, err 0.01 | n=0            | 20                           | `rollback`                                          |
| Not enough canary data       | n=200, err 0.01 | n=10, err 0.50 | 5 (expected=10.5, below min) | `hold`                                              |
| Both versions failing        | n=200, err 0.40 | n=50, err 0.45 | 20                           | `hold`                                              |
| Boundary on the margin       | n=200, err 0.10 | n=50, err 0.20 | 20                           | `hold` (difference equals the margin, not above it) |

Also test the model-selection helper (canary = highest numeric version, stable = lowest, middle ignored; `v9` vs `v10` picks `v10` as canary).

Commit: `phase11: unit tests for the guard decision`.

## Task 11.3: Manual run against a live setup

1. Dry-run first, from the Ubuntu WSL terminal with `modelmesh-env` active. The script needs `httpx` and `clickhouse-connect` (`pip install httpx clickhouse-connect` if missing, after a `--dry-run` check as in Phase 7). Use the Phase 0 key, which was promoted to admin, and do not echo it:

```bash
source modelmesh-env/bin/activate
export ADMIN_KEY="$API_KEY"          # the Phase 0 key, already promoted to admin
cd gateway
GATEWAY_URL=http://localhost:8000 CLICKHOUSE_HOST=localhost python canary_guard.py
cd .. && deactivate
```

For a Compose setup, use the temporary ClickHouse from Phase 10 Task 10.1 (it already publishes port 8123 to `localhost`). With no canary configured, the guard must print nothing and exit 0.

2. Confirm dry-run never changes anything: re-read `GET /v1/models` afterwards.

## Task 11.4: Demo plan (this is the honest version)

A genuinely failing worker would need test-only code inside the product, which is out of scope. So:

**Demo A: starvation (real).** This needs no new worker at all.

```bash
# register a v2 whose worker does not exist, with a 20% canary
curl -s -X POST $BASE/v1/models -H "Authorization: Bearer $ADMIN_KEY" -H 'Content-Type: application/json' \
  -d '{"name":"asr","version":"v2","worker_name":"asr-worker-v2","protocol":"http",
       "endpoint":"http://asr-worker:8002/infer","canary_percent":20}'
```

Send traffic to `/v1/infer/asr`. About 20% of jobs are routed to v2 and never complete. To keep the demo short, run the guard with a lower threshold: `--min-requests 10` needs stable completions of at least `10 * 80 / 20 = 40` inside the window. Run the guard: expect `rollback` with a "canary starved" reason. Re-run with `--apply`: `canary_percent` becomes 0. Run once more: `noop`.

**Demo B: error spike (synthetic).** Insert synthetic `error` rows for a version into ClickHouse (`INSERT INTO request_metrics ...`) so that the canary shows a high error rate against a healthy stable one, run the guard once, and confirm `rollback`. State clearly in the report that these rows were synthetic.

Clean up afterwards: set `canary_percent` to 0, disable the `asr` v2 row through the existing `DELETE /v1/models/{id}` endpoint (this is demo cleanup by a human operator, not part of the guard), and delete the synthetic rows.

## Task 11.5 (optional): Kubernetes CronJob

Only if the human wants it. Add `k8s/gateway/canary-guard-cronjob.yaml`:

- `schedule: "* * * * *"`, `concurrencyPolicy: Forbid`, `successfulJobsHistoryLimit: 1`, `failedJobsHistoryLimit: 3`.
- The gateway image (`imagePullPolicy` as the other manifests use), `command: ["python", "canary_guard.py", "--apply"]`.
- Environment: `GATEWAY_URL=http://gateway:8000`, `CLICKHOUSE_HOST=clickhouse`, and `ADMIN_KEY` from a **new Secret** `canary-guard-admin-key`. Create the Secret with `kubectl create secret generic ... --from-literal=...` at deploy time; **do not commit the key**, and do not add a Secret manifest containing a real key.
- Apply to a cluster only if one is available; otherwise validate with `kubectl apply --dry-run=client -f` and mark the live run `NOT RUN`.

## Task 11.6: Docs

- Phase 5 doc: a new "Canary rollback guard" section covering the design, the two rules, the defaults, the limitations above, and the demo results.
- `SETUP.md`: how to run it (dry-run, `--apply`) and how to create an admin key.
- README status table: one honest line.

Commit: `phase11: docs`.

---

## Verification

```bash
pytest -q tests/test_canary_guard.py       # all eight decide() cases plus model selection
cd gateway && python canary_guard.py       # dry-run prints decisions, changes nothing
```

Plus the outputs from Demo A (three runs: rollback in dry-run, rollback applied, noop) and Demo B.

## Exit criteria

- [ ] **Regression gate:** `scripts/smoke_test.sh` output matches `docs/baseline-smoke.txt` on every `CHECK` line except those listed under "Intended behaviour changes"; latency p95 is no more than 25% worse than the baseline for either model (re-run once before calling noise).
- [ ] The gate checklist at the top was confirmed, with evidence for each line.
- [ ] `decide()` tests pass for all eight cases; model-selection test passes.
- [ ] Dry-run changes nothing (before and after `GET /v1/models` compared).
- [ ] Demo A: rollback decision, then applied (`canary_percent` is 0), then `noop`. Output attached.
- [ ] Demo B: synthetic error spike produces `rollback`; the report says the rows were synthetic.
- [ ] Demo cleanup done (canary at 0, demo v2 disabled, synthetic rows deleted).
- [ ] No admin key appears in any committed file, log excerpt or report (check with `git grep` for the first 6 characters).
- [ ] The guard changes nothing but `canary_percent`, and only to 0.
- [ ] Docs updated as in Task 11.6.

Then print the **Phase 11 report** and stop.
