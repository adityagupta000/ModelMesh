import grpc
import json
import logging
import inference_pb2
import inference_pb2_grpc

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def call_worker_grpc(endpoint: str, model_name: str, payload: bytes) -> dict:
    """
    Call worker via gRPC Predict RPC (unary).

    Args:
        endpoint: Worker gRPC endpoint (e.g., "doc-ocr-worker:50051")
        model_name: Model name for the request
        payload: Raw bytes to process

    Returns:
        dict: Parsed result from worker
    """
    try:
        async with grpc.aio.insecure_channel(endpoint) as channel:
            stub = inference_pb2_grpc.InferenceWorkerStub(channel)

            request = inference_pb2.InferenceRequest(
                payload=payload,
                model_name=model_name
            )

            response = await stub.Predict(request, timeout=30.0)

            logger.info(f"gRPC call to {endpoint} completed in {response.latency_ms:.2f}ms")

            return json.loads(response.result_json)

    except grpc.RpcError as e:
        logger.error(f"gRPC error calling {endpoint}: {e.code()}: {e.details()}")
        raise RuntimeError(f"Worker gRPC call failed: {e.details()}")
    except Exception as e:
        logger.error(f"Error calling worker via gRPC: {e}")
        raise


async def stream_worker_grpc(endpoint: str, model_name: str, chunks):
    """
    Call worker via gRPC PredictStream RPC (bidirectional streaming).

    Args:
        endpoint: Worker gRPC endpoint
        model_name: Model name
        chunks: Async generator/iterator of (bytes, sequence, is_final) tuples

    Yields:
        dict: Partial results as they arrive
    """
    try:
        async with grpc.aio.insecure_channel(endpoint) as channel:
            stub = inference_pb2_grpc.InferenceWorkerStub(channel)

            # Create request generator
            async def request_generator():
                async for chunk_data, sequence, is_final in chunks:
                    yield inference_pb2.InferenceChunk(
                        chunk=chunk_data,
                        sequence=sequence,
                        is_final=is_final
                    )

            # Call streaming RPC
            response_stream = stub.PredictStream(request_generator(), timeout=60.0)

            # Yield partial results as they arrive
            async for result in response_stream:
                yield {
                    "text": result.partial_text,
                    "is_final": result.is_final,
                    "confidence": result.confidence
                }

    except grpc.RpcError as e:
        logger.error(f"gRPC streaming error: {e.code()}: {e.details()}")
        raise RuntimeError(f"Worker gRPC stream failed: {e.details()}")
    except Exception as e:
        logger.error(f"Error in gRPC streaming: {e}")
        raise
