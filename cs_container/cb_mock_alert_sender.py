# cs_container/cb_mock_alert_sender.py
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route('/api/v1/alert', methods=['POST'])
def receive_alert():
    """
    Simulates the CB (Container-Brain) API endpoint receiving a structured alert.
    This endpoint is used by both the PromQL rules (via Alertmanager) and the
    CS ML Service.
    """
    try:
        alert_data = request.json
        
        # Determine the source (PromQL Alerts from Alertmanager, or direct from ML Service)
        source = alert_data.get('source', 'Alertmanager (PromQL)')

        print("\n--- CB (Container-Brain) Triggered ---")
        print(f"Source: {source}")
        
        # If from PromQL/Alertmanager, process the list of alerts
        if 'Alertmanager' in source:
            firing_alerts = [a for a in alert_data.get('alerts', []) if a['status'] == 'firing']
            print(f"Received {len(firing_alerts)} FIRING PromQL alerts.")
            # For brevity, we just print the content for PromQL alerts
            final_payload = alert_data
            
        # If directly from the CS ML Service, it's already the final format
        else:
            print("Received direct payload from CS ML Service.")
            final_payload = alert_data

        print("\n[CS Payload for CB RAG]")
        print(jsonify(final_payload).get_data(as_text=True))
        
        # Acknowledge the receipt
        print("---------------------------------------")
        return jsonify({"status": "Alert received and CB diagnosis initiated"}), 200

    except Exception as e:
        print(f"Error processing alert: {e}")
        return jsonify({"status": "Error", "message": str(e)}), 500

if __name__ == '__main__':
    # Run the mock CB on the internal network port 5001
    app.run(host='0.0.0.0', port=5001, debug=False)