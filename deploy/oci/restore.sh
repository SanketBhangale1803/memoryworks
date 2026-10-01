#!/usr/bin/env bash
# Restore an archive written by backup.sh onto this VM, replacing the current
# SQLite database and ArcadeDB graph. Run it as the restore drill on a fresh VM
# before relying on the backups.
#
#   deploy/oci/restore.sh ~/memoryworks-backups/memoryworks-20261001T031500Z.tar.gz
set -euo pipefail

archive=${1:?usage: restore.sh <memoryworks-*.tar.gz>}
archive=$(cd "$(dirname "${archive}")" && pwd)/$(basename "${archive}")

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "${repo_dir}"
set -a
. ./.env.production
set +a

compose=(docker compose --env-file .env.production -f compose.production.yml)
if [[ -n ${SITE_URL:-} ]]; then
  compose+=(-f deploy/oci/compose.api-only.yml)
fi
project=$("${compose[@]}" config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')
database=${ARCADEDB_DATABASE:-runbook}

work=$(mktemp -d)
trap 'rm -rf "${work}"' EXIT
tar -C "${work}" -xzf "${archive}"
[[ -f ${work}/runbook.db && -f ${work}/arcadedb-${database}.zip ]] || {
  echo "${archive} does not contain runbook.db and arcadedb-${database}.zip" >&2
  exit 1
}

read -r -p "Replace this VM's data with ${archive}? [y/N] " answer
[[ ${answer} == y || ${answer} == Y ]] || exit 1

"${compose[@]}" stop caddy mcp backend arcadedb

# Both volumes are written through a throwaway container so the restore does
# not depend on tools inside the application images.
docker run --rm \
  -v "${project}_orgmemory_data:/data" \
  -v "${project}_arcadedb_data:/databases" \
  -v "${work}:/restore:ro" \
  python:3.12-slim python - "${database}" <<'PY'
import pathlib, shutil, sys, zipfile
database = sys.argv[1]
data = pathlib.Path("/data")
for suffix in ("", "-wal", "-shm"):
    (data / f"runbook.db{suffix}").unlink(missing_ok=True)
shutil.copy2("/restore/runbook.db", data / "runbook.db")
target = pathlib.Path("/databases") / database
shutil.rmtree(target, ignore_errors=True)
target.mkdir(parents=True)
with zipfile.ZipFile(f"/restore/arcadedb-{database}.zip") as backup:
    backup.extractall(target)
owner = pathlib.Path("/databases").stat()  # the volume belongs to the arcadedb user
for path in [target, *target.rglob("*")]:
    shutil.chown(path, owner.st_uid, owner.st_gid)
print(f"Restored runbook.db and {len(list(target.iterdir()))} graph files")
PY

"${compose[@]}" up -d
echo "Restored. Run deploy/oci/verify.sh, then sign in and check your memories."
