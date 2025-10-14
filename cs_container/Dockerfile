# cs_container/Dockerfile

# 1. Use a standard Debian-based Python image (UPGRADED to Bullseye/Debian 11)
FROM python:3.10-slim-bullseye 

WORKDIR /app

# 2. Install necessary system tools for debugging (procps provides ps, top)
# We can remove the complex sed commands and dependency hacks.
RUN apt-get update && apt-get install -y --no-install-recommends \
    procps \
    bash \
    net-tools \
    && rm -rf /var/lib/apt/lists/*

# 3. Copy requirements and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy the Python scripts
COPY ml_model_service.py .
COPY cb_mock_alert_sender.py .