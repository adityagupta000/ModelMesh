# Phase 3 — Streaming (WebSockets + gRPC)

### ModelMesh Implementation Guide

**Goal:** Real-time partial results to clients via WebSockets, and internal worker communication moved from HTTP to gRPC — with the registry's `endpoint` and `protocol` fields, not hardcoded addresses, driving every connection.

**Estimated time:** 7 days — this is the hardest phase, budget real time for it.

**Prerequisite:** Phase 2 fully complete and checked off.

---

## Part A: WebSockets (client-facing streaming)

### 1. Why this matters

Right now clients poll `/v1/jobs/{job_id}`. That's wasteful and slow to feel real-time. A WebSocket lets you push partial results as they're ready — most useful for ASR, where you can stream transcribed words as Whisper processes chunks, rather than making the client wait for the whole file.

### 2. Gateway WebSocket endpoint — registry-resolved

```python
from fastapi import WebSocket

@app.websocket("/v1/ws/infer/{model_name}")
async def ws_infer(websocket: WebSocket, model_name: str):
    await websocket.accept()
    api_key = websocket.query_params.get("api_key")
    key_record = await verify_api_key_ws(api_key)
    if not key_record:
        await websocket.close(code=4401)
        return

    model = await registry.get_model(db, model_name)
    if not model or model.status != "active":
        await websocket.close(code=4404)
        return

    try:
        while True:
            data = await websocket.receive_bytes()
            partial = await stream_to_worker(model, data)   # model carries endpoint + protocol
            if partial:
                await websocket.send_json({"partial": partial})
    except WebSocketDisconnect:
        pass
```

Same principle as Phase 1's routing: the WebSocket handler doesn't know it's talking to Whisper specifically — it knows it's talking to whatever `model.endpoint`/`model.protocol` the registry resolved.

### 3. Streaming ASR specifically

Whisper doesn't natively stream token-by-token, but you can simulate meaningful streaming by:

- Chunking incoming audio into ~2–5 second windows
- Running inference per chunk
- Sending each chunk's transcription back over the WebSocket as it completes

This is honest streaming behavior (progressive results, not one big blocking response) even if it's not literal token-level streaming. Be accurate about this distinction if asked in an interview — don't overclaim "token-level streaming" if you built chunk-level streaming.

### 4. Client-side test

Write a small test client (Python `websockets` library or a simple HTML page) that connects, sends an audio file in chunks, and prints partial results as they arrive. This is your demo artifact — the thing you'd actually show someone in an interview.

---

## Part B: gRPC (internal worker communication)

### 1. Why gRPC instead of HTTP internally

HTTP/JSON is fine for a public API, but internal service-to-service calls benefit from gRPC's binary protocol (faster serialization), strongly-typed contracts (protobufs catch mismatches at compile time, not runtime), and built-in streaming support. This is also the JD's explicit ask: "deep understanding of HTTP, WebSockets, and gRPC protocols."

Update the registry rows to reflect the new protocol once workers speak gRPC:

```sql
UPDATE models SET protocol = 'grpc', endpoint = 'doc-ocr-worker:50051' WHERE name = 'doc-ocr';
UPDATE models SET protocol = 'grpc', endpoint = 'asr-worker:50051' WHERE name = 'asr';
```

This is the payoff of Phase 1's design: switching every model from HTTP to gRPC is a **data change**, not a **code change** — you're not touching gateway routing logic at all.

### 2. Define the protobuf contract

```protobuf
// inference.proto
syntax = "proto3";

package modelmesh;

service InferenceWorker {
  rpc Predict (InferenceRequest) returns (InferenceResponse);
  rpc PredictStream (stream InferenceChunk) returns (stream InferenceResult);
}

message InferenceRequest {
  bytes payload = 1;
  string model_name = 2;
}

message InferenceResponse {
  string result_json = 1;
  float latency_ms = 2;
}

message InferenceChunk {
  bytes chunk = 1;
  int32 sequence = 2;
}

message InferenceResult {
  string partial_text = 1;
  bool is_final = 2;
}
```

Generate the Python stubs:

```bash
pip install grpcio grpcio-tools
python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. inference.proto
```

### 3. Worker: implement the gRPC server

```python
# workers/doc_ocr/grpc_server.py
import grpc
from concurrent import futures
import inference_pb2, inference_pb2_grpc

class InferenceWorkerServicer(inference_pb2_grpc.InferenceWorkerServicer):
    def Predict(self, request, context):
        result = predict(request.payload)
        return inference_pb2.InferenceResponse(
            result_json=json.dumps(result),
            latency_ms=0.0  # fill in real timing
        )

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    inference_pb2_grpc.add_InferenceWorkerServicer_to_server(InferenceWorkerServicer(), server)
    server.add_insecure_port("[::]:50051")
    server.start()
    server.wait_for_termination()
```

### 4. Gateway: gRPC client — extends `route_request`, not a rewrite

Recall Phase 1's `route_request` already had a `NotImplementedError` placeholder for gRPC. Fill it in now:

```python
# gateway/grpc_client.py
import grpc
import inference_pb2, inference_pb2_grpc

async def call_worker_grpc(endpoint: str, model_name: str, payload: bytes):
    channel = grpc.aio.insecure_channel(endpoint)   # endpoint comes from the registry
    stub = inference_pb2_grpc.InferenceWorkerStub(channel)
    response = await stub.Predict(inference_pb2.InferenceRequest(payload=payload, model_name=model_name))
    return json.loads(response.result_json)
```

```python
async def route_request(endpoint: str, protocol: str, payload: bytes, model_name: str = ""):
    if protocol == "http":
        async with httpx.AsyncClient() as client:
            resp = await client.post(endpoint, content=payload)
            return resp.json()
    elif protocol == "grpc":
        return await call_worker_grpc(endpoint, model_name, payload)
    else:
        raise ValueError(f"Unsupported protocol: {protocol}")
```

This is the concrete proof that the registry investment in Phase 1 paid off: adding a whole new transport protocol touched one function, not every route handler.

### 5. Streaming gRPC (bidirectional)

For the ASR worker specifically, use the `PredictStream` bidirectional streaming RPC so the gateway can push audio chunks and receive partial transcriptions over the same gRPC connection — this is what feeds your WebSocket streaming to the end client.

---

## 3. Re-run your baseline load test

Go back to the Artillery config from Phase 1, run it again against the gRPC-backed path, and compare p50/p95/p99 latency against your original numbers. Write both sets down side by side. Whether gRPC actually improved latency in your specific setup (it may or may not, depending on payload sizes) is itself an honest, interesting thing to report — don't assume the answer, measure it.

---

## 4. Definition of done

- [ ] WebSocket endpoint streams partial ASR results to a connected client in real time, resolved via the registry
- [ ] Test client (script or simple page) demonstrates the streaming behavior end-to-end
- [ ] `.proto` contract defined for at least the Document OCR and ASR workers
- [ ] Registry rows updated from `protocol: http` to `protocol: grpc` with zero gateway route-handler changes
- [ ] Gateway↔worker communication runs over gRPC, not HTTP
- [ ] Bidirectional streaming gRPC used for at least the ASR path
- [ ] Before/after latency comparison recorded from Artillery (Phase 1 baseline vs. gRPC path)
- [ ] You can explain, honestly, whether gRPC helped in your case and why

Once checked off, move to Phase 4.
