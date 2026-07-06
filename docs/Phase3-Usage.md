# Phase 3 Usage Guide - WebSockets + gRPC

## Quick Start

```bash
docker-compose up --build
```

## Testing WebSocket Streaming

### Python CLI Client

```bash
# Install websockets
pip install websockets

# Test ASR streaming
python test_ws_client.py <your-api-key> asr test-audio.wav

# Test doc-ocr streaming
python test_ws_client.py <your-api-key> doc-ocr test-image.png
```

### Browser Client

1. Open `test_ws_client.html` in your browser
2. Enter your API key
3. Select model (asr or doc-ocr)
4. Choose an audio or image file
5. Click "Connect & Send"
6. Watch results stream in real-time

## Testing gRPC Workers

### Update Registry to Use gRPC

```bash
# Get admin JWT token
TOKEN=$(curl -X POST http://localhost:8000/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"admin123"}' | jq -r .access_token)

# Update doc-ocr to gRPC
curl -X PATCH http://localhost:8000/v1/models/<model-id> \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "protocol": "grpc",
    "endpoint": "doc-ocr-worker:50051"
  }'

# Update asr to gRPC
curl -X PATCH http://localhost:8000/v1/models/<model-id> \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "protocol": "grpc",
    "endpoint": "asr-worker:50051"
  }'
```

### Test HTTP API with gRPC Backend

```bash
# Submit job (gateway routes via gRPC internally)
curl -X POST http://localhost:8000/v1/infer/doc-ocr \
  -H "Authorization: Bearer <api-key>" \
  -F "file=@test-image.png"

# Response: {"job_id": "...", "status": "queued"}

# Check job status
curl http://localhost:8000/v1/jobs/<job-id> \
  -H "Authorization: Bearer <api-key>"
```

The gateway automatically routes to gRPC if `protocol: "grpc"` in registry. No client-side changes needed.

## Performance Testing

### HTTP Baseline (Phase 1)
```bash
# Update model to HTTP
curl -X PATCH http://localhost:8000/v1/models/<model-id> \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "protocol": "http",
    "endpoint": "http://doc-ocr-worker:8001/infer"
  }'

# Run load test
artillery run load-test.yml > http-results.txt
```

### gRPC Comparison (Phase 3)
```bash
# Update model to gRPC
curl -X PATCH http://localhost:8000/v1/models/<model-id> \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "protocol": "grpc",
    "endpoint": "doc-ocr-worker:50051"
  }'

# Run same load test
artillery run load-test.yml > grpc-results.txt

# Compare
diff http-results.txt grpc-results.txt
```

## Monitoring gRPC Services

### Check gRPC Server Status

```bash
# Install grpcurl
go install github.com/fullstorydev/grpcurl/cmd/grpcurl@latest

# List services
grpcurl -plaintext localhost:50051 list

# Call Predict RPC
grpcurl -plaintext -d '{"payload": "base64data", "model_name": "doc-ocr"}' \
  localhost:50051 modelmesh.InferenceWorker/Predict
```

### View Worker Logs

```bash
# doc-ocr logs
docker-compose logs -f doc-ocr-worker

# Look for:
# - "gRPC server started on port 50051"
# - "gRPC Predict called for model: ..."
# - Latency measurements

# asr logs
docker-compose logs -f asr-worker

# Look for:
# - "gRPC PredictStream called - bidirectional streaming"
# - "Streamed partial result for chunk X"
```

## Troubleshooting

### WebSocket Connection Refused

**Symptoms**: `Connection refused` or `404 Not Found`

**Check**:
1. Gateway is running: `curl http://localhost:8000/health`
2. WebSocket endpoint exists: Check `gateway/main.py` has `@app.websocket("/v1/ws/infer/{model_name}")`
3. API key is valid

### gRPC UNAVAILABLE Error

**Symptoms**: `grpc.RpcError: StatusCode.UNAVAILABLE`

**Check**:
1. Worker gRPC server is running: `docker-compose logs doc-ocr-worker | grep "gRPC server started"`
2. Port is exposed: Check `docker-compose.yml` has `50051:50051` mapping
3. Registry endpoint is correct: `grpcurl -plaintext doc-ocr-worker:50051 list`

### Protobuf Import Error

**Symptoms**: `ModuleNotFoundError: No module named 'inference_pb2'`

**Fix**:
```bash
# Regenerate stubs
cd /path/to/modelmesh
bash generate_protos.sh

# Or rebuild containers
docker-compose up --build
```

### WebSocket Closes Immediately

**Symptoms**: Client connects then disconnects with error code

**Error codes**:
- `4401`: Invalid API key
- `4404`: Model not found
- `4503`: Model disabled

**Check**:
1. API key is valid: `curl http://localhost:8000/v1/auth/keys -H "Authorization: Bearer <jwt>"`
2. Model exists: `curl http://localhost:8000/v1/models`
3. Model status is "active"

## Switching Between HTTP and gRPC

The beauty of Phase 1's registry design: switching protocols is just a data change.

### HTTP (Phase 1 behavior)
```sql
UPDATE models SET 
  protocol = 'http', 
  endpoint = 'http://doc-ocr-worker:8001/infer' 
WHERE name = 'doc-ocr';
```

### gRPC (Phase 3 behavior)
```sql
UPDATE models SET 
  protocol = 'grpc', 
  endpoint = 'doc-ocr-worker:50051' 
WHERE name = 'doc-ocr';
```

**Zero code changes**. Gateway reads protocol from registry and routes accordingly.

## Example: Complete Flow

```bash
# 1. Register user
curl -X POST http://localhost:8000/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"user@test.com","password":"pass123"}'

# 2. Login
TOKEN=$(curl -X POST http://localhost:8000/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"user@test.com","password":"pass123"}' | jq -r .access_token)

# 3. Create API key
API_KEY=$(curl -X POST http://localhost:8000/v1/auth/api-keys \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"rate_limit_per_min": 60}' | jq -r .key)

# 4. Test WebSocket streaming
python test_ws_client.py $API_KEY asr test-audio.wav

# 5. Test async job queue (Phase 2 still works)
JOB_ID=$(curl -X POST http://localhost:8000/v1/infer/asr \
  -H "Authorization: Bearer $API_KEY" \
  -F "file=@test-audio.wav" | jq -r .job_id)

curl http://localhost:8000/v1/jobs/$JOB_ID \
  -H "Authorization: Bearer $API_KEY"
```

## Next Steps

Phase 4 will add:
- Kubernetes deployment with Helm charts
- Horizontal pod autoscaling
- Service mesh (Istio) for gRPC load balancing
- Prometheus + Grafana observability

Phase 3 provides:
- Real-time streaming via WebSockets
- High-performance gRPC for internal communication
- Protocol abstraction via registry (HTTP ↔ gRPC switchable with zero code changes)
