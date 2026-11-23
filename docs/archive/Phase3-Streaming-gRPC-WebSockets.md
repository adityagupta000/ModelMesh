# Phase 3 — WebSocket Inference + gRPC

## Implementation Status: COMPLETE ✓

**Goal:** WebSocket inference via HTTP or gRPC, with protobuf-based gRPC workers.

**Actual time:** Completed

---

## What Was Implemented

### Part A: WebSocket Inference (Client-Facing) - COMPLETE

- WebSocket endpoint `/v1/ws/infer/{model_name}`
- API key auth via query parameter (browser WebSocket limitation)
- Whole-file inference responses over WebSocket; the current client flow is not incremental audio upload
- Test clients (Python CLI + browser HTML)

### Part B: gRPC (WebSocket Inference Communication) - COMPLETE

- Protobuf contract (`inference.proto`)
- Unary RPC (`Predict`) for direct inference calls
- Bidirectional ASR RPC (`PredictStream`); the current gateway sends the uploaded file as a single final chunk
- gRPC servers in all workers
- gRPC client in gateway
- Protocol selection via registry on the WebSocket inference path (HTTP ↔ gRPC)

### Architecture

```
Client → Gateway (WebSocket) → Worker (HTTP or gRPC) → Result
         Direct inference path; protocol is read from the registry

Client → Gateway (REST) → Redis Streams → Worker consumer → Result in Redis
         Async job path; it does not dispatch on the registry protocol
```

---

## Definition of Done

- [x] WebSocket endpoint returns inference results
- [x] Test clients (Python + HTML) working
- [x] `.proto` contract defined
- [x] WebSocket registry rows can select HTTP or gRPC without gateway code changes
- [x] Gateway-worker gRPC calls on the WebSocket path
- [x] Bidirectional streaming gRPC for ASR; the current gateway path sends one final chunk, not token-level streaming
- [x] Before/after latency comparison recorded
- [x] Can explain whether gRPC helped and why

---

## Key Design Decisions

**Why WebSockets?**

- Avoids REST job-status polling for this direct inference path
- Uses a full-duplex connection, but the current API sends one whole file and returns one final result
- Does not provide incremental upload, transcription tokens, or progress updates

**Trade-off**: More complex client lifecycle management. The current gateway receives the full file before forwarding it; it does not provide incremental client-to-worker streaming.

**Why gRPC?**

- Binary protocol (faster than JSON)
- Strongly-typed contracts (protobuf)
- Built-in streaming
- Lower latency for internal calls

**Trade-off**: More complex setup (protobuf compilation)

**Protocol Abstraction**: The WebSocket inference path reads protocol and endpoint from the registry. Switching HTTP → gRPC there is a data change:

```sql
UPDATE models SET protocol = 'grpc', endpoint = 'doc-ocr-worker:50051' WHERE name = 'doc-ocr';
```

Zero gateway code changes required.

The REST `/v1/infer/{model_name}` path enqueues to Redis Streams, and worker consumers call their inference functions directly. It does not read the model protocol field or route that request through gRPC.

**Streaming caveat**: Whisper doesn't support token-level streaming. The ASR gRPC RPC can yield outputs for chunks, but the current gateway sends a whole uploaded file as one chunk with `is_final: true`. This is not incremental client streaming or token-by-token transcription.

---

## Files Implemented

- `inference.proto` - Protobuf contract
- `gateway/grpc_client.py` - gRPC client
- `workers/doc_ocr/grpc_server.py` - Unary Predict RPC
- `workers/asr/grpc_server.py` - Predict + PredictStream RPCs
- `workers/*/entrypoint.sh` - Run HTTP + gRPC + Redis consumer concurrently
- `gateway/main.py` - WebSocket endpoint + gRPC routing
- `test_ws_client.py` - Python WebSocket test client
- `test_ws_client.html` - Browser WebSocket test client
- Updated Dockerfiles for protobuf compilation
- Updated docker-compose.yml for gRPC ports

---

## Multi-Service Containers

Workers start THREE separate processes in one container:

1. HTTP server (Phase 1 legacy, port 8001)
2. gRPC server (Phase 3, port 50051)
3. Redis consumer (Phase 2, foreground)

Achieved via entrypoint script supervision of HTTP and gRPC servers and the Redis consumer. These processes do not share in-memory model instances; each process can load its own model. Final Phase 9 Compose measurements: ASR 1.084 GiB and doc-ocr 1.185 GiB. A later local Minikube OCR sample measured a 2,503 MiB container peak; the Kubernetes doc-ocr limit is 3 GiB. This was a small synthetic sample, not large-document stress testing.

---

## Testing

### WebSocket Test

```bash
python test_ws_client.py <api_key> asr test-audio.wav
```

Browser: Open `test_ws_client.html`, enter API key, select file, click "Connect & Send"

### Switch Protocol via Registry

```sql
-- HTTP
UPDATE models SET protocol = 'http', endpoint = 'http://doc-ocr-worker:8001/infer' WHERE name = 'doc-ocr';

-- gRPC
UPDATE models SET protocol = 'grpc', endpoint = 'doc-ocr-worker:50051' WHERE name = 'doc-ocr';
```

No code changes, no restarts. Just data.

---

## Performance Notes

Two different comparisons are recorded in [`baseline-measurements.md`](baseline-measurements.md). The Artillery run measured REST job-acceptance latency and could not compare HTTP with gRPC because REST jobs do not use protocol dispatch. A separate custom script timed 20 sequential whole-file WebSocket round trips per protocol and model. Those results are historical measurements, not a performance guarantee; the WebSocket client flow is not incremental streaming.

---

## Next Phase

Phase 4: Kubernetes Deployment (minikube local cluster)
