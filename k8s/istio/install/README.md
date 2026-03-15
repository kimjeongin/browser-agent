# Istio Installation for Minikube

Istio is installed via `istioctl` (not via Helm dependency) to keep the
operator model separate from application chart state.

## Prerequisites

```bash
# Install istioctl (1.23+ recommended)
curl -L https://istio.io/downloadIstio | ISTIO_VERSION=1.23.0 sh -
export PATH="$PWD/istio-1.23.0/bin:$PATH"
```

## Install

The `setup-minikube.sh` script handles this automatically.  To install
manually:

```bash
istioctl install --set profile=demo -y

# Verify
kubectl get pods -n istio-system
istioctl verify-install
```

The `demo` profile installs:
- istiod (control plane)
- istio-ingressgateway (NodePort on Minikube)
- istio-egressgateway

## Uninstall

```bash
istioctl uninstall --purge -y
kubectl delete namespace istio-system
```
