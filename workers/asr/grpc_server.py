import grpc
import json
import time
import logging
import io
from concurrent import futures
import inference_pb2
import inference_pb2_grpc
from inference import transcribe

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class InferenceWorkerServicer(inference_pb2_grpc.InferenceWorkerServicer):
    def Predict(self, request, context):
        """Unary RPC for single inference request."""
        start = time.perf_counter()

        try:
            logger.info(f"gRPC Predict called for model: {request.model_name}")

            # Run inference
            result = transcribe(request.payload)

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

    def PredictStream(self, request_iterator, context):
        """
        Bidirectional streaming RPC for progressive ASR inference.

        Receives audio chunks, processes them incrementally, and yields
        partial transcription results as they become available.
        """
        logger.info("gRPC PredictStream called - bidirectional streaming")

        try:
            # Collect chunks
            chunks = []
            for chunk_msg in request_iterator:
                logger.debug(f"Received chunk {chunk_msg.sequence}, size: {len(chunk_msg.chunk)}")
                chunks.append((chunk_msg.sequence, chunk_msg.chunk, chunk_msg.is_final))

                # Process chunk immediately for streaming
                if len(chunk_msg.chunk) > 0:
                    try:
                        # Transcribe this chunk
                        result = transcribe(chunk_msg.chunk)

                        # Yield partial result
                        yield inference_pb2.InferenceResult(
                            partial_text=result.get("text", ""),
                            is_final=chunk_msg.is_final,
                            confidence=result.get("confidence", 0.0)
                        )

                        logger.info(f"Streamed partial result for chunk {chunk_msg.sequence}")

                        # If final chunk, we're done
                        if chunk_msg.is_final:
                            break

                    except Exception as e:
                        logger.error(f"Failed to process chunk {chunk_msg.sequence}: {e}")
                        yield inference_pb2.InferenceResult(
                            partial_text=f"[Error: {str(e)}]",
                            is_final=chunk_msg.is_final,
                            confidence=0.0
                        )

        except Exception as e:
            logger.error(f"Stream processing failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))


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
