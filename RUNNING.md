# Running ModelMesh

Run commands from the repository root unless noted.

## Full application: Docker Compose

First build and start:

```powershell
docker compose up --build -d
```

Later starts:

```powershell
docker compose up -d
docker compose ps
```

After changing gateway or worker code, rebuild with `docker compose up --build -d`; plain `docker compose up -d` reuses the existing images.

Stop and keep the containers:

```powershell
docker compose stop
```

Resume stopped containers:

```powershell
docker compose start
```

Stop and remove the containers and network, keeping database volumes:

```powershell
docker compose down
```

Avoid `docker compose down -v` unless you intend to delete persisted database data.

## Python virtualenv: tests and client scripts

The virtualenv does not start the gateway, workers, Postgres, or Redis. Start the full app with Docker Compose first. In an Ubuntu WSL terminal:

```bash
cd /mnt/c/Project_Files/modelmesh
source "$HOME/modelmesh/modelmesh-env/bin/activate"

# Example: run the WebSocket client (set API_KEY to a valid key first)
python test_ws_client.py "$API_KEY" asr fixtures/sample_audio.wav

# Leave the virtualenv when finished
deactivate
```

The client exits when its test completes. For a foreground Python process, press `Ctrl+C` to stop it, then run `deactivate`. The Phase 0 pytest baseline had failures; see [docs/run-baseline.md](docs/run-baseline.md).

The working WSL virtualenv must be on the Linux filesystem. On this workstation it is `$HOME/modelmesh/modelmesh-env`; a copy under `/mnt/c` can contain empty Python launcher files and should not be activated. The test requirements retain NumPy 2.4.4 and OpenCV headless 5.0.0.93.

## Running the tests

From Ubuntu WSL at the repository root, publish the database ports with the test-only Compose override, bootstrap the scratch database, and run the suite twice:

```bash
cd /mnt/c/Project_Files/modelmesh
docker compose -f docker-compose.yml -f docker-compose.test.yml up -d

# Skip only this CREATE command if modelmesh_test already exists. Never drop it.
docker compose exec -T postgres psql -U user -d postgres -c "CREATE DATABASE modelmesh_test;"
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U user -d modelmesh_test < db/init.sql
for f in migrations/*.sql; do
  docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U user -d modelmesh_test < "$f"
done

export TEST_DATABASE_URL=postgresql://user:pass@localhost/modelmesh_test
export TEST_REDIS_URL=redis://localhost:6379/1
source "$HOME/modelmesh/modelmesh-env/bin/activate"
pip install --dry-run -r tests/requirements.txt
pip install -r tests/requirements.txt
pytest -q
pytest -q
deactivate
```

Tests use the scratch database `modelmesh_test` and Redis DB 1, never the `modelmesh` database or Redis DB 0. If `modelmesh_test` already exists, skip only the `CREATE DATABASE` command; the idempotent schema and migration commands may be rerun. Never drop it. Only starting Compose needs the two `-f` flags. Plain `docker compose up -d` removes the published database ports, so use the override form whenever running tests. If Postgres or Redis are recreated while the app is running, run `docker compose restart gateway doc-ocr-worker asr-worker`.

For the full test setup explanation, see [SETUP.md](SETUP.md#testing).
