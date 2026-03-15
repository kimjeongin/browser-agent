#!/usr/bin/env bash
# =============================================================================
# port-forward.sh — Start all port-forwards for local development
# =============================================================================
# Exposes cluster services to localhost, replacing docker-compose port bindings.
#
# Usage:
#   chmod +x k8s/scripts/port-forward.sh
#   ./k8s/scripts/port-forward.sh         # Start all
#   ./k8s/scripts/port-forward.sh stop    # Kill all

set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }

PID_FILE="/tmp/browser-agent-port-forwards.pid"

stop_all() {
  if [ -f "${PID_FILE}" ]; then
    info "Stopping port-forwards..."
    while IFS= read -r pid; do
      kill "${pid}" 2>/dev/null && echo "  killed PID ${pid}" || true
    done < "${PID_FILE}"
    rm -f "${PID_FILE}"
    info "Done."
  else
    warn "No PID file found (${PID_FILE}). Nothing to stop."
  fi
  exit 0
}

if [ "${1:-}" = "stop" ]; then
  stop_all
fi

# Kill any existing port-forwards first
if [ -f "${PID_FILE}" ]; then
  warn "Existing port-forwards found — stopping them first."
  stop_all || true
fi

> "${PID_FILE}"

start_pf() {
  local label="$1"
  local ns="$2"
  local svc="$3"
  local ports="$4"

  kubectl port-forward "svc/${svc}" ${ports} -n "${ns}" \
    --pod-running-timeout=30s &>/tmp/pf-${svc}.log &
  local pid=$!
  echo "${pid}" >> "${PID_FILE}"
  sleep 0.5
  if kill -0 "${pid}" 2>/dev/null; then
    info "  ${label} → localhost:${ports%:*}  (PID ${pid})"
  else
    warn "  ${label} FAILED — check /tmp/pf-${svc}.log"
  fi
}

echo ""
echo "======================================================================"
echo "  Starting port-forwards for Browser Agent"
echo "======================================================================"
echo ""

echo "── API / Extension ──────────────────────────────────────────────────"
# All extension traffic goes through Istio IngressGateway
start_pf "Istio IngressGateway (API)" "istio-system"  "istio-ingressgateway" "9000:80"

echo ""
echo "── Observability ────────────────────────────────────────────────────"
start_pf "Grafana               " "browser-agent-observability" "grafana"   "3000:3000"
start_pf "Phoenix               " "browser-agent-observability" "phoenix"   "6006:6006"
start_pf "Prometheus            " "browser-agent-observability" "prometheus" "9090:9090"

echo ""
echo "── Database (for docker-compose Keycloak) ───────────────────────────"
start_pf "PostgreSQL            " "browser-agent-infra"         "postgresql" "5432:5432"

echo ""
echo "── Storage (optional) ───────────────────────────────────────────────"
start_pf "MinIO Console         " "browser-agent-infra"         "minio"     "9001:9001"

echo ""
echo "======================================================================"
echo "  Port-forwards active (PIDs saved to ${PID_FILE})"
echo ""
echo "  Endpoint          Local URL"
echo "  ─────────────────────────────────────────────────────"
echo "  API / Extension   http://localhost:9000  (via Istio)"
echo "  Grafana           http://localhost:3000"
echo "  Phoenix           http://localhost:6006"
echo "  Prometheus        http://localhost:9090"
echo "  MinIO Console     http://localhost:9001"
echo "  PostgreSQL        localhost:5432"
echo ""
echo "  Keycloak (docker-compose — run separately):"
echo "    cd infra && docker-compose -f docker-compose.keycloak.yml up -d"
echo "    http://localhost:8080/admin"
echo ""
echo "  To stop:  ./k8s/scripts/port-forward.sh stop"
echo "======================================================================"
echo ""

# Keep script alive so port-forwards stay up (Ctrl+C to exit)
info "Press Ctrl+C to stop all port-forwards."
trap './k8s/scripts/port-forward.sh stop 2>/dev/null; exit 0' INT TERM
wait
