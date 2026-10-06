#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "${repo_dir}"
set -a
. ./.env.production
set +a

api="https://api.${PUBLIC_DOMAIN}"
health=$(curl -fsS "${api}/api/health")
echo "${health}"
if ! grep -q '"storage":"durable"' <<<"${health}"; then
  echo "Storage is not durable; check SQLITE_PATH, GRAPH_BACKEND, and the connector vault." >&2
  exit 1
fi
curl -fsS "${api}/.well-known/oauth-authorization-server" >/dev/null
curl -fsS "${api}/.well-known/oauth-protected-resource" >/dev/null
# The MCP endpoint answers an unauthenticated request with 401 and an OAuth
# challenge; anything else means it is not being served.
mcp_status=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "https://mcp.${PUBLIC_DOMAIN}/mcp")
if [[ ${mcp_status} != 401 ]]; then
  echo "Expected 401 from https://mcp.${PUBLIC_DOMAIN}/mcp, got ${mcp_status}" >&2
  exit 1
fi

# Imports run in the worker; with it down they queue up and never finish.
compose=(docker compose --env-file .env.production -f compose.production.yml)
[[ -n ${SITE_URL:-} ]] && compose+=(-f deploy/server/compose.api-only.yml)
if [[ -z $("${compose[@]}" ps --status running -q worker) ]]; then
  echo "The worker is not running: imports and syncs will queue without finishing." >&2
  echo "Check it with: ${compose[*]} logs worker" >&2
  exit 1
fi

if [[ -n ${SITE_URL:-} ]]; then
  # Only meaningful after the site forwards /api/* here.
  if curl -fsS "${SITE_URL}/api/health" | grep -q '"storage":"durable"'; then
    echo "${SITE_URL} is served by this VM."
  else
    echo "${SITE_URL}/api is not forwarded here yet (expected before cutover)."
  fi
else
  curl -fsS -o /dev/null "https://app.${PUBLIC_DOMAIN}"
fi

echo
echo "API, OAuth discovery, durable storage, MCP, and worker checks passed."
