# Phase 3 — Streaming (WebSockets + gRPC)

## Implementation Status: COMPLETE ✓

**Goal:** Real-time streaming via WebSockets + high-performance gRPC for internal communication, all registry-driven.

**Actual time:** Completed

---

## What Was Implemented

### Part A: WebSockets (Client-Facing Streaming) - COMPLETE
- WebSocket endpoint `/v1/ws/infer/{model_name}`
- API key auth via query parameter (browser WebSocket limitation)
- Real-time result streaming
- Test clients (Python CLI + browser HTML)

### Part B: gRPC (Internal Worker Communication) - COMPLETE
- Protobuf contract (`inference.proto`)
- Unary RPC (`Predict`) for doc-ocr
- Bidirectional streaming RPC (`PredictStream`) for ASR
- gRPC servers in all workers
- gRPC client in gateway
- Protocol switching via registry (HTTP ↔ gRPC)

### Architecture
```
Client → Gateway (WebSocket) → Worker (gRPC stream) → Partial results
         Real-time push

Client → Gateway (HTTP) → Worker (gRPC unary) → Result
         Sync path with gRPC backend
```

---

## Definition of Done

- [x] WebSocket endpoint streaming partial results
- [x] Test clients (Python + HTML) working
- [x] `.proto` contract defined
- [x] Registry rows updated protocol: http → grpc with zero code changes
- [x] Gateway-worker communication over gRPC
- [x] Bidirectional streaming gRPC for ASR
- [x] Before/after latency comparison recorded
- [x] Can explain whether gRPC helped and why

---

## Key Design Decisions

**Why WebSockets?**
- Eliminates polling from Phase 2
- Server pushes updates as they arrive
- Bi-directional streaming
- Lower latency for real-time use cases

**Trade-off**: More complex client lifecycle management

**Why gRPC?**
- Binary protocol (faster than JSON)
- Strongly-typed contracts (protobuf)
- Built-in streaming
- Lower latency for internal calls

**Trade-off**: More complex setup (protobuf compilation)

**Protocol Abstraction**: Phase 1's registry design paid off. Switching HTTP → gRPC is a data change:
```sql
UPDATE models SET protocol = 'grpc', endpoint = 'doc-ocr-worker:50051' WHERE name = 'doc-ocr';
```
Zero gateway code changes required.

**Streaming caveat**: Whisper doesn't support token-level streaming. Implementation is chunk-level (send full audio, get result when ready, mark `is_final: true`). Honest streaming behavior, but not token-by-token. Infrastructure ready for future models with true incremental output.

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

Workers now run THREE services concurrently:
1. HTTP server (Phase 1 legacy, port 8001)
2. gRPC server (Phase 3, port 50051)
3. Redis consumer (Phase 2, foreground)

Achieved via entrypoint script backgrounding HTTP and gRPC, running consumer in foreground.

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

Latency comparison (HTTP vs gRPC) measured via Artillery load test. Results depend on payload size and network overhead. For containerized networking with small payloads, difference may be minimal. True value: streaming capability + type safety, not raw speed for this workload.

---

## Next Phase

Phase 4: Kubernetes Deployment (minikube local cluster)
