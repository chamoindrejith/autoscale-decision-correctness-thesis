#!/usr/bin/env bash
# install-monitoring.sh
#
# Installs the kube-prometheus-stack Helm chart into a 'monitoring' namespace.
# Provides: Prometheus, Grafana, Alertmanager, node-exporter, kube-state-metrics.

set -euo pipefail

CHART_VERSION="${CHART_VERSION:-}"        # blank = latest
RELEASE_NAME="kube-prometheus-stack"
NAMESPACE="monitoring"

echo ">>> Adding Prometheus community Helm repo"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
helm repo update >/dev/null

echo ">>> Creating namespace ${NAMESPACE} (if missing)"
kubectl get ns "${NAMESPACE}" >/dev/null 2>&1 || kubectl create ns "${NAMESPACE}"

echo ">>> Installing ${RELEASE_NAME}"
VERSION_ARG=""
[ -n "${CHART_VERSION}" ] && VERSION_ARG="--version ${CHART_VERSION}"

helm upgrade --install "${RELEASE_NAME}" prometheus-community/kube-prometheus-stack \
  --namespace "${NAMESPACE}" \
  ${VERSION_ARG} \
  --values "$(dirname "$0")/prometheus-values.yaml" \
  --wait --timeout 10m

echo ""
echo ">>> Waiting for Grafana pod ..."
kubectl -n "${NAMESPACE}" rollout status deploy/${RELEASE_NAME}-grafana --timeout=5m

echo ""
echo "===================================================================="
echo "Monitoring stack installed."
echo ""
echo "Access Grafana with port-forward (run this and keep the terminal open):"
echo ""
echo "  kubectl -n ${NAMESPACE} port-forward svc/${RELEASE_NAME}-grafana \\"
echo "    3000:80 --address 0.0.0.0"
echo ""
echo "Then open http://<DROPLET_IP>:3000"
echo "Login:   admin"
echo "Password (run this to fetch):"
echo ""
echo "  kubectl -n ${NAMESPACE} get secret ${RELEASE_NAME}-grafana \\"
echo "    -o jsonpath='{.data.admin-password}' | base64 -d; echo"
echo ""
echo "Import the custom dashboard from grafana-dashboard.json"
echo "  Dashboards -> New -> Import -> Upload JSON file"
echo "===================================================================="
