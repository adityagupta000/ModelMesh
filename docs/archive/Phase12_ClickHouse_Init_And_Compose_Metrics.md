# Phase 12: ClickHouse Init in Kubernetes and Metrics Pass-Through in Compose

> **Historical implementation prompt.** Phase 12 was completed. The exit checks below are retained as the original acceptance criteria; live Minikube evidence and the refreshed smoke baseline are recorded in this report.

> Paste this whole file into your coding assistant, inside the ModelMesh repo.
> **Precondition:** Phases 6 to 11 and the review follow-up are merged, `git status` is clean, and `pytest -q` is green (record the passed count; the last recorded run was 102 passed).
> Global rules, the shell rule (Ubuntu WSL terminal) and the report format are in `00_Run_The_Project_Prompt.md` (it lives in `docs/` in the repo), Section A. They apply here.

**Goal:** close two small gaps found in the Round 2 review.

1. **Kubernetes (review item N3):** the ClickHouse deployment has no volume and no init script, so after any pod restart the `request_metrics` table is gone, metrics silently stop, and someone must re-run the DDL by hand.
2. **Compose (review item N2):** `docker-compose.yml` does not pass `CLICKHOUSE_HOST` to the workers, and metrics are off when it is unset, so metrics cannot be switched on under Compose without editing the file.

**Effort:** about half a day. **Adds capability:** no. Both tasks make existing metrics easier to run correctly.

**Scope fence:** only `k8s/clickhouse/`, `docker-compose.yml`, and the docs listed in Task 12.3. Do **not** touch `metrics.py`, either worker, the canary guard, the gateway, the Postgres manifests, or `docker-compose.test.yml`. Do **not** add a persistent volume for ClickHouse: keeping metrics across restarts is a separate decision for the human. Default Compose behaviour must not change: with no variable set, metrics stay **off**.

**Branch:** `phase-12-clickhouse-init`

### Intended behaviour changes (everything else must stay identical)

1. **Kubernetes:** the ClickHouse pod creates the `request_metrics` table by itself at start-up (the table is still empty after a restart, because there is no volume).
2. **Compose:** both workers accept `CLICKHOUSE_HOST` and `CLICKHOUSE_PORT` from the shell. With nothing set, behaviour is exactly as today (metrics disabled, no connection attempts).

Run `scripts/smoke_test.sh` before and after. The `CHECK` lines must be identical. One exception: the `bad-upload` INFO line already differs from `docs/baseline-smoke.txt` because of Phases 9 and 10, so compare it with your "before" run from this phase instead.

---

## Task 12.0: Before-run

On a clean branch, with the normal Compose stack up (`docker compose up -d`, then `docker compose ps`):

```bash
scripts/smoke_test.sh > /tmp/smoke-before-phase12.txt
tail -3 /tmp/smoke-before-phase12.txt     # expect: SMOKE: PASS
```

If it does not pass, **stop and report**; do not start the phase on a failing baseline.

## Task 12.1: Create the table automatically when ClickHouse starts

The official ClickHouse image runs `*.sql` files found in `/docker-entrypoint-initdb.d/` when it starts. `clickhouse/init.sql` already uses `CREATE TABLE IF NOT EXISTS`, so running it on every start is safe.

1. Generate the ConfigMap from the SQL file, never by hand, so the two cannot drift (named `configmap.yaml` so a directory apply creates it before the Deployment):

```bash
kubectl create configmap clickhouse-init-sql --from-file=init.sql=clickhouse/init.sql \
  -n modelmesh --dry-run=client -o yaml > k8s/clickhouse/configmap.yaml
```

This needs `kubectl` but not a running cluster.

2. Edit `k8s/clickhouse/deployment.yaml`. Keep everything that is there (image `clickhouse/clickhouse-server:24.8-alpine`, `CLICKHOUSE_SKIP_USER_SETUP`, ports, resources). Add the mount to the container and the volume to the pod spec:

```yaml
          volumeMounts:
            - name: init-sql
              mountPath: /docker-entrypoint-initdb.d
              readOnly: true
      volumes:
        - name: init-sql
          configMap:
            name: clickhouse-init-sql
```

