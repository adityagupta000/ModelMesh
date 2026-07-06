#!/bin/bash
set -e

# Generate gRPC stubs
cd /app
python3 -m grpc_tools.protoc -I. --python_out=. --pyi_out=. --grpc_python_out=. inference.proto

# Start the HTTP server in the background
uvicorn main:app --host 0.0.0.0 --port 8002 &

# Start the gRPC server in the background
python grpc_server.py &

# Start the Redis consumer in the foreground
python consumer.py
