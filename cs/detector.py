from prometheus_client import query_metric
from sklearn.ensemble import IsolationForest
import pandas as pd

def detect_anomalies():
    df = query_metric("node_cpu_seconds_total")
    if df.empty:
        return {"status": "no data"}

    # Simplified: extract numeric values
    values = []
    for r in df["values"]:
        if isinstance(r, list):
            values.extend([float(v[1]) for v in r])

    series = pd.DataFrame(values, columns=["cpu"])
    model = IsolationForest(contamination=0.1, random_state=42)
    series["anomaly"] = model.fit_predict(series[["cpu"]])
    anomalies = series[series["anomaly"] == -1]

    return {"total": len(series), "anomalies": len(anomalies)}
