# cs_model/cs_model_service.py
import os
import time
import json
import requests
import numpy as np
import pandas as pd
from prometheus_api_client import PrometheusConnect, Metric
from datetime import datetime, timedelta
# --- ADDED: Prometheus Client for exposing metrics ---
from prometheus_client import Gauge, start_http_server

# --- Configuration ---
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL")
ALERTMANAGER_URL = os.environ.get("ALERTMANAGER_URL") 
PREDICTION_INTERVAL_MIN = 5 # Run prediction every 5 minutes
PREDICT_METRIC = 'rate(node_cpu_seconds_total{mode="user"}[5m])'
SLO_THRESHOLD = 0.01 # If user CPU usage > 50% in the next 15 min, alert.
# --- ADDED: Port for metric exposure ---
ML_EXPORTER_PORT = 9001 

# --- Components ---
# Initialize prometheus connection object (will connect lazily in run_prediction_cycle)
prom = None

# --- Helper function to initialize Prometheus connection ---
def get_prometheus_client():
    """Initialize and return Prometheus client, with error handling."""
    global prom
    if prom is None:
        try:
            prom = PrometheusConnect(url=PROMETHEUS_URL, disable_ssl=True)
            print(f"✓ Connected to Prometheus at {PROMETHEUS_URL}")
            return prom
        except Exception as e:
            print(f"✗ Failed to connect to Prometheus: {e}")
            return None
    return prom

# --- ADDED: Prometheus Gauges for Exposing Metrics ---
# Gauge 1: The predicted metric value itself
PREDICTED_CPU_GAUGE = Gauge(
    'cs_ml_predicted_cpu_user_rate', 
    'LSTM prediction of the 15m future user CPU rate.',
    # Label the metric by the instance it is predicting for
    ['instance', 'target_metric'] 
)
# Gauge 2: The result of the SLO check (0=OK, 1=Breach)
SLO_STATUS_GAUGE = Gauge(
    'cs_ml_slo_status',
    'Status of the SLO check (1 if predicted CPU > SLO_THRESHOLD, 0 otherwise).',
    ['instance', 'slo_threshold'] 
)


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
    time_diff = historical_data.index[1] - historical_data.index[0]
    if time_diff.total_seconds() == 0:
        future_steps = 0 # Avoid division by zero
    else:
        future_steps = 900 / time_diff.total_seconds()
    
    predicted_value = historical_data.iloc[-1] + (trend * future_steps)
    
    # Ensure prediction is between 0 and 1
    return max(0, min(1, predicted_value))

def generate_and_send_alert(predicted_value: float):
    """
    Generates the Alertmanager V2 payload and sends the alert directly.
    """
    if ALERTMANAGER_URL is None:
        print(f"   (!) Alert generation skipped: ALERTMANAGER_URL not configured")
        return
    
    # ALERTMANAGER V2 API PAYLOAD
    payload = [{
        "labels": {
            "alertname": "PredictedCpuBreach",
            "instance": "ch-application-host",
            "severity": "critical",
            "demo_source": "CS_ML_Direct" # Useful label for debugging/filtering
        },
        "annotations": {
            "summary": f"CS Predicted CPU Breach: {predicted_value:.4f} > {SLO_THRESHOLD}",
            "description": f"Autonomous system detected CPU usage will exceed SLO in 15 minutes based on prediction model."
        },
        "generatorURL": f"http://cs-ml-service/alert_id/{int(time.time())}"
    }]
    
    try:
        response = requests.post(ALERTMANAGER_URL, json=payload)
        print(f"   --> Alert sent to Alertmanager. Status: {response.status_code}")
    except requests.exceptions.ConnectionError as e:
        print(f"   !!! ERROR: Could not connect to Alertmanager API at {ALERTMANAGER_URL}. {e}")

