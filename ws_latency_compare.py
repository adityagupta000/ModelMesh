import os
#!/usr/bin/env python3
"""
Compare WebSocket inference latency: HTTP-backed vs gRPC-backed workers.

This times the actual code path that responds to protocol switching
(/v1/ws/infer/{model_name}), unlike Artillery against /v1/infer/{model_name}
which bypasses the registry's protocol field entirely.

Usage:
    python ws_latency_compare.py <admin_key> <model_name> <test_file> [n_requests]

Example:
    python ws_latency_compare.py $ADMIN_KEY doc-ocr test-image.png 20
    python ws_latency_compare.py $ADMIN_KEY asr test-audio.wav 20
"""
import asyncio
import json
import statistics
import sys
import time

import httpx
import websockets

GATEWAY_HTTP = os.environ.get("GW", "http://localhost:8000")
GATEWAY_WS = os.environ.get("GW", "http://localhost:8000").replace("http://", "ws://")


async def get_model_id(admin_key: str, model_name: str) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{GATEWAY_HTTP}/v1/models/{model_name}")
        resp.raise_for_status()
        return resp.json()["id"]


async def set_protocol(admin_key: str, model_id: str, protocol: str, endpoint: str):
    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            f"{GATEWAY_HTTP}/v1/models/{model_id}",
            headers={"Authorization": f"Bearer {admin_key}"},
            json={"protocol": protocol, "endpoint": endpoint},
        )
        resp.raise_for_status()


async def time_one_request(admin_key: str, model_name: str, file_bytes: bytes) -> float:
    """Returns latency in ms for one full WS round trip (connect -> final result)."""
    uri = f"{GATEWAY_WS}/v1/ws/infer/{model_name}?api_key={admin_key}&version=v1"
    start = time.perf_counter()

    async with websockets.connect(uri) as ws:
        ready_raw = await ws.recv()
        ready = json.loads(ready_raw)
        if "error" in ready:
            raise RuntimeError(f"Server error before send: {ready['error']}")

        await ws.send(file_bytes)

        while True:
            msg_raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
            msg = json.loads(msg_raw)
            if "error" in msg:
                raise RuntimeError(f"Server error: {msg['error']}")
            if msg.get("is_final"):
                break
            if "result" in msg:
                break

    elapsed_ms = (time.perf_counter() - start) * 1000
    return elapsed_ms


async def run_batch(admin_key: str, model_name: str, file_bytes: bytes, n: int) -> list:
    latencies = []
    for i in range(n):
        try:
            ms = await time_one_request(admin_key, model_name, file_bytes)
            latencies.append(ms)
            print(f"  request {i+1}/{n}: {ms:.1f} ms")
        except Exception as e:
            print(f"  request {i+1}/{n}: FAILED ({e})")
    return latencies


def summarize(label: str, latencies: list):
    if not latencies:
        print(f"\n{label}: no successful requests")
        return
    latencies_sorted = sorted(latencies)
    n = len(latencies_sorted)
    p50 = statistics.median(latencies_sorted)
    p95 = latencies_sorted[min(int(n * 0.95), n - 1)]
    p99 = latencies_sorted[min(int(n * 0.99), n - 1)]
    print(f"\n{label} (n={n}):")
    print(f"  min:    {min(latencies_sorted):.1f} ms")
    print(f"  p50:    {p50:.1f} ms")
    print(f"  p95:    {p95:.1f} ms")
    print(f"  p99:    {p99:.1f} ms")
    print(f"  max:    {max(latencies_sorted):.1f} ms")
    print(f"  mean:   {statistics.mean(latencies_sorted):.1f} ms")


HTTP_ENDPOINTS = {
    "doc-ocr": "http://doc-ocr-worker.modelmesh.svc.cluster.local:8001/infer",
    "asr": "http://asr-worker.modelmesh.svc.cluster.local:8002/infer",
}
GRPC_ENDPOINTS = {
    "doc-ocr": "doc-ocr-worker.modelmesh.svc.cluster.local:50051",
    "asr": "asr-worker.modelmesh.svc.cluster.local:50051",
}


async def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    admin_key = sys.argv[1]
    model_name = sys.argv[2]
    test_file = sys.argv[3]
    n = int(sys.argv[4]) if len(sys.argv) > 4 else 20

    if model_name not in HTTP_ENDPOINTS:
        print(f"Unknown model_name '{model_name}', expected one of {list(HTTP_ENDPOINTS)}")
        sys.exit(1)

    with open(test_file, "rb") as f:
        file_bytes = f.read()

    model_id = await get_model_id(admin_key, model_name)
    print(f"Model '{model_name}' resolved to id={model_id}")

    print(f"\n=== Setting {model_name} to protocol=http ===")
    await set_protocol(admin_key, model_id, "http", HTTP_ENDPOINTS[model_name])
    await asyncio.sleep(0.5)
    print(f"Running {n} requests over WebSocket (http-backed)...")
    http_latencies = await run_batch(admin_key, model_name, file_bytes, n)

    print(f"\n=== Setting {model_name} to protocol=grpc ===")
    await set_protocol(admin_key, model_id, "grpc", GRPC_ENDPOINTS[model_name])
    await asyncio.sleep(0.5)
    print(f"Running {n} requests over WebSocket (grpc-backed)...")
    grpc_latencies = await run_batch(admin_key, model_name, file_bytes, n)

    print("\n" + "=" * 50)
    print(f"RESULTS: {model_name}, n={n} requests each")
    print("=" * 50)
    summarize("HTTP-backed", http_latencies)
    summarize("gRPC-backed", grpc_latencies)

    if http_latencies and grpc_latencies:
        http_p50 = statistics.median(http_latencies)
        grpc_p50 = statistics.median(grpc_latencies)
        diff_pct = ((grpc_p50 - http_p50) / http_p50) * 100
        direction = "slower" if diff_pct > 0 else "faster"
        print(f"\ngRPC p50 is {abs(diff_pct):.1f}% {direction} than HTTP p50 for this payload/model.")


if __name__ == "__main__":
    asyncio.run(main())