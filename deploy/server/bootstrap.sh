#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -eq 0 ]]; then
  echo "Run this script as the normal ubuntu user; it invokes sudo where needed." >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "${USER}"

# Only Caddy publishes ports; the firewall keeps anything else that listens
# on the host off the internet.
sudo apt-get install -y ufw
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp
sudo ufw --force enable
sudo systemctl enable --now docker

# oci-cli is used only when OCI_BACKUP_BUCKET sends nightly backups to OCI
# Object Storage.
sudo apt-get install -y pipx
pipx install oci-cli
pipx ensurepath

echo
echo "Docker is installed. Log out and SSH back in so group membership applies."
echo "Then clone MemoryWorks, populate .env.production, and run deploy/server/up.sh."
