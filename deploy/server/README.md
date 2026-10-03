# Single-server production deployment

The MemoryWorks control plane runs on one Ubuntu 24.04 server: Caddy/TLS,
FastAPI, the remote Streamable HTTP MCP service, SQLite durable state, and
persistent ArcadeDB graph storage (plus Next.js, unless the web app is hosted
on Vercel; see the variant below). OCI Vault encrypts delegated OAuth grants.

memoryworks.app runs on an **OVH VPS-2** (4 vCores, 8 GB, 75 GB NVMe, US East /
Vint Hill), with OCI Vault and OCI Object Storage kept for key management and
offsite backups. An Oracle Cloud Ampere A1 VM works the same way.

## 1. Create the infrastructure

**The server.** Any Ubuntu 24.04 machine with at least 4 cores and 8 GB of
memory and a public IPv4 address. Add your SSH public key when ordering, or
afterwards to `~ubuntu/.ssh/authorized_keys`. Only Caddy publishes ports (80
and 443); `bootstrap.sh` also enables `ufw` for SSH, HTTP, and HTTPS. On Oracle
Cloud, open TCP 22, 80, and 443 in the network security list instead.

**The encryption key (OCI Vault).** In the Oracle Cloud Console, in the
tenancy's home region:

1. Create a Vault (the default, non-private type) and a symmetric AES key in
   it. Copy the key OCID and the vault's **Crypto Endpoint**.
2. Grant the server permission to use only that key.

   *On an Oracle VM* create a dynamic group matching only the instance,
   `ALL {instance.id = '<INSTANCE_OCID>'}`, and set
   `CONNECTOR_OCI_KMS_AUTH=instance-principal`.

   *On any other server* create an IAM user (for example `memoryworks-server`)
   with no console password, put it in its own group, and add an API signing
   key generated on your machine
   (`openssl genrsa -out memoryworks-kms.pem 2048` and
   `openssl rsa -pubout -in memoryworks-kms.pem -out memoryworks-kms.pub`).
   Copy the private key to the server as `~/.oci/memoryworks-kms.pem`
   (mode 600) and write `~/.oci/config`:

   ```ini
   [DEFAULT]
   user=<USER_OCID>
   fingerprint=<KEY_FINGERPRINT>
   tenancy=<TENANCY_OCID>
   region=<HOME_REGION>
   key_file=~/.oci/memoryworks-kms.pem
   ```

   Set `CONNECTOR_OCI_KMS_AUTH=config-file` and `OCI_CONFIG_DIR=/home/ubuntu/.oci`
   in `.env.production`; the backend mounts that directory read-only, and the
   `~` in `key_file` resolves both on the host and in the container.
3. Add the policy in the key's compartment, with the dynamic group or the
   user's group as the subject:

   `Allow group memoryworks-server to use keys in compartment id <COMPARTMENT_OCID> where target.key.id = '<KEY_OCID>'`

Either way the credential can encrypt and decrypt with one key and nothing
else, and no OCI credential is stored in `.env.production`.

## 2. Install Docker and clone

SSH to the server as `ubuntu`, copy `deploy/server/bootstrap.sh` to it, and run:

```sh
chmod +x bootstrap.sh
./bootstrap.sh
```

Log out and SSH in again, then:

```sh
git clone https://github.com/SanketBhangale1803/memoryworks.git
cd memoryworks
cp .env.production.example .env.production
chmod 600 .env.production
```

Replace dots in the reserved public IP with hyphens and use the result as a
free sslip.io domain. IP `203.0.113.10`, for example, becomes
`203-0-113-10.sslip.io`. Fill every required value in `.env.production`.

## 3. Configure delegated GitHub OAuth

Create a GitHub OAuth App with:

- Homepage: `https://app.<PUBLIC_DOMAIN>`
- Authorization callback: `https://api.<PUBLIC_DOMAIN>/api/auth/github/callback`

Put its client ID and client secret in `.env.production`. GitHub is used for
interactive sign-in; each connector authorization still creates a per-user
delegated grant in OCI Vault.

## 4. Start and verify

