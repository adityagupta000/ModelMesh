# Prerequisites — What to Know Before Starting ModelMesh

Honest breakdown: what you already have covered, what you need to learn before each phase, and what to actually install. Don't try to learn everything upfront — learn just-in-time, phase by phase. Trying to master gRPC before you've even started Phase 1 is how these projects die before they start.

---

## 1. Already covered — skip these, don't waste time "reviewing"

- Python + FastAPI (route handlers, dependency injection, Pydantic models)
- JWT auth, rate limiting, session management, timing-safe comparison
- Docker, Docker Compose, Docker Secrets
- PostgreSQL basics (schema design, queries)
- Redis basics (get/set, expiry)
- PyTorch inference (loading a trained model, running predictions)
- pytest/httpx or equivalent testing discipline (you have this from Jest/Supertest)
- Artillery load testing
- Git

You genuinely don't need to "study" any of this before Phase 1 — you already build with it.

---

## 2. Tools to install now (before Phase 1)

```bash
# Python environment
python3 --version   # 3.10+ recommended
pip install fastapi uvicorn httpx pytest pytest-asyncio
pip install asyncpg redis python-multipart

# Whisper for the ASR worker
pip install openai-whisper
# Whisper needs ffmpeg on the system:
sudo apt install ffmpeg   # or brew install ffmpeg on Mac

# Docker + Docker Compose
docker --version
docker compose version

# Postman or similar, for manual endpoint testing (you already use this)
```

Get all of this installed and working — `whisper.load_model("tiny")` running once successfully — before you write any gateway code. Debugging environment setup mid-build is a bigger time sink than doing it upfront.

---

## 3. Before Phase 2 (Async Job Queue) — learn these

**Concept-level, not deep expertise yet:**

