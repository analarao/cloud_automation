from fastapi import FastAPI
from detector import detect_anomalies
from predictor import predict_future
from prometheus_client import start_http_server, Counter, Gauge
import threading
import time

app = FastAPI(title="Container-Spine (CS)")

# Prometheus metrics
anomalies_counter = Counter('cs_anomalies_total', 'Total anomalies detected')
prediction_gauge = Gauge('cs_prediction_value', 'Predicted future metric value')

# Start Prometheus metrics server on port 8001 in a separate thread
def start_metrics_server():
    start_http_server(8001)

threading.Thread(target=start_metrics_server, daemon=True).start()


@app.get("/analyze")
def analyze():
    anomalies = detect_anomalies()
    prediction = predict_future()

    # Update metrics
    anomalies_counter.inc(len(anomalies))
    prediction_gauge.set(prediction if isinstance(prediction, (int, float)) else 0)

    return {"anomalies": anomalies, "prediction": prediction}
