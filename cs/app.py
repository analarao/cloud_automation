from fastapi import FastAPI
from detector import detect_anomalies
from predictor import predict_future

app = FastAPI(title="Container-Spine (CS)")

@app.get("/analyze")
def analyze():
    anomalies = detect_anomalies()
    prediction = predict_future()
    return {"anomalies": anomalies, "prediction": prediction}
