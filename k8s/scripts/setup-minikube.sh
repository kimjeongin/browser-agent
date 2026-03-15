#!/usr/bin/env bash
# =============================================================================
# setup-minikube.sh — Idempotent Minikube cluster bootstrap for Browser Agent
# =============================================================================
# Run this script once before the first deployment.  It is safe to re-run.
#
# Prerequisites:
#   - minikube  >= 1.33
#   - kubectl   >= 1.28
#   - helm      >= 3.14
#   - istioctl  >= 1.23  (in PATH)
#   - argocd    CLI       (optional, for app management)
#
# Usage:
#   chmod +x k8s/scripts/setup-minikube.sh
#   ./k8s/scripts/setup-minikube.sh

set -euo pipefail

# ── Colour helpers ─────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── Configuration ──────────────────────────────────────────────────────────────
MINIKUBE_CPUS="${MINIKUBE_CPUS:-4}"
MINIKUBE_MEMORY="${MINIKUBE_MEMORY:-8192}"
MINIKUBE_DISK="${MINIKUBE_DISK:-40g}"
MINIKUBE_DRIVER="${MINIKUBE_DRIVER:-docker}"
MINIKUBE_K8S_VERSION="${MINIKUBE_K8S_VERSION:-v1.35.1}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
K8S_DIR="${REPO_ROOT}/k8s"

# ── 1. Minikube cluster ────────────────────────────────────────────────────────
info "Checking Minikube status..."
if minikube status --profile minikube &>/dev/null; then
  info "Minikube is already running."
else
  info "Starting Minikube (CPUs=${MINIKUBE_CPUS}, RAM=${MINIKUBE_MEMORY}MB, Disk=${MINIKUBE_DISK})..."
  minikube start \
    --cpus="${MINIKUBE_CPUS}" \
    --memory="${MINIKUBE_MEMORY}" \
    --disk-size="${MINIKUBE_DISK}" \
    --driver="${MINIKUBE_DRIVER}" \
    --kubernetes-version="${MINIKUBE_K8S_VERSION}" \
    --addons=storage-provisioner \
    --addons=default-storageclass
fi

# ── 2. Istio ───────────────────────────────────────────────────────────────────
info "Checking Istio installation..."
if kubectl get namespace istio-system &>/dev/null; then
  info "Istio namespace found — assuming already installed."
else
  if ! command -v istioctl &>/dev/null; then
    error "istioctl not found in PATH.  Install with: curl -L https://istio.io/downloadIstio | sh -"
  fi
  info "Installing Istio (demo profile)..."
  istioctl install --set profile=demo -y
  info "Waiting for Istio control plane to be ready..."
  kubectl rollout status deployment/istiod -n istio-system --timeout=300s
fi

# ── 3. Namespaces with Istio injection ────────────────────────────────────────
for ns in browser-agent browser-agent-infra browser-agent-observability; do
  if kubectl get namespace "${ns}" &>/dev/null; then
    info "Namespace ${ns} already exists."
  else
    info "Creating namespace: ${ns}"
    kubectl create namespace "${ns}"
  fi
  info "Labelling ${ns} for Istio sidecar injection..."
  kubectl label namespace "${ns}" istio-injection=enabled --overwrite
done

# ── 4. Istio mesh resources ───────────────────────────────────────────────────
info "Applying Istio mesh configuration..."
kubectl apply -f "${K8S_DIR}/istio/peer-auth.yaml"
kubectl apply -f "${K8S_DIR}/istio/destination-rules.yaml"
kubectl apply -f "${K8S_DIR}/istio/gateway.yaml"
kubectl apply -f "${K8S_DIR}/istio/virtual-services.yaml"
kubectl apply -f "${K8S_DIR}/istio/request-auth.yaml"
kubectl apply -f "${K8S_DIR}/istio/authz-policy.yaml"

# ── 5. Secrets (create only if they don't exist) ─────────────────────────────
create_secret_if_missing() {
  local ns="$1"; shift
  local name="$1"; shift
  if kubectl get secret "${name}" -n "${ns}" &>/dev/null; then
    info "Secret ${name} in ${ns} already exists — skipping."
  else
    warn "Creating placeholder secret: ${name} in ${ns}"
    warn "Replace these default values before production use!"
    kubectl create secret generic "${name}" -n "${ns}" "$@"
  fi
}

create_secret_if_missing browser-agent-infra postgresql-secret \
  --from-literal=postgres-password=changeme-postgres

create_secret_if_missing browser-agent-infra keycloak-secret \
  --from-literal=admin-password=changeme-keycloak