# --- MODIFIED: Added metrics updates to the main loop ---
def run_prediction_cycle():
    """
    The main prediction loop for the CS ML service.
    """
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting CS ML Prediction Cycle...")
    
    # Default values for metrics update
    target_instance = "ch-application-host" 
    prediction_metric_name = "node_cpu_user_rate"
    
    try:
        # Get Prometheus client (lazy initialization)
        prom_client = get_prometheus_client()
        if prom_client is None:
            print("   (i) Prometheus unavailable. Retrying on next cycle.")
            PREDICTED_CPU_GAUGE.labels(instance=target_instance, target_metric=prediction_metric_name).set(0.0)
            SLO_STATUS_GAUGE.labels(instance=target_instance, slo_threshold=SLO_THRESHOLD).set(0)
            return
        
        # Query last 4 hours of data from CR (Prometheus)
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=4)
        
        # Pull data for the 'user' CPU usage
        data = prom_client.custom_query_range(
            query=PREDICT_METRIC,
            start_time=start_time,
            end_time=end_time,
            step="15s"
        )
        
        # Convert PromQL data structure to a Pandas Series for ML processing
        if not data or not data[0].get('values'):
            print("   (i) No data received from Prometheus. Skipping prediction.")
            # Set metric value to 0 if no data is available
            PREDICTED_CPU_GAUGE.labels(instance=target_instance, target_metric=prediction_metric_name).set(0.0)
            SLO_STATUS_GAUGE.labels(instance=target_instance, slo_threshold=SLO_THRESHOLD).set(0)
            return

        # Extract values for the target instance (ch-application-host)
        # Note: A real implementation handles multiple time series/instances
        ts_values = data[0]['values']
        timestamps = [datetime.fromtimestamp(float(v[0])) for v in ts_values]
        values = [float(v[1]) for v in ts_values]
        historical_series = pd.Series(values, index=timestamps)
        
        predicted_value = simulate_lstm_prediction(historical_series)
        
        # --- METRICS UPDATE: Set the predicted CPU value ---
        PREDICTED_CPU_GAUGE.labels(instance=target_instance, target_metric=prediction_metric_name).set(predicted_value)
        
        print(f"   (i) Current User CPU: {historical_series.iloc[-1]:.4f}")
        print(f"   (i) Predicted CPU (15m): {predicted_value:.4f}")
        
        if predicted_value > SLO_THRESHOLD:
            print(f"   !!! PREDICTION BREACH: Predicted value ({predicted_value:.4f}) exceeds SLO ({SLO_THRESHOLD}). Sending alert.")
            generate_and_send_alert(predicted_value)
            # --- METRICS UPDATE: Set SLO Status to 1 (Breach) ---
            SLO_STATUS_GAUGE.labels(instance=target_instance, slo_threshold=SLO_THRESHOLD).set(1)
        else:
            print("   (i) Prediction below SLO. System stable.")
            # --- METRICS UPDATE: Set SLO Status to 0 (OK) ---
            SLO_STATUS_GAUGE.labels(instance=target_instance, slo_threshold=SLO_THRESHOLD).set(0)
            
    except Exception as e:
        print(f"   !!! CRITICAL ERROR in CS ML Cycle: {e}")
        # Optionally set a gauge for the service health error here

if __name__ == "__main__":
    try:
        print("=" * 80)
        print("CS ML Service starting...")
        print(f"PROMETHEUS_URL: {PROMETHEUS_URL}")
        print(f"ALERTMANAGER_URL: {ALERTMANAGER_URL}")
        print(f"PREDICTION_INTERVAL_MIN: {PREDICTION_INTERVAL_MIN}")
        print(f"ML_EXPORTER_PORT: {ML_EXPORTER_PORT}")
        print("=" * 80)
        
        # --- CRITICAL FIX: Start the Prometheus HTTP server in the background ---
        # Use addr='0.0.0.0' to ensure it's accessible from other containers (like Prometheus)
        print("Starting HTTP server for metrics exposure...")
        start_http_server(ML_EXPORTER_PORT, addr='0.0.0.0')
        print(f"✓ Metrics exposed on 0.0.0.0:{ML_EXPORTER_PORT}")
        print(f"✓ CS ML Service started successfully. Entering prediction cycle...")
        print("=" * 80)
        
        while True:
            run_prediction_cycle()
            time.sleep(PREDICTION_INTERVAL_MIN * 60)
    except Exception as e:
        print(f"FATAL ERROR in __main__: {e}")
        import traceback
        traceback.print_exc()
        raise
