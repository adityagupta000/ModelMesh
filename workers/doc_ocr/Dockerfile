FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir --timeout=120 --retries=5 torch torchvision --index-url https://download.pytorch.org/whl/cpu

COPY workers/doc_ocr/requirements.txt .
RUN pip install --no-cache-dir --timeout=120 --retries=5 -r requirements.txt

COPY inference.proto .
COPY workers/doc_ocr/ .

RUN python -c "import easyocr; easyocr.Reader(['en'], gpu=False)"

RUN chmod +x entrypoint.sh

CMD ["./entrypoint.sh"]
