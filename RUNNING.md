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
source modelmesh-env/bin/activate

# Example: run the WebSocket client (set API_KEY to a valid key first)
python test_ws_client.py "$API_KEY" asr fixtures/sample_audio.wav

# Leave the virtualenv when finished
deactivate
```

The client exits when its test completes. For a foreground Python process, press `Ctrl+C` to stop it, then run `deactivate`. The Phase 0 pytest baseline had failures; see [docs/run-baseline.md](docs/run-baseline.md).
