#!/usr/bin/env bash
# =============================================================================
# build-images.sh — Build Docker images and load them into Minikube
# =============================================================================
# Minikube's container runtime is isolated from the host Docker daemon.
# This script builds each service image in the host Docker context and
# then loads the tarball into Minikube with `minikube image load`.
#
# Usage:
#   chmod +x k8s/scripts/build-images.sh
#   ./k8s/scripts/build-images.sh [service...]
#
# Examples:
#   ./k8s/scripts/build-images.sh            # Build all services
#   ./k8s/scripts/build-images.sh gateway    # Build gateway only
#   ./k8s/scripts/build-images.sh gateway orchestrator

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
success() { echo -e "${CYAN}[OK]${NC}    $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SERVICES_DIR="${REPO_ROOT}/services"

# Returns Dockerfile path for a given service name.
get_dockerfile() {
  case "$1" in
    gateway)      echo "gateway/Dockerfile" ;;
    orchestrator) echo "orchestrator/Dockerfile" ;;
    chat-agent)   echo "chat_agent/Dockerfile" ;;
    browser-agent) echo "browser_agent/Dockerfile" ;;
    *) return 1 ;;
  esac
}

# Returns image tag for a given service name.
get_image() {
  case "$1" in
    gateway)      echo "browser-agent/gateway:latest" ;;
    orchestrator) echo "browser-agent/orchestrator:latest" ;;
    chat-agent)   echo "browser-agent/chat-agent:latest" ;;
    browser-agent) echo "browser-agent/browser-agent:latest" ;;
    *) return 1 ;;
  esac
}

# Determine which services to build.
if [ $# -eq 0 ]; then
  TARGETS="gateway orchestrator chat-agent browser-agent"
else
  TARGETS="$*"
fi

# Verify Minikube is running.
if ! minikube status &>/dev/null; then
  error "Minikube is not running. Start it with: ./k8s/scripts/setup-minikube.sh"
fi

info "Building services: ${TARGETS}"
echo ""

for svc in ${TARGETS}; do
  if ! get_dockerfile "${svc}" > /dev/null 2>&1; then
    error "Unknown service: ${svc}. Valid: gateway orchestrator chat-agent browser-agent"
  fi

  DOCKERFILE="$(get_dockerfile "${svc}")"
  IMAGE="$(get_image "${svc}")"

  info "Building ${svc} -> ${IMAGE}"
  docker build \
    --file "${SERVICES_DIR}/${DOCKERFILE}" \
    --tag "${IMAGE}" \
    "${SERVICES_DIR}"

  info "Loading ${IMAGE} into Minikube..."
  minikube image load "${IMAGE}"

  success "${svc} image ready in Minikube: ${IMAGE}"
  echo ""
done

echo ""
info "All images loaded. Verify with:"
echo "  minikube image ls | grep browser-agent"
echo ""
info "To trigger a rolling restart after image reload:"
echo "  kubectl rollout restart deployment -n browser-agent"
