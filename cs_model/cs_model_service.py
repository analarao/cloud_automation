# cs_model/cs_model_service.py
import os
import time
import json
import logging
import requests
import numpy as np
import pandas as pd
from prometheus_api_client import PrometheusConnect, Metric
from datetime import datetime, timedelta
# --- ADDED: Prometheus Client for exposing metrics ---
from prometheus_client import Gauge, start_http_server

# --- Configure logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# --- Configuration ---
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL")
ALERTMANAGER_URL = os.environ.get("ALERTMANAGER_URL") 
PREDICTION_INTERVAL_MIN = 1 # Run prediction every 5 minutes
TARGET_NAMESPACE = os.environ.get("TARGET_NAMESPACE", "target-services")  # Namespace to monitor

# Metric query for container CPU usage (verified working with your Prometheus setup)
# This queries cAdvisor metrics with cpu="total" label for all pods in target namespace
PREDICT_METRIC = f'rate(container_cpu_usage_seconds_total{{namespace="{TARGET_NAMESPACE}", cpu="total", pod!=""}}[5m])'

SLO_THRESHOLD = 0.01 # CPU threshold for breach detection
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
            logger.info(f"Attempting to connect to Prometheus at: {PROMETHEUS_URL}")
            prom = PrometheusConnect(url=PROMETHEUS_URL, disable_ssl=True)
            logger.info(f"✓ Successfully connected to Prometheus at {PROMETHEUS_URL}")
            return prom
        except Exception as e:
            logger.error(f"✗ Failed to connect to Prometheus at {PROMETHEUS_URL}: {e}")
            return None
    return prom

# --- ADDED: Prometheus Gauges for Exposing Metrics ---
# Gauge 1: The predicted metric value itself
# Using target_* labels to avoid conflicts with Prometheus scraping labels
PREDICTED_CPU_GAUGE = Gauge(
    'cs_ml_predicted_cpu_user_rate', 
    'LSTM prediction of the 15m future container CPU rate.',
    # Label the metric by target pod info (not the exporter pod)
    ['target_pod', 'target_namespace', 'target_container', 'target_app'] 
)
# Gauge 2: The result of the SLO check (0=OK, 1=Breach)
SLO_STATUS_GAUGE = Gauge(
    'cs_ml_slo_status',
    'Status of the SLO check (1 if predicted CPU > SLO_THRESHOLD, 0 otherwise).',
    ['target_pod', 'target_namespace', 'target_container', 'target_app'] 
)


def simulate_lstm_prediction(historical_data: pd.Series) -> float:
    """
    Simulates a time-series forecast (e.g., an LSTM model) on metric data.
    
    In a real system, this would involve loading a pre-trained Keras/PyTorch model.
    Here, we use a simple linear regression to mock a forecast.
    """
    if len(historical_data) < 10:
        logger.warning(f"Insufficient historical data for prediction: {len(historical_data)} points (need 10+)")
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

def get_service_topology(service_name: str) -> dict:
    """
    Queries Prometheus for upstream and downstream dependencies using Istio metrics.
    Returns a dictionary with topology information.
    """
    topology = {
        "focal_service": service_name,
        "upstream_services": [],
        "downstream_services": [],
        "edges": []
    }
    
    try:
        prom_client = get_prometheus_client()
        if prom_client is None:
            logger.warning(f"Cannot fetch topology for {service_name}: Prometheus unavailable")
            return topology
        
        logger.info(f"Fetching service topology for: {service_name}")
        
        # Query upstreams: who calls this service
        upstream_query = f'sum(rate(istio_requests_total{{destination_app="{service_name}"}}[5m])) by (source_app, destination_app)'
        logger.debug(f"Upstream query: {upstream_query}")
        upstream_result = prom_client.custom_query(upstream_query)
        
        # Query downstreams: whom this service calls
        downstream_query = f'sum(rate(istio_requests_total{{source_app="{service_name}"}}[5m])) by (source_app, destination_app)'
        logger.debug(f"Downstream query: {downstream_query}")
        downstream_result = prom_client.custom_query(downstream_query)
        
        # Process upstream results
        upstream_services = set()
        for result in upstream_result:
            labels = result.get('metric', {})
            source = labels.get('source_app')
            dest = labels.get('destination_app')
            value = float(result.get('value', [0, 0])[1])
            
            if source and source != service_name:
                upstream_services.add(source)
                topology['edges'].append({
                    'source': source,
                    'destination': dest,
                    'rate_rps': round(value, 3)
                })
        
        # Process downstream results
        downstream_services = set()
        for result in downstream_result:
            labels = result.get('metric', {})
            source = labels.get('source_app')
            dest = labels.get('destination_app')
            value = float(result.get('value', [0, 0])[1])
            
            if dest and dest != service_name:
                downstream_services.add(dest)
                topology['edges'].append({
                    'source': source,
                    'destination': dest,
                    'rate_rps': round(value, 3)
                })
        
        topology['upstream_services'] = list(upstream_services)
        topology['downstream_services'] = list(downstream_services)
        
        logger.info(f"Topology for {service_name}: Upstreams={len(upstream_services)}, Downstreams={len(downstream_services)}")
        logger.debug(f"Full topology: {json.dumps(topology, indent=2)}")
        
    except Exception as e:
        logger.error(f"Error fetching topology for {service_name}: {e}")
        logger.exception("Full traceback:")
    
    return topology

