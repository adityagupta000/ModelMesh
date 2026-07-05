FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN echo "setuptools<81" > /tmp/constraints.txt
ENV PIP_CONSTRAINT=/tmp/constraints.txt
RUN pip install --no-cache-dir --timeout=120 --retries=5 --upgrade pip "setuptools<81" wheel

RUN pip install --no-cache-dir --timeout=120 --retries=5 torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir --timeout=120 --retries=5 -r requirements.txt

COPY . .

RUN python -c "import whisper; whisper.load_model('tiny')"

RUN chmod +x entrypoint.sh

CMD ["./entrypoint.sh"]
