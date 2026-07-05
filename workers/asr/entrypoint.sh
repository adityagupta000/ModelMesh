#!/bin/bash
set -e

# Start the HTTP server in the background
uvicorn main:app --host 0.0.0.0 --port 8002 &

# Start the Redis consumer in the foreground
python consumer.py