`volumeMounts` goes under the `clickhouse` container; `volumes` goes at the same level as `containers`.

3. **Do not** add a PersistentVolumeClaim, probes, or change resource limits in this phase.

Commit: `phase12: clickhouse creates request_metrics at startup`.

## Task 12.2: Let Compose switch worker metrics on from the shell

In `docker-compose.yml`, add these two lines to the `environment:` of **both** worker services (`doc-ocr-worker` and `asr-worker`), next to the other pass-through variables:

```yaml
CLICKHOUSE_HOST: ${CLICKHOUSE_HOST:-}
CLICKHOUSE_PORT: ${CLICKHOUSE_PORT:-8123}
```

An empty `CLICKHOUSE_HOST` is treated the same as unset by the workers (`if not os.getenv("CLICKHOUSE_HOST"): return`), so default behaviour does not change. Keep the file's existing indentation style (check whether `environment:` is a mapping or a list in this file, and match it).

Verify the default is unchanged:

```bash
docker compose up -d doc-ocr-worker asr-worker
docker compose exec doc-ocr-worker printenv CLICKHOUSE_HOST     # prints an empty line
docker compose exec asr-worker printenv CLICKHOUSE_PORT         # prints 8123
scripts/smoke_test.sh > /tmp/smoke-after-12-2.txt
diff <(grep '^CHECK' /tmp/smoke-before-phase12.txt | grep -v bad-upload) \
     <(grep '^CHECK' /tmp/smoke-after-12-2.txt | grep -v bad-upload) && echo "CHECK lines identical"
```

Commit: `phase12: compose passes clickhouse settings to workers`.

## Task 12.3: Docs

Update only what these two changes make wrong or incomplete:

