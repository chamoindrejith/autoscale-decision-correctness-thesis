#!/usr/bin/env bash
# install-k3s.sh
#
# Runs on a fresh Ubuntu 24.04 Droplet. Installs:
#   - k3s (lightweight Kubernetes)
#   - kubectl alias
#   - Helm (k8s package manager)
#   - Docker (to build the Ballerina app image)
#
# Usage (on the Droplet as root or with sudo):
#   curl -fsSL https://raw.githubusercontent.com/<you>/<repo>/main/install-k3s.sh | sudo bash
#   -- OR --
#   scp install-k3s.sh root@<DROPLET_IP>:/root/
#   ssh root@<DROPLET_IP> 'bash /root/install-k3s.sh'

set -euo pipefail

echo ">>> [1/5] Updating apt packages"
apt-get update -y
apt-get install -y curl ca-certificates gnupg lsb-release snapd jq

echo ">>> [2/5] Installing k3s"
# --write-kubeconfig-mode 644 makes the kubeconfig readable by non-root users.
# INSTALL_K3S_VERSION is pinned to the exact k3s (and therefore Kubernetes)
# version used by the counted campaign. Changing this changes: HPA default
# behavior policies, metrics-server sync period, container CPU accounting.
# Override at install time via `INSTALL_K3S_VERSION=... bash install-k3s.sh`
# only if you know why you're diverging from the campaign environment.
INSTALL_K3S_VERSION="${INSTALL_K3S_VERSION:-v1.34.6+k3s1}"
curl -sfL https://get.k3s.io | \
  INSTALL_K3S_VERSION="${INSTALL_K3S_VERSION}" \
  INSTALL_K3S_EXEC="--write-kubeconfig-mode 644" \
  sh -

# Wait for k3s to come up
echo "Waiting for k3s node to be Ready ..."
for i in {1..30}; do
  if k3s kubectl get nodes 2>/dev/null | grep -q " Ready "; then
    echo "k3s is Ready."
    break
  fi
  sleep 2
done

echo ">>> [3/5] Setting up kubectl for the current user"
mkdir -p "${HOME}/.kube"
cp /etc/rancher/k3s/k3s.yaml "${HOME}/.kube/config"
chown "$(id -u):$(id -g)" "${HOME}/.kube/config"

# Install a standalone kubectl (nicer than typing `k3s kubectl` every time)
if ! command -v kubectl >/dev/null 2>&1; then
  snap install kubectl --classic || {
    # Fallback: direct binary install
    KVER=$(curl -L -s https://dl.k8s.io/release/stable.txt)
    curl -LO "https://dl.k8s.io/release/${KVER}/bin/linux/amd64/kubectl"
    install -m 0755 kubectl /usr/local/bin/kubectl
    rm -f kubectl
  }
fi

echo ">>> [4/5] Installing Helm"
curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

echo ">>> [5/5] Installing Docker"
# Docker is used only to build the Ballerina sample-app image; the running
# workload uses k3s + containerd, not Docker. So the version is less
# critical than k3s, but we still pin loosely via the vendor script's
# `--version` flag. Override with DOCKER_VERSION=... to install a specific
# release for strict reproduction; leave blank to install the current
# vendor-supported release.
DOCKER_VERSION="${DOCKER_VERSION:-}"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  if [ -n "${DOCKER_VERSION}" ]; then
    sh /tmp/get-docker.sh --version "${DOCKER_VERSION}"
  else
    sh /tmp/get-docker.sh
  fi
  rm -f /tmp/get-docker.sh
fi

echo ""
echo "===================================================================="
echo "All done. Verify with:"
echo ""
echo "  kubectl get nodes"
echo "  helm version"
echo "  docker version"
echo ""
echo "Next step: install Ballerina and build the sample app."
echo "  bash install-ballerina.sh"
echo "===================================================================="
