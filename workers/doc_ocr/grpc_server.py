import grpc
import json
import time
import logging
from concurrent import futures
import inference_pb2
import inference_pb2_grpc
from inference import extract_text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class InferenceWorkerServicer(inference_pb2_grpc.InferenceWorkerServicer):
    def Predict(self, request, context):
        """Unary RPC for single inference request."""
        start = time.perf_counter()

        try:
            logger.info(f"gRPC Predict called for model: {request.model_name}")

            # Run inference
            result = extract_text(request.payload)

            latency_ms = (time.perf_counter() - start) * 1000

            return inference_pb2.InferenceResponse(
                result_json=json.dumps(result),
                latency_ms=latency_ms
            )
        except Exception as e:
            logger.error(f"Prediction failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return inference_pb2.InferenceResponse()


def serve(port=50051):
    """Start the gRPC server."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    inference_pb2_grpc.add_InferenceWorkerServicer_to_server(
        InferenceWorkerServicer(), server
    )
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    logger.info(f"gRPC server started on port {port}")
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
