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

required=(PUBLIC_DOMAIN TLS_EMAIL ARCADEDB_PASSWORD JWT_SECRET GITHUB_CLIENT_ID GITHUB_CLIENT_SECRET CONNECTOR_KMS_KEY_ID CONNECTOR_OCI_KMS_CRYPTO_ENDPOINT)
for name in "${required[@]}"; do
  if [[ -z ${!name:-} ]]; then
    echo "${name} is required in .env.production" >&2
    exit 1
  fi
done

# With SITE_URL set, the web app is hosted elsewhere and this VM serves only
# api.<PUBLIC_DOMAIN> and mcp.<PUBLIC_DOMAIN>.
compose=(docker compose --env-file .env.production -f compose.production.yml)
if [[ -n ${SITE_URL:-} ]]; then
  compose+=(-f deploy/server/compose.api-only.yml)
fi

"${compose[@]}" config --quiet
"${compose[@]}" up -d --build

echo "Waiting for public HTTPS health check..."
for _ in $(seq 1 60); do
  if curl -fsS "https://api.${PUBLIC_DOMAIN}/api/health" >/dev/null; then
    echo "MemoryWorks API is live at https://api.${PUBLIC_DOMAIN}"
    [[ -z ${SITE_URL:-} ]] && echo "MemoryWorks is live at https://app.${PUBLIC_DOMAIN}"
    echo "Remote MCP is live at https://mcp.${PUBLIC_DOMAIN}/mcp"
    exit 0
  fi
  sleep 5
done

echo "Deployment started but health verification timed out." >&2
"${compose[@]}" ps
exit 1
