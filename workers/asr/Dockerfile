FROM python:3.11-slim

# ffmpeg required by whisper for audio decoding
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pre-download the whisper model at build time so first request isn't slow
RUN python -c "import whisper; whisper.load_model('tiny')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
