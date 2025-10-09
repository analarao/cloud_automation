FROM python:3.10-slim

WORKDIR /app
COPY . /app

RUN pip install --default-timeout=1000 --no-cache-dir -r requirements.txt

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
