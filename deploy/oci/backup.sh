#!/usr/bin/env bash
# Nightly backup of the durable state: the SQLite database and the ArcadeDB
# graph. Writes one archive to ~/memoryworks-backups, uploads it to OCI Object
# Storage when OCI_BACKUP_BUCKET is set, and prunes local archives older than
# BACKUP_RETENTION_DAYS (object retention is a lifecycle rule on the bucket).
#
#   crontab: 15 3 * * * $HOME/memoryworks/deploy/oci/backup.sh >> $HOME/memoryworks-backups/backup.log 2>&1
set -euo pipefail
export PATH="${HOME}/.local/bin:${PATH}"  # cron does not see pipx's oci

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "${repo_dir}"
set -a
. ./.env.production
set +a

compose=(docker compose --env-file .env.production -f compose.production.yml)
if [[ -n ${SITE_URL:-} ]]; then
  compose+=(-f deploy/oci/compose.api-only.yml)
fi

stamp=$(date -u +%Y%m%dT%H%M%SZ)
out_dir="${HOME}/memoryworks-backups"
work="${out_dir}/${stamp}"
mkdir -p "${work}"
trap 'rm -rf "${work}"' EXIT

# SQLite: the online backup API gives a consistent copy while the app runs.
"${compose[@]}" exec -T backend python - <<'PY'
import os, sqlite3
source = sqlite3.connect(os.environ["SQLITE_PATH"])
target = sqlite3.connect("/workspace/runbook/data/backup.db")
source.backup(target)
target.close()
source.close()
PY
"${compose[@]}" cp backend:/workspace/runbook/data/backup.db "${work}/runbook.db"
"${compose[@]}" exec -T backend rm -f /workspace/runbook/data/backup.db

# ArcadeDB: BACKUP DATABASE writes a zip inside the arcadedb container.
database=${ARCADEDB_DATABASE:-runbook}
response=$("${compose[@]}" exec -T backend curl -fsS -u "root:${ARCADEDB_PASSWORD}" \
  -X POST "http://arcadedb:2480/api/v1/command/${database}" \
  -H 'Content-Type: application/json' \
  -d '{"language":"sql","command":"backup database"}')
file=$(python3 -c 'import json,sys; r=json.load(sys.stdin).get("result") or [{}]; print(r[0].get("backupFile",""))' <<<"${response}")
path=$("${compose[@]}" exec -T arcadedb sh -c \
  "find /home/arcadedb -name '${file:-${database}-backup-*.zip}' 2>/dev/null | sort | tail -n 1")
if [[ -z ${path} ]]; then
  echo "ArcadeDB backup file not found (response: ${response})" >&2
  exit 1
fi
"${compose[@]}" cp "arcadedb:${path}" "${work}/arcadedb-${database}.zip"
"${compose[@]}" exec -T arcadedb rm -f "${path}"

archive="${out_dir}/memoryworks-${stamp}.tar.gz"
tar -C "${work}" -czf "${archive}" .
echo "Wrote ${archive} ($(du -h "${archive}" | cut -f1))"

if [[ -n ${OCI_BACKUP_BUCKET:-} ]]; then
  oci os object put --auth instance_principal --bucket-name "${OCI_BACKUP_BUCKET}" \
    --file "${archive}" --name "$(basename "${archive}")" >/dev/null
  echo "Uploaded to bucket ${OCI_BACKUP_BUCKET}"
fi

find "${out_dir}" -name 'memoryworks-*.tar.gz' -mtime +"${BACKUP_RETENTION_DAYS:-14}" -delete
