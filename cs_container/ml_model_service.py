# cs_container/ml_model_service.py
import os
import time
import json
import requests
import numpy as np
import pandas as pd
from prometheus_api_client import PrometheusConnect, Metric
from datetime import datetime, timedelta

# --- Configuration ---
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090")
CB_API_URL = os.environ.get("CB_API_URL", "http://cb-mock-api:5001/api/v1/alert")
PREDICTION_INTERVAL_MIN = 5 # Run prediction every 5 minutes
PREDICT_METRIC = 'rate(node_cpu_seconds_total{mode="user"}[5m])'
SLO_THRESHOLD = 0.50 # If user CPU usage > 50% in the next 15 min, alert.

# --- Components ---
prom = PrometheusConnect(url=PROMETHEUS_URL, disable_ssl=True)

def simulate_lstm_prediction(historical_data: pd.Series) -> float:
    """
    Simulates a time-series forecast (e.g., an LSTM model) on metric data.
    
    In a real system, this would involve loading a pre-trained Keras/PyTorch model.
    Here, we use a simple linear regression to mock a forecast.
    """
    if len(historical_data) < 10:
        return 0.0 # Not enough data
    
    # Mock prediction: Assume future value is the current value + a linear trend component
    # This simulates a memory leak or continuous CPU growth
    
    # Calculate trend over the last hour
    trend = (historical_data.iloc[-1] - historical_data.iloc[0]) / len(historical_data)
    
    # Predict value 15 minutes (900 seconds) in the future
    # Using a simple next-step model for demonstration
    future_steps = 900 / (historical_data.index[1] - historical_data.index[0]).total_seconds()
    
    predicted_value = historical_data.iloc[-1] + (trend * future_steps)
    
    # Ensure prediction is between 0 and 1
    return max(0, min(1, predicted_value))

def generate_and_send_alert(predicted_value: float):
    """
    Generates the structured CS payload and sends it to the CB API.
    """
    payload = {
      "alert_id": f"CS-ML-{int(time.time())}",
      "source": "CS-ML-LSTM",
      "triggers": [
        {
          "type": "SLO_PREDICTION",
          "metric": PREDICT_METRIC,
          "service": "ch-application-host",
          "endpoint": "N/A",
          "predicted_value": f"{predicted_value:.4f}",
          "threshold": f"Predicted CPU usage breach > {SLO_THRESHOLD*100}% in 15 minutes."
        }
      ]
    }
    
    try:
        response = requests.post(CB_API_URL, json=payload)
        print(f"   --> Alert sent to CB. Status: {response.status_code}")
    except requests.exceptions.ConnectionError as e:
        print(f"   !!! ERROR: Could not connect to CB API at {CB_API_URL}. {e}")


def run_prediction_cycle():
    """
    The main prediction loop for the CS ML service.
    """
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting CS ML Prediction Cycle...")
    
    try:
        # Query last 4 hours of data from CR (Prometheus)
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=4)
        
        # Pull data for the 'user' CPU usage
        data = prom.custom_query_range(
            query=PREDICT_METRIC,
            start_time=start_time,
            end_time=end_time,
            step="15s"
        )
        
        # Convert PromQL data structure to a Pandas Series for ML processing
        if not data or not data[0].get('values'):
            print("   (i) No data received from Prometheus. Skipping prediction.")
            return

        # Extract values for the target instance (ch-application-host)
        # Note: A real implementation handles multiple time series/instances
        ts_values = data[0]['values']
        timestamps = [datetime.fromtimestamp(float(v[0])) for v in ts_values]
        values = [float(v[1]) for v in ts_values]
        historical_series = pd.Series(values, index=timestamps)
        
        predicted_value = simulate_lstm_prediction(historical_series)
        
        print(f"   (i) Current User CPU: {historical_series.iloc[-1]:.4f}")
        print(f"   (i) Predicted CPU (15m): {predicted_value:.4f}")
        
        if predicted_value > SLO_THRESHOLD:
            print(f"   !!! PREDICTION BREACH: Predicted value ({predicted_value:.4f}) exceeds SLO ({SLO_THRESHOLD}). Sending alert.")
            generate_and_send_alert(predicted_value)
        else:
            print("   (i) Prediction below SLO. System stable.")
            
    except Exception as e:
        print(f"   !!! CRITICAL ERROR in CS ML Cycle: {e}")

if __name__ == "__main__":
    print(f"CS ML Service started. Querying Prometheus at {PROMETHEUS_URL}")
    while True:
        run_prediction_cycle()
        time.sleep(PREDICTION_INTERVAL_MIN * 60)