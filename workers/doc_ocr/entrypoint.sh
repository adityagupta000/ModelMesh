#!/bin/bash
set -e
cd /app
python3 -m grpc_tools.protoc -I. --python_out=. --pyi_out=. --grpc_python_out=. inference.proto

uvicorn main:app --host 0.0.0.0 --port 8001 &
python grpc_server.py &
python consumer.py &

trap 'kill $(jobs -p) 2>/dev/null; exit 0' TERM INT
wait -n || true
kill $(jobs -p) 2>/dev/null || true
exit 1
