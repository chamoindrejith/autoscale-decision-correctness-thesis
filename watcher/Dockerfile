# Slim image for the HPA watcher.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY watcher.py /app/

# Writable directory for JSONL output; k8s will mount a volume here.
RUN mkdir -p /data
VOLUME ["/data"]

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "/app/watcher.py"]