```sh
chmod +x deploy/server/up.sh deploy/server/verify.sh
./deploy/server/up.sh
./deploy/server/verify.sh
```

Caddy obtains and renews public TLS certificates automatically. After the
verification script passes, the GitHub repository can be made private without
interrupting the checked-out deployment. Configure a read-only deploy key
before the next `git pull` from the private repository.

## Variant: web app on Vercel, API and MCP on the server

This is how memoryworks.app runs: Vercel keeps serving the web app and forwards
`/api/*` and `/.well-known/*` to `api.<PUBLIC_DOMAIN>` on this server. The
browser only ever talks to the site, so session cookies stay first-party and the
OAuth callbacks already registered on GitHub and Google do not change. Hosted
MCP is served at `https://mcp.<PUBLIC_DOMAIN>/mcp`.

Follow sections 1–4 above with these differences.

**Infrastructure (section 1).**

- 8 GB of memory is enough for the API, MCP, and ArcadeDB (capped at a 2 GB
  heap); the Docker volumes live on the server's disk.
- On Oracle Cloud's Always Free Arm allowance (4 OCPUs and 24 GB in total), give
  the VM all of it and a 100 GB boot volume. Creation often fails with "Out of
  capacity"; retry another availability domain, upgrade to Pay As You Go, or
  use any other provider.
- Also create an Object Storage bucket (for example `memoryworks-backups`) with a
  lifecycle rule that deletes objects after 14 days, and extend the policy for
  the same subject:

  `Allow group memoryworks-server to manage objects in compartment id <COMPARTMENT_OCID> where target.bucket.name = 'memoryworks-backups'`

**DNS.** At the domain's registrar, add two A records pointing at the server's
public IP: `api` and `mcp`. Leave the apex and `www` on Vercel. Caddy issues
TLS for both names once DNS resolves.

**`.env.production` (section 2).**

```sh
PUBLIC_DOMAIN=memoryworks.app
SITE_URL=https://memoryworks.app
OCI_BACKUP_BUCKET=memoryworks-backups
```

Copy `GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET`, `GOOGLE_CLIENT_ID`/
`GOOGLE_CLIENT_SECRET`, and the model keys from the current Vercel backend
environment: the OAuth apps are already registered with callbacks on SITE_URL,
so section 3 is skipped. Generate a new `JWT_SECRET` and `ARCADEDB_PASSWORD`
(`openssl rand -base64 48`); everyone signs in once after cutover.

**Start and verify (section 4).** `up.sh` and `verify.sh` detect `SITE_URL` and
layer `deploy/server/compose.api-only.yml`, which drops the frontend container and
serves only `api.` and `mcp.`. `verify.sh` also fails unless `/api/health`
reports `"storage":"durable"`.

**Backups.** Add the nightly job with `crontab -e`:

```cron
15 3 * * * $HOME/memoryworks/deploy/server/backup.sh >> $HOME/memoryworks-backups/backup.log 2>&1
```

Run `deploy/server/backup.sh` once by hand, then do the restore drill: create a
second server the same way, copy the archive to it, run
`deploy/server/restore.sh <archive>`, and check that you can sign in and see the
same memories. Delete the second server afterwards.

**Cut over.** Point the site's forwarded paths at the server in `vercel.json`,
replacing the two rewrites that target the `backend` service:

```json
{ "source": "/api/:path*", "destination": "https://api.memoryworks.app/api/:path*" },
{ "source": "/.well-known/:path*", "destination": "https://api.memoryworks.app/.well-known/:path*" }
```

Then remove the `backend` service from `vercel.json` and deploy. Before calling
it done, check through the site: GitHub sign-in, listing repositories, a
streamed answer in chat, and a large file upload. Vercel proxies external
rewrites with its own body-size and duration limits; if uploads or long answers
fail there, those calls must go to `api.<PUBLIC_DOMAIN>` directly.

Anonymous requests all arrive from Vercel's proxy, so they share one public
rate-limit bucket (`API_RATE_LIMIT_PUBLIC_PER_MINUTE`); signed-in requests are
limited per session as before.
