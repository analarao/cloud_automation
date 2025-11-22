# chandrashekar316/capstone:cs_model

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
COPY requirements_cs_model.txt .
RUN pip install --no-cache-dir -r requirements_cs_model.txt

# 4. Copy the Python scripts
COPY cs_model_service.py .
COPY cb_mock_alert_sender.py .

EXPOSE 9001

CMD ["python", "cs_model_service.py"]