- What a message queue actually does and why (decoupling producer/consumer, buffering load spikes) — if this is new, spend 30–60 minutes on a conceptual overview before touching code
- Redis Streams specifically: `XADD`, `XREADGROUP`, `XACK`, consumer groups — the [official Redis Streams intro](https://redis.io/docs/latest/develop/data-types/streams/) is enough; you don't need a course
- Basic Kafka concepts if you get to it: topics, partitions, consumer groups, offsets

**Install:**

```bash
# Redis Streams needs nothing extra — it's built into Redis you already have

# For Kafka (later, optional in Phase 2):
# Easiest local setup: bitnami/kafka Docker image with KRaft mode (no Zookeeper)
pip install aiokafka
```

**Time to get comfortable:** a few hours of reading + your first working consumer loop will teach you more than any tutorial. Don't over-prepare here — start building once you understand `XADD`/`XREADGROUP` conceptually.

---

## 4. Before Phase 3 (Streaming) — learn these, this is the real investment

This phase has the steepest genuine learning curve. Budget real study time, not just "read as you go."

**WebSockets (lighter lift):**

- Core concept: persistent connection, `send`/`receive` instead of request/response
- FastAPI's `WebSocket` class — a couple hours with the [FastAPI WebSockets docs](https://fastapi.tiangolo.com/advanced/websockets/) is enough

**gRPC (the real learning curve — budget 2–3 focused days before/alongside building):**

- Protocol Buffers: how `.proto` files define message types and services, and how `protoc` generates code from them
- Unary vs. server-streaming vs. client-streaming vs. bidirectional-streaming RPCs — know the difference before you pick one
- How gRPC channels and stubs work in Python (`grpc.aio` for async)

**Suggested learning path:**

1. Do the official gRPC Python "Quick Start" tutorial end-to-end (not skimmed) — this alone takes a few hours but is worth doing properly
2. Write one trivial `.proto` (e.g. a simple echo service) and get client/server talking before touching your real workers
3. Only then move it into ModelMesh

**Install:**

```bash
pip install grpcio grpcio-tools
```

---

## 5. Before Phase 4 (Kubernetes) — learn these

This is the second-biggest learning investment. Kubernetes has a lot of surface area — you don't need all of it, just the subset below.

**Core concepts you actually need:**

- Pods, Deployments, Services (ClusterIP vs. NodePort), Namespaces
- ConfigMaps and Secrets
- Liveness vs. readiness probes — know the conceptual difference before writing them
- StatefulSets vs. Deployments — specifically why Postgres needs one and your workers don't
- `kubectl` basics: `apply`, `get pods`, `describe`, `logs`, `delete`, `scale`

**What you can skip for now:** Ingress controllers, service meshes (Istio/Linkerd — only relevant if you attempt weighted canary traffic splitting in Phase 5), Helm charts beyond the two you'll install in Phase 5, RBAC, network policies. These are real K8s topics but not required for this project's scope.

**Suggested learning path:**

1. Kubernetes official "Basics" interactive tutorial (kubernetes.io) — a half day, hands-on, worth doing before minikube
2. Install minikube, get a trivial "hello world" pod running before touching ModelMesh's real YAML
3. Deploy one of your services (start with Redis, it's stateless and simple) before attempting the full stack

**Install:**

```bash
# minikube
curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64
sudo install minikube-linux-amd64 /usr/local/bin/minikube

kubectl version --client
minikube start
```

**Time estimate:** 3–4 days of learning + hands-on practice before you're comfortable enough to write ModelMesh's actual manifests confidently. This is not optional prep — going in blind here is where most self-taught K8s attempts stall out.

---

## 6. Before Phase 5 (Observability + Canary) — learn these

**ClickHouse:**

- It's SQL-like, so your Postgres knowledge transfers most of the way
- New concept: columnar storage and why `ORDER BY` in `MergeTree` tables matters for query performance — 30–60 minutes of reading is enough
- `clickhouse-connect` Python client basics

**Prometheus + Grafana:**

- Prometheus concept: pull-based scraping, metric types (Counter, Histogram, Gauge) — know the difference, you'll use both Counter and Histogram
- PromQL basics: enough to write a p95 query and a rate() query
- Grafana: how to add a data source and build a panel — this is mostly point-and-click, minimal study needed

**Canary deployment concept:**

- Understand what you're actually testing for (error rate delta, latency delta) before building the mechanism — this is a judgment/reasoning skill, not a tool skill, and it's worth thinking through on paper first

**Install:**

```bash
pip install clickhouse-connect prometheus-fastapi-instrumentator prometheus-client

# Prometheus + Grafana deployed via Helm (commands in Phase 5 doc)
helm version   # install helm first if you don't have it
```

**Time estimate:** 1–2 days of learning, since this phase leans more on your existing SQL/dashboard instincts than genuinely new mental models.

---

## 7. Total honest prep time, phase by phase

| Phase | New learning required                          | Realistic prep time |
| ----- | ---------------------------------------------- | ------------------- |
| 1     | None — all existing skills                     | 0 days              |
| 2     | Redis Streams concepts, optionally Kafka       | 0.5–1 day           |
| 3     | WebSockets (light), gRPC + protobufs (heavy)   | 2–3 days            |
| 4     | Kubernetes core concepts                       | 3–4 days            |
| 5     | ClickHouse (light), Prometheus/Grafana (light) | 1–2 days            |

**Total standalone learning time: roughly 7–10 days**, on top of the 4–5 weeks of build time already estimated. Realistically, don't front-load all of this — learn gRPC right before Phase 3, learn Kubernetes right before Phase 4. Concepts you learn and don't immediately use fade fast; just-in-time learning will stick better and won't stall your momentum on Phase 1.

---

## 8. The one thing worth doing before any of this

Skim the actual Sarvam JD one more time and read one or two engineering blog posts from companies doing similar ML-serving-at-scale work (Hugging Face's inference endpoints writeups, or Anyscale/Ray Serve blog posts are good examples). Not to copy their architecture, but so the vocabulary — "cold start," "batching," "backpressure," "canary" — feels familiar rather than newly memorized when you're building and later interviewing.