create_secret_if_missing browser-agent-infra minio-secret \
  --from-literal=root-user=minioadmin \
  --from-literal=root-password=changeme-minio

create_secret_if_missing browser-agent-observability grafana-secret \
  --from-literal=admin-user=admin \
  --from-literal=admin-password=changeme-grafana

# The observability chart reads the postgresql secret from the infra namespace.
# Copy it so Phoenix can reach it without cross-namespace secret access issues.
if kubectl get secret postgresql-secret -n browser-agent-observability &>/dev/null; then
  info "Secret postgresql-secret in browser-agent-observability already exists."
else
  info "Copying postgresql-secret to browser-agent-observability namespace..."
  kubectl get secret postgresql-secret -n browser-agent-infra -o json \
    | jq 'del(.metadata.namespace,.metadata.resourceVersion,.metadata.uid,.metadata.creationTimestamp)' \
    | jq '.metadata.namespace = "browser-agent-observability"' \
    | kubectl apply -f -
fi

# ── 6. Determine host Ollama IP ───────────────────────────────────────────────
info "Detecting host gateway IP for Ollama access..."
HOST_IP=$(minikube ssh "ip route | awk '/default/ {print \$3}'" 2>/dev/null | tr -d '\r' || echo "192.168.49.1")
info "Host gateway IP: ${HOST_IP}"
info "Use --set global.ollamaHostIP=${HOST_IP} when installing the browser-agent chart."

# ── 7. ArgoCD ─────────────────────────────────────────────────────────────────
info "Checking ArgoCD installation..."
if kubectl get namespace argocd &>/dev/null && \
   kubectl get deployment argocd-server -n argocd &>/dev/null; then
  info "ArgoCD already installed."
else
  info "Installing ArgoCD..."
  kubectl apply -f "${K8S_DIR}/argocd/install/argocd-install.yaml"
  kubectl apply -n argocd -f \
    https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

  info "Waiting for ArgoCD server to be ready (this may take 2-3 minutes)..."
  kubectl rollout status deployment/argocd-server -n argocd --timeout=300s

  # Disable TLS for Minikube convenience.
  kubectl patch configmap argocd-cmd-params-cm -n argocd \
    --patch '{"data":{"server.insecure":"true"}}' || true
  kubectl rollout restart deployment/argocd-server -n argocd
  kubectl rollout status deployment/argocd-server -n argocd --timeout=120s
fi

# ── 8. ArgoCD Project and Applications ────────────────────────────────────────
info "Applying ArgoCD AppProject..."
kubectl apply -f "${K8S_DIR}/argocd/projects/browser-agent-project.yaml"

info "Applying ArgoCD Applications..."
kubectl apply -f "${K8S_DIR}/argocd/applications/browser-agent-infra.yaml"
kubectl apply -f "${K8S_DIR}/argocd/applications/browser-agent-observability.yaml"
kubectl apply -f "${K8S_DIR}/argocd/applications/browser-agent-apps.yaml"

# ── 9. Summary ────────────────────────────────────────────────────────────────
MINIKUBE_IP=$(minikube ip)
ARGOCD_PORT=$(kubectl get svc argocd-server -n argocd \
  -o jsonpath='{.spec.ports[?(@.port==80)].nodePort}' 2>/dev/null || echo "N/A")
INGRESS_PORT=$(kubectl get svc istio-ingressgateway -n istio-system \
  -o jsonpath='{.spec.ports[?(@.name=="http2")].nodePort}' 2>/dev/null || echo "N/A")

echo ""
echo "======================================================================"
echo "  Browser Agent — Minikube setup complete"
echo "======================================================================"
echo ""
echo "  Minikube IP          : ${MINIKUBE_IP}"
echo "  Istio Ingress        : http://${MINIKUBE_IP}:${INGRESS_PORT}"
echo "  ArgoCD UI            : http://${MINIKUBE_IP}:${ARGOCD_PORT}"
echo "  Host (Ollama) IP     : ${HOST_IP}"
echo ""
echo "  ArgoCD admin password:"
echo "    kubectl get secret argocd-initial-admin-secret -n argocd \\"
echo "      -o jsonpath='{.data.password}' | base64 -d; echo"
echo ""
echo "  Next steps:"
echo "    1. Build and load images:  ./k8s/scripts/build-images.sh"
echo "    2. Deploy (manual):        ./k8s/scripts/deploy.sh"
echo "    3. Or use ArgoCD UI to sync applications."
echo "======================================================================"
