FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install small set of system packages required for typical Python deps
RUN apt-get update \
    && apt-get install -y --no-install-recommends git build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

# Copy project (we intentionally don't COPY .env here; use docker-compose env_file)
COPY . /app

# Entrypoint helper
RUN chmod +x /app/entrypoint.sh || true

# Persist Chroma DB and runbook DB as volumes
VOLUME ["/app/chroma_db", "/app/runbook_db"]
ENV DB_PATH=/app/runbook_db

ENTRYPOINT ["/app/entrypoint.sh"]