def generate_and_send_alert(predicted_value: float, pod_info: dict, topology: dict):
    """
    Generates the Alertmanager V2 payload and sends the alert directly.
    Includes pod information and service topology (upstream/downstream dependencies).
    
    NOTE: This service uses a DUAL ALERT MECHANISM:
    1. Direct Path: Sends enriched alerts to Alertmanager (THIS FUNCTION)
       - Includes full service topology (upstream/downstream dependencies)
       - Immediate alert with rich context for autonomous remediation
    
    2. Prometheus Path: Exposes metrics that Prometheus scrapes
       - Metric: cs_ml_predicted_cpu_user_rate (set via PREDICTED_CPU_GAUGE)
       - Prometheus evaluates alert rule: PredictedCpuBreach
       - Fires alert when metric > 0.01 for 1 minute
       - Provides observability and historical tracking
    
    Both paths produce alerts with matching labels for Alertmanager deduplication.
    
    Args:
        predicted_value: The predicted CPU value
        pod_info: Dictionary with pod, namespace, container, app labels
        topology: Dictionary with upstream/downstream service dependencies
    """
    if ALERTMANAGER_URL is None:
        logger.warning("Alert generation skipped: ALERTMANAGER_URL not configured")
        return
    
    logger.info(f"Generating alert for predicted CPU breach: {predicted_value:.4f} > {SLO_THRESHOLD}")
    logger.info(f"Pod: {pod_info.get('pod')}, Namespace: {pod_info.get('namespace')}, Service: {pod_info.get('app')}")
    
    # Serialize topology to JSON string for inclusion in alert
    topology_json = json.dumps(topology, indent=2)
    
    # ALERTMANAGER V2 API PAYLOAD with enriched pod and topology information
    payload = [{
        "labels": {
            "alertname": "PredictedCpuBreach",
            "pod": pod_info.get('pod', 'unknown'),
            "namespace": pod_info.get('namespace', TARGET_NAMESPACE),
            "container": pod_info.get('container', 'unknown'),
            "app": pod_info.get('app', 'unknown'),
            "severity": "critical",
            "alert_type": "SLO_PREDICTION",
            "source": "CS_ML_Service"
        },
        "annotations": {
            "summary": f"Predicted CPU breach for pod {pod_info.get('pod')} in namespace {pod_info.get('namespace')}",
            "description": f"The CS ML service predicts CPU usage will exceed SLO threshold ({SLO_THRESHOLD}) within 15 minutes. Current predicted value: {predicted_value:.4f}",
            "predicted_value": str(predicted_value),
            "threshold": str(SLO_THRESHOLD),
            "service_topology": topology_json,
            "upstream_services": ", ".join(topology.get('upstream_services', [])),
            "downstream_services": ", ".join(topology.get('downstream_services', []))
        },
        "generatorURL": f"http://cs-ml-service/alert/{pod_info.get('pod')}/{int(time.time())}"
    }]
    
    try:
        logger.info(f"Sending alert to Alertmanager at: {ALERTMANAGER_URL}")
        logger.debug(f"Alert payload: {json.dumps(payload, indent=2)}")
        response = requests.post(ALERTMANAGER_URL, json=payload)
        logger.info(f"✓ Alert sent to Alertmanager. Status: {response.status_code}, Response: {response.text[:200]}")
    except requests.exceptions.ConnectionError as e:
        logger.error(f"✗ Could not connect to Alertmanager API at {ALERTMANAGER_URL}: {e}")
    except Exception as e:
        logger.error(f"✗ Unexpected error sending alert to Alertmanager: {e}")

