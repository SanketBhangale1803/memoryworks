#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "${repo_dir}"

if [[ ! -f .env.production ]]; then
  echo "Missing ${repo_dir}/.env.production; copy .env.production.example and fill it first." >&2
  exit 1
fi

set -a
. ./.env.production
set +a

required=(PUBLIC_DOMAIN TLS_EMAIL ARCADEDB_PASSWORD JWT_SECRET NEXTAUTH_SECRET GITHUB_CLIENT_ID GITHUB_CLIENT_SECRET CONNECTOR_KMS_KEY_ID CONNECTOR_OCI_KMS_CRYPTO_ENDPOINT)
for name in "${required[@]}"; do
  if [[ -z ${!name:-} ]]; then
    echo "${name} is required in .env.production" >&2
    exit 1
  fi
done

docker compose --env-file .env.production -f compose.production.yml config --quiet
docker compose --env-file .env.production -f compose.production.yml up -d --build

echo "Waiting for public HTTPS health check..."
for _ in $(seq 1 60); do
  if curl -fsS "https://api.${PUBLIC_DOMAIN}/api/health" >/dev/null; then
    echo "MemoryWorks is live at https://app.${PUBLIC_DOMAIN}"
    echo "Remote MCP is live at https://mcp.${PUBLIC_DOMAIN}/mcp"
    exit 0
  fi
  sleep 5
done

echo "Deployment started but health verification timed out." >&2
docker compose --env-file .env.production -f compose.production.yml ps
exit 1
