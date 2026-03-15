---
name: browser-agent k8s Helm chart setup
description: Complete Helm chart + Istio + ArgoCD setup written to k8s/ for Minikube deployment
type: project
---

The k8s/ directory was created at /Users/jeongin/workspace/spica/browser-agent/k8s/ with the following structure and key decisions:

**Three Helm charts (all first-party, no bitnami/upstream sub-chart deps):**
- `charts/infrastructure/` — PostgreSQL (pgvector/pgvector:pg16), Redis (8-alpine) + redis-exporter sidecar, Keycloak (26.5.5), MinIO
- `charts/observability/` — OTel Collector (0.147.0), Prometheus (v2.55.1), Loki (3.3.2), Grafana (11.4.0), Phoenix (version-13.10.0)
- `charts/browser-agent/` — Gateway, Orchestrator, Chat Agent, Browser Agent; each with Deployment + Service + HPA + PDB

**Namespaces:**
- `browser-agent` — app services (Istio injection enabled)
- `browser-agent-infra` — infrastructure (Istio injection enabled)
- `browser-agent-observability` — observability (Istio injection enabled)
- `argocd` — ArgoCD (injection disabled)

**Key design decisions:**
- Ollama host access via `hostAliases` (host.docker.internal -> ollamaHostIP, default 192.168.49.1). Set `global.ollamaHostIP` to output of `minikube ssh "ip route | awk '/default/ {print \$3}'"`.
- SSE endpoints (`/sessions/*/commands`, `/sessions/*/events`) get 1-hour Istio VirtualService timeout, retries disabled.
- Browser-agent Service is named `browser-agent-svc` to avoid collision with chart/Deployment name.
- StatefulSets use `standard` StorageClass (Minikube default).
- All secrets are pre-created (not rendered in charts); documented in NOTES.txt.
- Istio `RequestAuthentication` + `AuthorizationPolicy` handle JWT; app still validates for defence-in-depth.
- ArgoCD: infra+observability = auto-sync with prune+selfHeal; apps = manual sync.
- `rollme` annotation in app deployments causes ArgoCD diff — handled in `ignoreDifferences`.

**Why:** User requested full Minikube-deployable k8s setup with Istio ingress + JWT, ArgoCD GitOps, and first-party chart manifests for all services.

**How to apply:** Run `k8s/scripts/setup-minikube.sh` first, then `k8s/scripts/build-images.sh`, then `k8s/scripts/deploy.sh`.