# --- MODIFIED: Added metrics updates to the main loop ---
def run_prediction_cycle():
    """
    The main prediction loop for the CS ML service.
    Monitors all pods in the target namespace and predicts CPU breaches.
    """
    logger.info("=" * 80)
    logger.info("Starting CS ML Prediction Cycle")
    logger.info("=" * 80)
    
    logger.info(f"Target namespace: {TARGET_NAMESPACE}")
    logger.info(f"Prediction metric: {PREDICT_METRIC}")
    logger.info(f"SLO threshold: {SLO_THRESHOLD}")
    
    try:
        # Get Prometheus client (lazy initialization)
        prom_client = get_prometheus_client()
        if prom_client is None:
            logger.warning("Prometheus client unavailable. Retrying on next cycle.")
            return
        
        # Query last 4 hours of data from CR (Prometheus) for all pods in target namespace
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=4)
        
        # Query the verified working metric
        logger.info(f"Querying metric: {PREDICT_METRIC}")
        logger.info(f"Time range: {start_time.strftime('%Y-%m-%d %H:%M:%S')} to {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        try:
            data = prom_client.custom_query_range(
                query=PREDICT_METRIC,
                start_time=start_time,
                end_time=end_time,
                step="15s"
            )
            
            if not data or len(data) == 0:
                logger.warning("Query returned no data. Possible reasons:")
                logger.warning("  1. No pods running in target-services namespace")
                logger.warning("  2. Pods haven't generated enough CPU metrics yet")
                logger.warning("  3. cAdvisor metrics not available")
                return
            
            logger.info(f"✓ Successfully retrieved {len(data)} time series from Prometheus")
            
        except Exception as e:
            logger.error(f"✗ Failed to query Prometheus: {e}")
            logger.exception("Full error:")
            return
        
        # Process each time series (each represents a different pod/container)
        pods_processed = 0
        breaches_detected = 0
        
        for ts_data in data:
            metric_labels = {}
            try:
                metric_labels = ts_data.get('metric', {})
                ts_values = ts_data.get('values', [])
                
                if not ts_values:
                    continue
                
                # Extract pod information from metric labels
                pod_info = {
                    'pod': metric_labels.get('pod', 'unknown'),
                    'namespace': metric_labels.get('namespace', TARGET_NAMESPACE),
                    'container': metric_labels.get('container', 'unknown'),
                    'app': metric_labels.get('app', metric_labels.get('pod', 'unknown').split('-')[0] if '-' in metric_labels.get('pod', '') else 'unknown')
                }
                
                logger.info(f"Processing pod: {pod_info['pod']}, container: {pod_info['container']}")
                
                # Convert to Pandas Series for ML processing
                timestamps = [datetime.fromtimestamp(float(v[0])) for v in ts_values]
                values = [float(v[1]) for v in ts_values]
                historical_series = pd.Series(values, index=timestamps)
                
                current_cpu = historical_series.iloc[-1]
                logger.info(f"  Current CPU: {current_cpu:.4f}")
                
                # Run prediction model
                predicted_value = simulate_lstm_prediction(historical_series)
                logger.info(f"  Predicted CPU (15m): {predicted_value:.4f}")
                
                # Update metrics with target_* labels to avoid Prometheus scraping label conflicts
                PREDICTED_CPU_GAUGE.labels(
                    target_pod=pod_info['pod'],
                    target_namespace=pod_info['namespace'],
                    target_container=pod_info['container'],
                    target_app=pod_info['app']
                ).set(predicted_value)
                
                # Check for SLO breach
                if predicted_value > SLO_THRESHOLD:
                    logger.warning(f"  !!! PREDICTION BREACH for pod {pod_info['pod']} !!!")
                    logger.warning(f"  Predicted: {predicted_value:.4f} > Threshold: {SLO_THRESHOLD}")
                    
                    # Fetch service topology (upstream/downstream dependencies)
                    service_name = pod_info['app']
                    logger.info(f"  Fetching topology for service: {service_name}")
                    topology = get_service_topology(service_name)
                    
                    # Send enriched alert to Alertmanager
                    logger.info(f"  Sending enriched alert to Alertmanager...")
                    generate_and_send_alert(predicted_value, pod_info, topology)
                    
                    # Update SLO status gauge
                    SLO_STATUS_GAUGE.labels(
                        target_pod=pod_info['pod'],
                        target_namespace=pod_info['namespace'],
                        target_container=pod_info['container'],
                        target_app=pod_info['app']
                    ).set(1)
                    
                    breaches_detected += 1
                else:
                    logger.info(f"  ✓ Pod {pod_info['pod']}: Prediction below threshold")
                    SLO_STATUS_GAUGE.labels(
                        target_pod=pod_info['pod'],
                        target_namespace=pod_info['namespace'],
                        target_container=pod_info['container'],
                        target_app=pod_info['app']
                    ).set(0)
                
                pods_processed += 1
                
            except Exception as e:
                logger.error(f"Error processing time series for pod {metric_labels.get('pod', 'unknown')}: {e}")
                continue
        
        logger.info("=" * 80)
        logger.info(f"Prediction cycle complete: {pods_processed} pods processed, {breaches_detected} breaches detected")
        logger.info(f"Metrics exposed at http://0.0.0.0:{ML_EXPORTER_PORT}/metrics")
        logger.info("=" * 80)
            
    except Exception as e:
        logger.error(f"!!! CRITICAL ERROR in CS ML Cycle: {e}")
        logger.exception("Full traceback:")

if __name__ == "__main__":
    try:
        logger.info("=" * 80)
        logger.info("CS ML SERVICE STARTING - MULTI-POD PREDICTOR")
        logger.info("=" * 80)
        logger.info(f"Configuration:")
        logger.info(f"  PROMETHEUS_URL: {PROMETHEUS_URL}")
        logger.info(f"  ALERTMANAGER_URL: {ALERTMANAGER_URL}")
        logger.info(f"  TARGET_NAMESPACE: {TARGET_NAMESPACE}")
        logger.info(f"  PREDICTION_INTERVAL_MIN: {PREDICTION_INTERVAL_MIN}")
        logger.info(f"  ML_EXPORTER_PORT: {ML_EXPORTER_PORT}")
        logger.info(f"  PREDICT_METRIC: {PREDICT_METRIC}")
        logger.info(f"  SLO_THRESHOLD: {SLO_THRESHOLD}")
        logger.info("=" * 80)
        logger.info("Features:")
        logger.info("  • Monitors all pods in target-services namespace")
        logger.info("  • Predicts CPU breach 15 minutes in advance")
        logger.info("  • Enriches alerts with Istio service topology")
        logger.info("  • Includes upstream/downstream dependencies")
        logger.info("=" * 80)
        
        # --- CRITICAL FIX: Start the Prometheus HTTP server in the background ---
        # Use addr='0.0.0.0' to ensure it's accessible from other containers (like Prometheus)
        logger.info("Starting HTTP server for metrics exposure...")
        start_http_server(ML_EXPORTER_PORT, addr='0.0.0.0')
        logger.info(f"✓ Metrics HTTP server started successfully")
        logger.info(f"✓ Metrics accessible at: http://0.0.0.0:{ML_EXPORTER_PORT}/metrics")
        logger.info(f"✓ CS ML Service initialization complete. Entering prediction cycle...")
        logger.info(f"✓ Prediction cycle interval: {PREDICTION_INTERVAL_MIN} minutes")
        logger.info("=" * 80)
        
        while True:
            run_prediction_cycle()
            logger.info(f"Sleeping for {PREDICTION_INTERVAL_MIN} minutes until next cycle...")
            time.sleep(PREDICTION_INTERVAL_MIN * 60)
    except KeyboardInterrupt:
        logger.info("Received shutdown signal (Ctrl+C). Exiting gracefully...")
    except Exception as e:
        logger.critical(f"FATAL ERROR in __main__: {e}")
        logger.exception("Full traceback:")
        raise