| File                                   | Change                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SETUP.md`                             | Environment-variable table: `CLICKHOUSE_HOST` is "unset (disabled); Compose passes it through when set in the shell; Kubernetes sets `clickhouse`". Kubernetes section: the table is now created automatically when the ClickHouse pod starts; keep the manual `kubectl exec ... < clickhouse/init.sql` command as a fallback. Add the "ConfigMap is generated from `clickhouse/init.sql`" regeneration command. Add a short optional "Metrics under Compose" recipe (below). State that ClickHouse still has no persistent volume, so stored rows do not survive a pod restart. |
| `README.md`                            | Replace "The reconstructed `clickhouse/init.sql` schema must be applied separately" with the automatic behaviour; keep the "no persistent volume" caveat.                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `docs/Phase4-Kubernetes-Deployment.md` | The three `kubectl` lines that applied the DDL by hand become a fallback note.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `docs/Phase5-Observability-Canary.md`  | Same wording fix as README; Compose can enable metrics with a shell variable.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |

"Metrics under Compose" recipe for `SETUP.md` (optional use, run in the WSL terminal):

```bash
NET=$(docker inspect --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' $(docker compose ps -q gateway))
docker run -d --name ch-local --network "$NET" --network-alias clickhouse -p 8123:8123 \
  -e CLICKHOUSE_SKIP_USER_SETUP=1 clickhouse/clickhouse-server:24.8-alpine
sleep 10
docker exec -i ch-local clickhouse-client --multiquery < clickhouse/init.sql
CLICKHOUSE_HOST=clickhouse docker compose up -d doc-ocr-worker asr-worker

# turn it off again
docker compose up -d doc-ocr-worker asr-worker      # without the variable: metrics disabled
docker rm -f ch-local
```

Commit: `phase12: docs for clickhouse init and compose metrics`.

---

## Verification

**Compose (always):**

```bash
# 1. default: metrics off, nothing connects
docker compose up -d doc-ocr-worker asr-worker
docker compose logs --since 2m doc-ocr-worker asr-worker | grep -c "metric insert failed"   # expect 0

# 2. optional: metrics on, using the recipe from Task 12.3
#    run 3 OCR and 3 ASR jobs, then:
docker exec ch-local clickhouse-client -q \
  "SELECT model_name, count() FROM request_metrics GROUP BY model_name"        # rows for both models
#    then turn it off again as in the recipe and re-run scripts/smoke_test.sh
```

**Kubernetes (only if a cluster is available; otherwise write `NOT RUN (no cluster)` and do the static checks):**

Stop Compose first (`docker compose stop`) to free memory, and start it again afterwards (`docker compose start`).

```bash
kubectl apply -f k8s/clickhouse/
kubectl wait --for=condition=available deployment/clickhouse -n modelmesh --timeout=180s
kubectl exec -n modelmesh deploy/clickhouse -- clickhouse-client -q "SHOW CREATE TABLE request_metrics"   # table exists, no manual step run
kubectl rollout restart deployment/clickhouse -n modelmesh
kubectl rollout status deployment/clickhouse -n modelmesh
kubectl exec -n modelmesh deploy/clickhouse -- clickhouse-client -q "EXISTS TABLE request_metrics"        # prints 1 again after the restart
# send one OCR job through the gateway, then:
kubectl exec -n modelmesh deploy/clickhouse -- clickhouse-client -q "SELECT count() FROM request_metrics"  # at least 1
```

**If the table does not exist after the restart**, the image did not run the mounted script. **Stop and report** the container logs (`kubectl logs -n modelmesh deploy/clickhouse`); do not invent a different mechanism.

**Static checks (always):**

```bash
diff <(kubectl create configmap clickhouse-init-sql --from-file=init.sql=clickhouse/init.sql \
        -n modelmesh --dry-run=client -o yaml) k8s/clickhouse/configmap.yaml && echo "configmap matches init.sql"
kubectl apply --dry-run=client -f k8s/clickhouse/
docker compose config > /dev/null && echo "compose file valid"
git diff --stat            # only the files named in the scope fence
pytest -q                  # same passed count as before the phase
```

## Task 12.4 (optional, ask the human first): refresh the smoke baseline

`docs/baseline-smoke.txt` was recorded before Phases 6 to 11 and the review follow-up. If the human agrees:

1. Run `scripts/smoke_test.sh --latency 5` twice on the final code, **with no ClickHouse and metrics off** (the default). Both runs must end in `SMOKE: PASS` with the same `CHECK` lines.
2. Keep the old file as `docs/baseline-smoke-phase0.txt` (historical record) and save the new output as `docs/baseline-smoke.txt`.
3. Add one line to `RUNNING.md` or `SETUP.md` saying when and why the baseline was refreshed.

## Exit criteria

- [x] **Regression gate:** two final `scripts/smoke_test.sh --latency 5` runs passed and had identical `CHECK` lines. The second run recorded OCR p95 `2.523s` and ASR p95 `2.110s`.
- [x] `k8s/clickhouse/configmap.yaml` exists, structurally matches `clickhouse/init.sql`, and `deployment.yaml` mounts it at `/docker-entrypoint-initdb.d`.
- [x] Kubernetes: `request_metrics` existed immediately after pod startup and after ClickHouse rollout restart. A live OCR job created one `doc-ocr` success row.
- [x] Compose default unchanged: `CLICKHOUSE_HOST` was empty in both worker containers, no metric-insert warnings were observed, and smoke passed.
- [x] Compose with the temporary ClickHouse recipe: three OCR and three ASR success rows were observed.
- [x] `pytest -q` passed with `102 passed, 8 warnings`.
- [x] The scoped implementation touched `k8s/clickhouse/`, `docker-compose.yml`, and the four Phase 12 docs; baseline refresh files were added with explicit approval.
- [x] Documentation states automatic Kubernetes initialization, Compose pass-through, and the missing ClickHouse persistent volume.

## Phase 12 completion evidence

- Live Minikube verification used a 3 GiB OCR limit and completed a synthetic OCR request with text `HELLO MODELMESH 12345`.
- ClickHouse startup and restart logs showed the init SQL being run.
- The table was verified with `DESCRIBE TABLE request_metrics` after restart.
- The live Kubernetes OCR job returned `success`, and the metrics table reported `doc-ocr success 1`.
- Minikube was stopped and Docker Compose was restarted after verification.
