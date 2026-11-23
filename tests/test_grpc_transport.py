import asyncio
import io
import json
import wave
from concurrent import futures

import grpc
import pytest

from grpc_client import MAX_MSG, call_worker_grpc
import inference_pb2
import inference_pb2_grpc


@pytest.mark.asyncio(scope="session")
async def test_six_mib_wav_payload_traverses_grpc_transport():
    target_size = 6 * 1024 * 1024
    wav_data_size = target_size - 44
    assert wav_data_size % 2 == 0
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00" * wav_data_size)
    payload = buffer.getvalue()
    assert len(payload) == target_size
    assert payload[:4] == b"RIFF" and payload[8:12] == b"WAVE"
    assert len(payload) < MAX_MSG

    class PayloadSizeServicer(inference_pb2_grpc.InferenceWorkerServicer):
        def Predict(self, request, context):
            return inference_pb2.InferenceResponse(
                result_json=json.dumps({"payload_bytes": len(request.payload)}),
                latency_ms=0,
            )

    executor = futures.ThreadPoolExecutor(max_workers=1)
    server = grpc.server(
        executor,
        options=[
            ("grpc.max_send_message_length", MAX_MSG),
            ("grpc.max_receive_message_length", MAX_MSG),
        ],
    )
    inference_pb2_grpc.add_InferenceWorkerServicer_to_server(
        PayloadSizeServicer(), server
    )
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        result = await call_worker_grpc(
            f"127.0.0.1:{port}", "asr", payload
        )
    finally:
        await asyncio.to_thread(lambda: server.stop(0).wait())
        executor.shutdown(wait=True)

    assert result == {"payload_bytes": target_size}
