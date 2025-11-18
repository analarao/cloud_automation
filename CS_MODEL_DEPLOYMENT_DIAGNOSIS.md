# CS Model Deployment Diagnosis & Fix

## Status Summary
✅ **Helm Chart IS deployed** - Release name: `monitoring-services` in `monitoring` namespace  
❌ **Pods are in CrashLoopBackOff** - 2 pods, 1/2 containers running (Istio sidecar OK, cs-model crashing)  
🔧 **Root Cause Found** - Multiple configuration issues

---

## Issues Found

### Issue 1: Typo in ConfigMap Template
**File:** `info/templates/configmap.yaml` line 13  
**Problem:** `exrtas` should be `extras`

```yaml
# WRONG:
ALERTMANAGER_URL: "http://{{ .Values.services.alertmanager.host }}:{{ .Values.services.alertmanager.port }}{{ .Values.services.alertmanager.exrtas }}"

# CORRECT:
ALERTMANAGER_URL: "http://{{ .Values.services.alertmanager.host }}:{{ .Values.services.alertmanager.port }}{{ .Values.services.alertmanager.extras }}"
```

**Impact:** Alertmanager URL is malformed in the ConfigMap (missing the `/api/v2/alerts` suffix)

---

### Issue 2: Deployment Uses 2 Replicas but cs_model.yaml Only Specifies 1
**File:** `info/cs_model.yaml` and `info/templates/cs_model.yaml`

