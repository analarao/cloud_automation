import requests
import pandas as pd
from datetime import datetime, timedelta
import os

PROM_URL = os.getenv("PROM_URL", "http://localhost:9090")

def query_metric(metric_name: str, duration_minutes=5):
    """Fetch recent metric samples from Prometheus."""
    query = f"{metric_name}[{duration_minutes}m]"
    resp = requests.get(f"{PROM_URL}/api/v1/query", params={"query": query})
    data = resp.json().get("data", {}).get("result", [])
    df = pd.DataFrame(data)
    return df
