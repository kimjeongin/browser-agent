#!/usr/bin/env bash
# =============================================================================
# deploy.sh — Full Helm deployment for Browser Agent on Minikube
# =============================================================================
# This script is idempotent (uses `helm upgrade --install`).
# It deploys the three Helm charts in dependency order:
#   1. infrastructure  (PostgreSQL, Redis, Keycloak, MinIO)
#   2. observability   (OTel, Prometheus, Loki, Grafana, Phoenix)
#   3. browser-agent   (Gateway, Orchestrator, Chat Agent, Browser Agent)
#
# Usage:
#   chmod +x k8s/scripts/deploy.sh
#   ./k8s/scripts/deploy.sh [--only infra|observability|apps]
#
# Options:
#   --only infra         Deploy only infrastructure
#   --only observability Deploy only observability
#   --only apps          Deploy only application services
#   --dry-run            Print rendered manifests without applying

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
success() { echo -e "${CYAN}[OK]${NC}    $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
K8S_DIR="${REPO_ROOT}/k8s"

ONLY=""
DRY_RUN=""

# Parse arguments.
while [[ $# -gt 0 ]]; do
  case "$1" in
    --only) ONLY="$2"; shift 2 ;;
    --dry-run) DRY_RUN="--dry-run" ;;
    *) error "Unknown argument: $1" ;;
  esac
done

# ── Prerequisite checks ────────────────────────────────────────────────────────
command -v helm    &>/dev/null || error "helm not found"
command -v kubectl &>/dev/null || error "kubectl not found"

if ! minikube status &>/dev/null; then
  error "Minikube is not running. Run setup-minikube.sh first."
fi

# ── Detect Ollama host IP ─────────────────────────────────────────────────────
HOST_IP=$(minikube ssh "ip route | awk '/default/ {print \$3}'" 2>/dev/null \
  | tr -d '\r' || echo "192.168.49.1")
info "Using Ollama host IP: ${HOST_IP}"

# ── Helper: helm upgrade --install ────────────────────────────────────────────
helm_deploy() {
  local release="$1"
  local chart_path="$2"
  local namespace="$3"
  shift 3
  local extra_args=("$@")

  info "Deploying ${release} into namespace ${namespace}..."
  # shellcheck disable=SC2086
  helm upgrade --install "${release}" "${chart_path}" \
    --namespace "${namespace}" \
    --create-namespace \
    --wait \
    --timeout 10m \
    ${DRY_RUN} \
    "${extra_args[@]+"${extra_args[@]}"}"

  if [[ -z "${DRY_RUN}" ]]; then
    success "${release} deployed successfully."
  fi
  echo ""
}

# ── 1. Infrastructure ─────────────────────────────────────────────────────────
deploy_infra() {
  helm_deploy infrastructure \
    "${K8S_DIR}/charts/infrastructure" \
    browser-agent-infra \
    --values "${K8S_DIR}/charts/infrastructure/values.yaml"

  info "Waiting for PostgreSQL to be ready..."
  kubectl rollout status statefulset/postgresql -n browser-agent-infra --timeout=120s || true

  info "Waiting for Redis to be ready..."
  kubectl rollout status statefulset/redis -n browser-agent-infra --timeout=120s || true

  info "Waiting for Keycloak to be ready..."
  kubectl rollout status deployment/keycloak -n browser-agent-infra --timeout=300s || true

  info "Waiting for MinIO to be ready..."
  kubectl rollout status statefulset/minio -n browser-agent-infra --timeout=120s || true
}

# ── 2. Observability ──────────────────────────────────────────────────────────
deploy_observability() {
  helm_deploy observability \
    "${K8S_DIR}/charts/observability" \
    browser-agent-observability \
    --values "${K8S_DIR}/charts/observability/values.yaml"

  info "Waiting for OTel Collector to be ready..."
  kubectl rollout status deployment/otel-collector -n browser-agent-observability --timeout=120s || true
}

# ── 3. Application services ───────────────────────────────────────────────────
deploy_apps() {
  helm_deploy browser-agent \
    "${K8S_DIR}/charts/browser-agent" \
    browser-agent \
    --values "${K8S_DIR}/charts/browser-agent/values.yaml" \
    --values "${K8S_DIR}/charts/browser-agent/values-local.yaml" \
    --set global.ollamaHostIP="${HOST_IP}"
}

# ── Dispatch ──────────────────────────────────────────────────────────────────
case "${ONLY}" in
  "")
    deploy_infra
    deploy_observability
    deploy_apps
    ;;
  infra)
    deploy_infra
    ;;
  observability)
    deploy_observability
    ;;
  apps)
    deploy_apps
    ;;
  *)
    error "Invalid --only value: ${ONLY}. Use: infra | observability | apps"
    ;;
esac

# ── Summary ───────────────────────────────────────────────────────────────────
if [[ -z "${DRY_RUN}" ]]; then
  MINIKUBE_IP=$(minikube ip)
  INGRESS_PORT=$(kubectl get svc istio-ingressgateway -n istio-system \
    -o jsonpath='{.spec.ports[?(@.name=="http2")].nodePort}' 2>/dev/null || echo "N/A")

  echo ""
  echo "======================================================================"
  echo "  Browser Agent — Deployment complete"
  echo "======================================================================"
  echo ""
  echo "  Istio IngressGateway : http://${MINIKUBE_IP}:${INGRESS_PORT}"
  echo ""
  echo "  ★ All traffic must go through Istio IngressGateway."
  echo "    Run this in a separate terminal to expose it on localhost:9000:"
  echo ""
  echo "    kubectl port-forward svc/istio-ingressgateway 9000:80 -n istio-system"
  echo ""
  echo "    Extension .env: WXT_PUBLIC_API_BASE_URL=http://localhost:9000"
  echo ""
  echo "  Pod status:"
  kubectl get pods -n browser-agent 2>/dev/null || true
  echo ""
  echo "  Observability port-forwards:"
  echo "    kubectl port-forward svc/grafana    3000:3000 -n browser-agent-observability"
  echo "    kubectl port-forward svc/phoenix    6006:6006 -n browser-agent-observability"
  echo "    kubectl port-forward svc/prometheus 9090:9090 -n browser-agent-observability"
  echo "======================================================================"
fi