**Current State:**
- `cs_model.yaml` (root) is **NOT** being used (it's not a template)
- `templates/cs_model.yaml` specifies `replicas: 1` but Helm is creating 2 pods
- Check `info/values.yaml` for any replicas override

**Why 2 pods?** Either:
1. Both `cs_model.yaml` and `templates/cs_model.yaml` are being applied
2. `values.yaml` has `replicas: 2` setting
3. Manually scaled after deployment

---

### Issue 3: Missing PORT Variable in Environment Variables
**Current ConfigMap shows:**
```yaml
PROMETHEUS_URL: http://prometheus-kube-prometheus-prometheus:9090  ✅ CORRECT
GRAFANA_URL: http://prometheus-grafana:3000                       ✅ CORRECT
LOKI_URL: http://loki:3100                                         ✅ CORRECT
ALERTMANAGER_URL: http://prometheus-kube-prometheus-alertmanager:9093  ✅ (Missing /api/v2/alerts)
```

**Missing Environment Variables:**
The CS Model service also needs:
- `PREDICTION_INTERVAL_MIN` (currently hardcoded as 5)
- `PREDICT_METRIC` (currently hardcoded)
- `SLO_THRESHOLD` (currently hardcoded as 0.01)
- `ML_EXPORTER_PORT` (currently hardcoded as 9001)

---

## Why Pods Are CrashLoopBackOff

When cs-model container tries to start:
1. ✅ ConfigMap environment variables ARE injected via `envFrom`
2. ✅ Python dependencies installed correctly
3. ❌ CS Model script tries to initialize `PrometheusConnect` with `PROMETHEUS_URL`
4. ❌ Even though PROMETHEUS_URL is set, the service may not be reachable from the pod

**Possible secondary causes:**
- Istio mTLS blocking traffic (monitoring namespace has injection enabled)
- NetworkPolicy blocking traffic
- Prometheus service unreachable due to DNS or network

---

## Fixes Required

### Fix 1: Correct the Typo in ConfigMap Template
**File:** `info/templates/configmap.yaml`

Change line 13 from:
```yaml
ALERTMANAGER_URL: "http://{{ .Values.services.alertmanager.host }}:{{ .Values.services.alertmanager.port }}{{ .Values.services.alertmanager.exrtas }}"
```

To:
```yaml
ALERTMANAGER_URL: "http://{{ .Values.services.alertmanager.host }}:{{ .Values.services.alertmanager.port }}{{ .Values.services.alertmanager.extras }}"
```

### Fix 2: Delete the Unused Root cs_model.yaml
**File:** `info/cs_model.yaml`

This file is NOT being used by the Helm chart. The actual template is in `templates/cs_model.yaml`.
- Delete `info/cs_model.yaml` to avoid confusion
- The Helm template is correctly using `{{ .Release.Name }}` which produces the correct pod name

### Fix 3: Set Correct Replica Count
Check if you want 1 or 2 replicas:

**Option A: If you want 1 replica (recommended)**
```bash
# Edit values.yaml and add:
replicas: 1
```

Then update the template:
```yaml
# In templates/cs_model.yaml
spec:
  replicas: {{ .Values.replicas | default 1 }}
```

**Option B: If you want 2 replicas for HA**
```bash
# Scale down and check connectivity first
kubectl scale deployment monitoring-services-python-app -n monitoring --replicas=1
```

### Fix 4: Add More Environment Variables to ConfigMap
Update `info/templates/configmap.yaml`:

```yaml
data:
  PROMETHEUS_URL: "http://{{ .Values.services.prometheus.host }}:{{ .Values.services.prometheus.port }}"
  GRAFANA_URL: "http://{{ .Values.services.grafana.host }}:{{ .Values.services.grafana.port }}"
  LOKI_URL: "http://{{ .Values.services.loki.host }}:{{ .Values.services.loki.port }}"
  ALERTMANAGER_URL: "http://{{ .Values.services.alertmanager.host }}:{{ .Values.services.alertmanager.port }}{{ .Values.services.alertmanager.extras }}"
  # Add these:
  PREDICTION_INTERVAL_MIN: "{{ .Values.pythonApp.predictionInterval | default 5 }}"
  PREDICT_METRIC: "{{ .Values.pythonApp.predictMetric | default 'rate(container_cpu_usage_seconds_total{namespace=\"target-services\"}[5m])' }}"
  SLO_THRESHOLD: "{{ .Values.pythonApp.sloThreshold | default 0.01 }}"
  ML_EXPORTER_PORT: "{{ .Values.pythonApp.exporterPort | default 9001 }}"
```

And update `info/values.yaml` to add these fields.

---

## Deployment Steps to Fix

### Step 1: Fix the Typo
```bash
# Edit templates/configmap.yaml
# Change exrtas → extras on line 13
```

### Step 2: Check DNS/Network Connectivity
```bash
# Test if Prometheus is reachable from a pod in monitoring namespace
kubectl run -it --rm network-test \
  --image=curlimages/curl \
  -n monitoring \
  -- curl -v http://prometheus-kube-prometheus-prometheus:9090/-/healthy
```

### Step 3: Helm Upgrade
```bash
helm upgrade monitoring-services . \
  --namespace monitoring \
  -f values.yaml
```

### Step 4: Verify
```bash
# Check pods
kubectl get pods -n monitoring -l app=monitoring-services-python-app

# Check logs
kubectl logs -n monitoring -l app=monitoring-services-python-app -c cs-model --tail=50

# Check ConfigMap
kubectl get configmap monitoring-services-service-urls -n monitoring -o yaml
```

---

## Service URLs Verification Checklist

| Service | Configured URL | Port | Namespace | Status |
|---------|---|---|---|---|
| Prometheus | `prometheus-kube-prometheus-prometheus` | 9090 | monitoring | ✅ Correct |
| Grafana | `prometheus-grafana` | 3000 | monitoring | ✅ Correct |
| Loki | `loki` | 3100 | monitoring | ✅ Correct |
| Alertmanager | `prometheus-kube-prometheus-alertmanager` | 9093 | monitoring | ⚠️ Missing `/api/v2/alerts` suffix |

All services are in the `monitoring` namespace, so the DNS names will auto-resolve within that namespace.

---

## Files to Update

1. `info/templates/configmap.yaml` — Fix typo `exrtas` → `extras`
2. `info/values.yaml` — Add pythonApp configuration options
3. `info/templates/cs_model.yaml` — Use `replicas` from values
4. **DELETE** `info/cs_model.yaml` — Not used by Helm chart
5. `info/cs_model_service.py` — Add environment variable defaults (already has them)

---

## Debugging Commands

```bash
# See what's actually being generated
helm template monitoring-services . -n monitoring -f values.yaml

# Dry-run to see what will be deployed
helm upgrade monitoring-services . --namespace monitoring -f values.yaml --dry-run --debug

# Check pod startup sequence
kubectl get events -n monitoring --sort-by='.lastTimestamp' | tail -20

# Check networking between pods
kubectl exec -it <pod-name> -n monitoring -c cs-model -- ping -c 2 prometheus-kube-prometheus-prometheus
```

---

**Last Updated:** November 18, 2025
