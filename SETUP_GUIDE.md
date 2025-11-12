# Cloud Automation K8s Setup Guide

Complete guide for deploying and managing Istio, Prometheus, Grafana, Loki, Vector, Kiali, and Bookinfo on Kubernetes.

---

## Table of Contents

1. [Prerequisites & Initial Setup](#prerequisites--initial-setup)
2. [Namespace & Cluster Configuration](#namespace--cluster-configuration)
3. [Service Installation & Configuration](#service-installation--configuration)
4. [Port Forwarding & Access](#port-forwarding--access)
5. [Restart Instructions](#restart-instructions)
6. [Complete Uninstall](#complete-uninstall)

---

## Prerequisites & Initial Setup

### Install Required Tools

#### Helm (Package Manager for Kubernetes)
```bash
sudo dnf install helm
```

#### Istio Control Plane (istioctl)
```bash
# Download and extract Istio
curl -L https://istio.io/downloadIstio | sh -

# Make istioctl available in PATH (adjust version as needed)
export PATH=$PWD/istio-1.xx.x/bin:$PATH
```

### Add Helm Repositories
```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo add vector https://helm.vector.dev
helm repo add kiali https://kiali.org/helm-charts

# Fetch latest chart metadata
helm repo update
```

---

## Namespace & Cluster Configuration

### Create Required Namespaces
```bash
kubectl create namespace target-services
kubectl create namespace monitoring
kubectl create namespace istio-system  # Created automatically by istioctl
```

### Enable Istio Sidecar Injection

#### Enable on target-services (for Bookinfo)
```bash
kubectl label namespace target-services istio-injection=enabled
```

#### Enable on monitoring (for Grafana, Prometheus, etc. to communicate via mTLS)
```bash
kubectl label namespace monitoring istio-injection=enabled
```

### Install Istio Control Plane

Use the `demo` profile (includes all components; for production use a lighter profile):
```bash
istioctl install --set profile=demo -y
```

**Verify Installation:**
```bash
kubectl get pods -n istio-system
# Should see istiod, ingress-gateway, egress-gateway pods
```

---

## Service Installation & Configuration

### 1. Bookinfo Application (Target Service)

#### Deploy Bookinfo Services
```bash
# Apply the core Bookinfo microservices
kubectl apply -f ./istio-1.xx.x/samples/bookinfo/platform/kube/bookinfo.yaml -n target-services

# Apply Istio Gateway & VirtualServices for external traffic
kubectl apply -f ./istio-1.xx.x/samples/bookinfo/networking/bookinfo-gateway.yaml -n target-services
```

**Verify:**
```bash
kubectl get pods -n target-services
kubectl get vs,gw -n target-services
```

---

### 2. Prometheus & Prometheus Operator

#### Install kube-prometheus-stack (Prometheus + Operator)

This installs Prometheus, Grafana (basic), and the Prometheus Operator for ServiceMonitor management.

```bash
helm install prometheus prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
  --set prometheus.prometheusSpec.podMonitorSelectorNilUsesHelmValues=false
```

**Key Flags:**
- `serviceMonitorSelectorNilUsesHelmValues=false` — Prometheus discovers ALL ServiceMonitors, not just Helm-labeled ones
- `podMonitorSelectorNilUsesHelmValues=false` — Same for PodMonitors

#### Apply Istio Telemetry Collection
```bash
# Apply Prometheus operator config provided by Istio for metrics collection
kubectl apply -f ./istio-1.xx.x/samples/addons/extras/prometheus-operator.yaml
```

**Verify:**
```bash
kubectl get prometheus -n monitoring
kubectl get servicemonitor -n monitoring
```

---

### 3. Grafana (Dashboards & Visualization)

> **Note:** Grafana is installed as part of `kube-prometheus-stack` but can be replaced/upgraded with a standalone Helm chart if needed.

#### Get Grafana Admin Password
```bash
kubectl get secret --namespace monitoring prometheus-grafana \
  -o jsonpath="{.data.admin-password}" | base64 --decode && echo
```

#### Access Grafana (via port-forward)
```bash
kubectl port-forward --namespace monitoring svc/prometheus-grafana 8080:80
# Open browser: http://localhost:8080
# Login with username: admin, password: <from above>
```

#### Enable Embedding (for Kiali integration)
The `kube-prometheus-stack` Helm chart already sets `allow_embedding: true` by default.

To verify:
```bash
kubectl get cm -n monitoring prometheus-grafana -o yaml | grep allow_embedding
```

---

### 4. Loki (Log Aggregation)

#### Install Loki
```bash
helm install loki grafana/loki \
  --namespace monitoring \
  -f loki-single.yaml
```

**Verify:**
```bash
kubectl get pods -n monitoring -l app=loki
kubectl get svc -n monitoring loki
```

#### Upgrade Loki Configuration
```bash
helm upgrade loki grafana/loki \
  --namespace monitoring \
  -f loki-single.yaml
```

#### Access Loki (via port-forward)
```bash
kubectl port-forward -n monitoring svc/loki 3100:3100
# Loki API available at: http://localhost:3100
```

---

### 5. Vector (Log Collection Agent)

Vector collects logs from all containers and ships them to Loki.

#### Install Vector
```bash
helm install vector vector/vector \
  --namespace monitoring \
  -f vector-values.yaml
```

**Note:** Vector is deployed as a **DaemonSet** (one pod per node) with the "Agent" role.

**Verify:**
```bash
kubectl get daemonset -n monitoring vector
kubectl get pods -n monitoring -l app.kubernetes.io/name=vector
```

#### Upgrade Vector Configuration
```bash
helm upgrade vector vector/vector \
  --namespace monitoring \
  -f vector-values.yaml
```

#### Check Vector Logs
```bash
# View logs from Vector DaemonSet
kubectl logs -n monitoring -l app.kubernetes.io/name=vector --tail=50
```

---

### 6. Kiali (Service Mesh Visualization)

Kiali provides a visual UI for managing and monitoring the Istio service mesh, with integration to Prometheus, Grafana, and Jaeger.

#### Install Kiali
```bash
helm install kiali kiali/kiali-server \
  --namespace istio-system \
  -f kiali-values.yaml
```

**Verify:**
```bash
kubectl get pods -n istio-system -l app=kiali
```

#### Upgrade Kiali Configuration
```bash
helm upgrade kiali kiali/kiali-server \
  --namespace istio-system \
  -f kiali-values.yaml
```

#### Launch Kiali Dashboard
```bash
# Opens Kiali UI at http://localhost:20001/kiali
istioctl dashboard kiali
```

#### Get Kiali Login Token
```bash
kubectl create token kiali -n istio-system
# Copy the token and paste it into the Kiali login page
```

---

## Port Forwarding & Access

### Enable External Access (Minikube)

If using Minikube, enable external load balancer access:
```bash
minikube tunnel
```

This command runs in the foreground. Open a new terminal for the commands below.

### Quick Reference Table

| Service | Port-Forward Command | Browser URL | Default Login |
|---------|----------------------|-------------|----------------|
| **Grafana** | `kubectl port-forward -n monitoring svc/prometheus-grafana 8080:80` | http://localhost:8080 | admin / *[see secret]* |
| **Prometheus** | `kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090` | http://localhost:9090 | N/A (no auth) |
| **Loki** | `kubectl port-forward -n monitoring svc/loki 3100:3100` | http://localhost:3100/loki/api/v1/query | N/A (API only) |
| **Kiali** | `istioctl dashboard kiali` | http://localhost:20001/kiali | *[bearer token]* |
| **Bookinfo** | `kubectl port-forward -n target-services svc/productpage 9080:9080` | http://localhost:9080/productpage | N/A |

### Multi-Service Port Forwarding Setup

To run multiple port-forwards simultaneously, open separate terminal tabs:

```bash
# Terminal 1: Grafana
kubectl port-forward --namespace monitoring svc/prometheus-grafana 8080:80

# Terminal 2: Prometheus
kubectl port-forward --namespace monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090

# Terminal 3: Loki
kubectl port-forward -n monitoring svc/loki 3100:3100

# Terminal 4: Bookinfo (optional)
kubectl port-forward -n target-services svc/productpage 9080:9080
```

Or use background processes:

```bash
# Run all port-forwards in background
kubectl port-forward --namespace monitoring svc/prometheus-grafana 8080:80 &
kubectl port-forward --namespace monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090 &
kubectl port-forward -n monitoring svc/loki 3100:3100 &
istioctl dashboard kiali &  # Kiali also runs in background
```

---

## Restart Instructions

Use these commands to gracefully stop and restart all services **without uninstalling**. This preserves your configurations and data.

### Stop All Services (Keep Deployments Intact)

#### 1. Scale Down Kiali
```bash
kubectl scale deployment kiali -n istio-system --replicas=0
```

#### 2. Scale Down Vector
```bash
kubectl scale daemonset vector -n monitoring --replicas=0
```

#### 3. Scale Down Loki
```bash
kubectl scale deployment loki -n monitoring --replicas=0
```

#### 4. Scale Down Prometheus
```bash
kubectl scale deployment prometheus-kube-prometheus-prometheus -n monitoring --replicas=0
```

#### 5. Scale Down Grafana
```bash
kubectl scale deployment prometheus-grafana -n monitoring --replicas=0
```

#### 6. Stop Bookinfo Services
```bash
kubectl scale deployment productpage -n target-services --replicas=0
kubectl scale deployment reviews -n target-services --replicas=0
kubectl scale deployment ratings -n target-services --replicas=0
kubectl scale deployment details -n target-services --replicas=0
```

### Restart All Services

#### 1. Start Bookinfo Services
```bash
kubectl scale deployment productpage -n target-services --replicas=1
kubectl scale deployment reviews -n target-services --replicas=1
kubectl scale deployment ratings -n target-services --replicas=1
kubectl scale deployment details -n target-services --replicas=1
```

#### 2. Restart Grafana
```bash
kubectl scale deployment prometheus-grafana -n monitoring --replicas=1
```

#### 3. Restart Prometheus
```bash
kubectl scale deployment prometheus-kube-prometheus-prometheus -n monitoring --replicas=1
```

#### 4. Restart Loki
```bash
kubectl scale deployment loki -n monitoring --replicas=1
```

#### 5. Restart Vector
```bash
kubectl scale daemonset vector -n monitoring --replicas=1
```

#### 6. Restart Kiali
```bash
kubectl scale deployment kiali -n istio-system --replicas=1
```

### Verify Services Are Running

```bash
# Check all namespaces
kubectl get pods -n target-services
kubectl get pods -n monitoring
kubectl get pods -n istio-system

# All pods should show READY status
```

### Quick Restart (All at Once)

If you want to restart everything in one go:

```bash
# Stop all
kubectl scale deployment -n target-services --all --replicas=0
kubectl scale deployment -n monitoring --all --replicas=0
kubectl scale daemonset -n monitoring --all --replicas=0

# Wait for pods to terminate
sleep 10

# Restart all
kubectl scale deployment -n target-services --all --replicas=1
kubectl scale deployment -n monitoring --all --replicas=1
kubectl scale daemonset -n monitoring --all --replicas=1
```

---

## Complete Uninstall

### Remove All Services (in reverse installation order)

#### 1. Uninstall Kiali
```bash
helm uninstall kiali --namespace istio-system
```

#### 2. Uninstall Vector
```bash
helm uninstall vector --namespace monitoring
```

#### 3. Uninstall Loki
```bash
helm uninstall loki --namespace monitoring
```

#### 4. Uninstall Prometheus Stack
```bash
helm uninstall prometheus --namespace monitoring
```

#### 5. Remove Bookinfo Applications
```bash
kubectl delete -f ./istio-1.xx.x/samples/bookinfo/networking/bookinfo-gateway.yaml -n target-services
kubectl delete -f ./istio-1.xx.x/samples/bookinfo/platform/kube/bookinfo.yaml -n target-services
```

#### 6. Uninstall Istio
```bash
# Remove Istio operator resources
istioctl uninstall --purge

# OR manually remove CRDs and resources
kubectl delete namespace istio-system
```

#### 7. Delete Namespaces
```bash
kubectl delete namespace target-services monitoring
```

### Verify Complete Cleanup
```bash
# Check no services remain
kubectl get pods --all-namespaces | grep -E "(prometheus|grafana|loki|vector|kiali|bookinfo|istio)"

# Should return empty results
```

### Restart from Scratch
```bash
# After complete uninstall, restart Kubernetes (if using minikube/kind)
minikube stop && minikube start
# or
kind delete cluster --name <cluster-name>
kind create cluster --name <cluster-name>
```

---

## Quick Start Commands

Copy-paste these commands to quickly set up the entire stack:

```bash
# 1. Add Helm repos
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo add vector https://helm.vector.dev
helm repo add kiali https://kiali.org/helm-charts
helm repo update

# 2. Create namespaces and enable Istio injection
kubectl create namespace target-services monitoring
kubectl label namespace target-services istio-injection=enabled
kubectl label namespace monitoring istio-injection=enabled

# 3. Install Istio
istioctl install --set profile=demo -y

# 4. Deploy Bookinfo
kubectl apply -f ./istio-1.xx.x/samples/bookinfo/platform/kube/bookinfo.yaml -n target-services
kubectl apply -f ./istio-1.xx.x/samples/bookinfo/networking/bookinfo-gateway.yaml -n target-services

# 5. Install monitoring stack
helm install prometheus prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
  --set prometheus.prometheusSpec.podMonitorSelectorNilUsesHelmValues=false

kubectl apply -f ./istio-1.xx.x/samples/addons/extras/prometheus-operator.yaml

# 6. Install Loki
helm install loki grafana/loki --namespace monitoring -f loki-single.yaml

# 7. Install Vector
helm install vector vector/vector --namespace monitoring -f vector-values.yaml

# 8. Install Kiali
helm install kiali kiali/kiali-server --namespace istio-system -f kiali-values.yaml

# 9. Launch services
kubectl port-forward --namespace monitoring svc/prometheus-grafana 8080:80 &
minikube tunnel &
istioctl dashboard kiali &

echo "✓ Setup complete! Access Grafana at http://localhost:8080"
```

---

## Additional Resources

- **Istio Documentation:** https://istio.io/latest/docs/
- **Prometheus Operator:** https://prometheus-operator.dev/
- **Grafana:** https://grafana.com/docs/
- **Loki:** https://grafana.com/docs/loki/latest/
- **Vector:** https://vector.dev/docs/
- **Kiali:** https://kiali.io/docs/

---

**Last Updated:** November 12, 2025  
**Version:** 1.0
