FROM python:3.11-slim

WORKDIR /app

COPY gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY inference.proto .
COPY gateway/ .

RUN python3 -m grpc_tools.protoc -I. --python_out=. --pyi_out=. --grpc_python_out=. inference.proto

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port 8000 --ws-max-size ${MAX_UPLOAD_BYTES:-10485760}"]
