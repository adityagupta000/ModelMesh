#!/bin/bash
# Generate Python gRPC stubs from protobuf
python3 -m grpc_tools.protoc -I. --python_out=. --pyi_out=. --grpc_python_out=. inference.proto
