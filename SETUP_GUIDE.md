# Cloud Automation K8s Setup Guide

Complete guide for deploying and managing Istio, Prometheus, Grafana, Loki, Vector, Kiali, and Bookinfo on Kubernetes.

---

## Table of Contents

1. [Kubernetes & Minikube Setup](#kubernetes--minikube-setup)
2. [Prerequisites & Initial Setup](#prerequisites--initial-setup)
3. [Namespace & Cluster Configuration](#namespace--cluster-configuration)
4. [Service Installation & Configuration](#service-installation--configuration)
5. [Port Forwarding & Access](#port-forwarding--access)
6. [Restart Instructions](#restart-instructions)
7. [Complete Uninstall](#complete-uninstall)

---

## Kubernetes & Minikube Setup

### Install Minikube (Kubernetes Cluster Manager)

Minikube allows you to run a local Kubernetes cluster on your machine for development and testing.

#### Prerequisites
- **CPU:** 2+ cores
- **RAM:** 2+ GB available
- **Disk Space:** 20+ GB available
- **Container Runtime:** Docker or Podman (must be installed and running)

#### Install Minikube

**On Linux (Fedora/RHEL/CentOS):**
```bash
# Download Minikube binary
curl -LO https://github.com/kubernetes/minikube/releases/latest/download/minikube-linux-amd64

# Make it executable and move to PATH
sudo install minikube-linux-amd64 /usr/local/bin/minikube

# Verify installation
minikube version
```

**On macOS:**
```bash
# Using Homebrew
brew install minikube

# Verify installation
minikube version
```

**On Windows:**
```powershell
# Using Chocolatey
choco install minikube

# Verify installation
minikube version
```

### Install kubectl (Kubernetes Command-Line Tool)

`kubectl` is the command-line interface for interacting with your Kubernetes cluster.

**On Linux (Fedora/RHEL/CentOS):**
```bash
# Download kubectl binary
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"

# Make it executable and move to PATH
sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl

# Verify installation
kubectl version --client
```

**On macOS:**
```bash
brew install kubectl
kubectl version --client
```

**On Windows:**
```powershell
choco install kubectl
kubectl version --client
```

### Start Minikube Cluster

#### Basic Start (Default Configuration)
```bash
# Start a Minikube cluster with default settings
minikube start

# This will:
# - Create a virtual machine (or container)
# - Install Kubernetes
# - Configure kubectl to use the cluster
```

#### Start with Custom Configuration (Recommended for This Setup)
```bash
# Start with more resources to handle Istio + monitoring stack
minikube start \
  --cpus=4 \
  --memory=8192 \
  --disk-size=30g \
  --driver=docker

# Flags explained:
# --cpus=4          — Allocate 4 CPU cores
# --memory=8192     — Allocate 8GB RAM (in MB)
# --disk-size=30g   — Allocate 30GB disk space
# --driver=docker   — Use Docker as the container runtime
```

#### Verify Cluster Is Running
```bash
# Check Minikube status
minikube status

# Should show:
# minikube
# type: Control Plane
# host: Running
# kubelet: Running
# apiserver: Running

# Check kubectl can access the cluster
kubectl cluster-info

# Check all system pods are running
kubectl get pods -n kube-system
```

### Enable Required Minikube Add-ons

Minikube has built-in add-ons that enable additional functionality.

#### Enable Metrics Server (for resource monitoring)
```bash
minikube addons enable metrics-server

# Verify
kubectl get deployment metrics-server -n kube-system
```

#### Enable Ingress Controller
```bash
minikube addons enable ingress

# Verify
kubectl get pods -n ingress-nginx
```

#### List All Available Add-ons
```bash
minikube addons list
```

### Access the Minikube Dashboard (Optional)

Minikube includes a built-in Kubernetes dashboard for visual cluster management.

```bash
# Launch the dashboard in your browser
minikube dashboard

# This opens a web UI showing:
# - All namespaces and resources
# - Pods, services, deployments
# - Resource usage and logs
# - Configuration and settings
```

### Useful Minikube Commands

| Command | Purpose |
|---------|---------|
| `minikube status` | Show the current status of Minikube cluster |
| `minikube start` | Start the Minikube cluster |
| `minikube stop` | Stop the cluster (preserves state) |
| `minikube restart` | Restart the cluster |
| `minikube delete` | Delete the cluster (WARNING: deletes all data) |
| `minikube ip` | Get the IP address of Minikube |
| `minikube ssh` | SSH into the Minikube VM |
| `minikube logs` | View Minikube logs for debugging |
| `minikube dashboard` | Open Kubernetes dashboard in browser |
| `minikube addons list` | List all available add-ons |
| `minikube addons enable <addon>` | Enable a specific add-on |
| `minikube addons disable <addon>` | Disable a specific add-on |
| `minikube tunnel` | Enable external access to LoadBalancer services |
| `minikube docker-env` | Set Docker environment to use Minikube's Docker daemon |
| `minikube mount <host-path>:<vm-path>` | Mount a directory from host into Minikube VM |

### Verify Kubernetes Is Ready

```bash
# Check all system namespaces
kubectl get pods -A

# Check nodes
kubectl get nodes

# Check cluster info
kubectl cluster-info

# You should see a single node named "minikube"
```

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

#### Apply Alert Rules
```bash
# Apply alert rules for target services (Bookinfo apps)
kubectl apply -f ./prometheus/alerts.yaml -n monitoring

# Apply alert rules for CS Model service
kubectl apply -f ./prometheus/cs_rules.yml -n monitoring
```

**Verify Alert Rules Are Loaded:**
```bash
# Check if PrometheusRule CRDs were created
kubectl get prometheusrules -n monitoring

# Access Prometheus UI and verify alerts are listed
kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090
# Open http://localhost:9090/alerts to see all configured alerts
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
  -f helm/loki/values.yaml
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
  -f helm/loki/values.yaml
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
  -f helm/vector/values.yaml
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
  -f helm/vector/values.yaml
```

#### Check Vector Logs
```bash
# View logs from Vector DaemonSet
kubectl logs -n monitoring -l app.kubernetes.io/name=vector --tail=50
```

---

### 6. CS Model (ML Prediction Service)

The CS (Container-Spine) ML Model service queries Prometheus for metrics, performs LSTM predictions, and sends alerts to Alertmanager when SLO thresholds are exceeded.

#### Install CS Model Service
```bash
# Navigate to the Helm chart directory
cd helm/monitoring-services

# Install the monitoring-services Helm release (deploys CS Model + ConfigMap)
helm install monitoring-services . --namespace monitoring

# Verify deployment
kubectl get pods -n monitoring -l app=monitoring-services-cs-model
kubectl get svc -n monitoring monitoring-services-cs-model
```

**Verify Deployment:**
```bash
# Check pod status (should be 1/1 Running)
kubectl get pods -n monitoring -l app=monitoring-services-cs-model

# View pod logs
kubectl logs -n monitoring -l app=monitoring-services-cs-model

# Expected output:
# ✓ Connected to Prometheus at http://prometheus-kube-prometheus-prometheus:9090
# ✓ Metrics exposed on 0.0.0.0:9001
# ✓ CS ML Service started successfully. Entering prediction cycle...
```

#### Upgrade CS Model Configuration
```bash
cd helm/monitoring-services

# If you modified values.yaml, upgrade the release
helm upgrade monitoring-services . --namespace monitoring
```

#### Access CS Model Metrics
```bash
# Port-forward to metrics endpoint (port 9001)
kubectl port-forward -n monitoring svc/monitoring-services-cs-model 9001:9001

# In another terminal, query metrics
curl http://localhost:9001/metrics | grep cs_ml

# Expected output:
# cs_ml_predicted_cpu_user_rate{instance="ch-application-host",target_metric="node_cpu_user_rate"} <value>
# cs_ml_slo_status{instance="ch-application-host",slo_threshold="0.01"} <status>
```

#### Verify Prometheus Scraping CS Model Metrics
```bash
# Access Prometheus UI
kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090

# Open http://localhost:9090 in browser
# Query: cs_ml_predicted_cpu_user_rate
# Should see metrics from CS Model service
```

#### Check for Alerts
```bash
# Access Alertmanager UI
kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-alertmanager 9093:9093

# Open http://localhost:9093 in browser
# Check if "PredictedCpuBreach" alerts are firing
```

---

### 7. Kiali (Service Mesh Visualization)

Kiali provides a visual UI for managing and monitoring the Istio service mesh, with integration to Prometheus, Grafana, and Jaeger.

#### Install Kiali
```bash
helm install kiali kiali/kiali-server \
  --namespace istio-system \
  -f helm/kiali/values.yaml
```

**Verify:**
```bash
kubectl get pods -n istio-system -l app=kiali
```

#### Upgrade Kiali Configuration
```bash
helm upgrade kiali kiali/kiali-server \
  --namespace istio-system \
  -f helm/kiali/values.yaml
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
| **Alertmanager** | `kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-alertmanager 9093:9093` | http://localhost:9093 | N/A (no auth) |
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

# Terminal 3: Alertmanager
kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-alertmanager 9093:9093

# Terminal 4: Loki
kubectl port-forward -n monitoring svc/loki 3100:3100

# Terminal 5: Bookinfo (optional)
kubectl port-forward -n target-services svc/productpage 9080:9080
```

Or use background processes:

```bash
# Run all port-forwards in background
kubectl port-forward --namespace monitoring svc/prometheus-grafana 8080:80 &
kubectl port-forward --namespace monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090 &
kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-alertmanager 9093:9093 &
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

#### 7. Stop CS Model Service
```bash
kubectl scale deployment monitoring-services-cs-model -n monitoring --replicas=0
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

#### 7. Restart CS Model Service
```bash
kubectl scale deployment monitoring-services-cs-model -n monitoring --replicas=1
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

#### 2. Uninstall CS Model Service
```bash
helm uninstall monitoring-services --namespace monitoring
```

#### 3. Uninstall Vector
```bash
helm uninstall vector --namespace monitoring
```

#### 4. Uninstall Loki
```bash
helm uninstall loki --namespace monitoring
```

#### 5. Uninstall Prometheus Stack
```bash
helm uninstall prometheus --namespace monitoring
```

#### 6. Remove Bookinfo Applications
```bash
kubectl delete -f ./istio-1.xx.x/samples/bookinfo/networking/bookinfo-gateway.yaml -n target-services
kubectl delete -f ./istio-1.xx.x/samples/bookinfo/platform/kube/bookinfo.yaml -n target-services
```

#### 7. Uninstall Istio
```bash
# Remove Istio operator resources
istioctl uninstall --purge

# OR manually remove CRDs and resources
kubectl delete namespace istio-system
```

#### 8. Delete Namespaces
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

# Apply alert rules
kubectl apply -f ./prometheus/alerts.yaml -n monitoring
kubectl apply -f ./prometheus/cs_rules.yml -n monitoring

# 6. Install Loki
helm install loki grafana/loki --namespace monitoring -f helm/loki/values.yaml

# 7. Install Vector
helm install vector vector/vector --namespace monitoring -f helm/vector/values.yaml

# 8. Install CS Model Service
cd helm/monitoring-services
helm install monitoring-services . --namespace monitoring
cd ../..

# 9. Install Kiali
helm install kiali kiali/kiali-server --namespace istio-system -f helm/kiali/values.yaml

# 10. Launch services
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
