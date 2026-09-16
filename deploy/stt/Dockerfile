# Speech recognition service of the `ai` profile: faster-whisper on CPU behind a small HTTP API
# compatible with the OpenAI transcription endpoint (so a GPU node can replace it, track G).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STT_MODEL_DIR=/models/stt/faster-whisper-base \
    STT_COMPUTE_TYPE=int8 \
    STT_THREADS=4

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py ./

EXPOSE 9000
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9000/health')"
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "9000"]
