# Free production deployment on Oracle Cloud

This deployment keeps the whole MemoryWorks control plane on one Oracle Cloud
Always Free Ampere A1 VM: Caddy/TLS, Next.js, FastAPI, the remote Streamable
HTTP MCP service, SQLite durable state, and persistent ArcadeDB graph storage.
OCI Vault protects delegated OAuth grants with the VM's instance principal.

## 1. Create the free infrastructure

In the Oracle Cloud Console, use the tenancy's **home region**:

1. Create an Ubuntu 24.04 Ampere A1 Compute instance with **2 OCPUs and 12 GB
   memory**, 50 GB boot storage, a reserved public IPv4 address, and your SSH
   public key. The shape must show **Always Free-eligible**.
2. Allow inbound TCP 22, 80, and 443 in its network security list. Do not open
   ports 2480, 2424, 3000, 8000, or 8001.
3. Create a standard OCI Vault and a symmetric AES key. Copy its key OCID and
   the vault's **Crypto Endpoint**.
4. Create a dynamic group matching only this instance:

   `ALL {instance.id = '<INSTANCE_OCID>'}`

5. Add this policy in the key's compartment, substituting the dynamic group
   name and key OCID:

   `Allow dynamic-group orgmemory-vm to use keys in compartment id <COMPARTMENT_OCID> where target.key.id = '<KEY_OCID>'`

The policy is what lets the container use the VM's short-lived instance
identity. No shared OCI credential is placed in MemoryWorks.

## 2. Install Docker and clone

SSH to the VM as `ubuntu`, copy `deploy/oci/bootstrap.sh` to it, and run:

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
chmod +x deploy/oci/up.sh deploy/oci/verify.sh
./deploy/oci/up.sh
./deploy/oci/verify.sh
```

Caddy obtains and renews public TLS certificates automatically. After the
verification script passes, the GitHub repository can be made private without
interrupting the checked-out deployment. Configure a read-only deploy key
before the next `git pull` from the private repository.

## Variant: web app on Vercel, API and MCP on the VM

This is how memoryworks.app runs: Vercel keeps serving the web app and forwards
`/api/*` and `/.well-known/*` to `api.<PUBLIC_DOMAIN>` on this VM. The browser
only ever talks to the site, so session cookies stay first-party and the OAuth
callbacks already registered on GitHub and Google do not change. Hosted MCP is
served at `https://mcp.<PUBLIC_DOMAIN>/mcp`.

Follow sections 1–4 above with these differences.

**Infrastructure (section 1).**

- Sign up at cloud.oracle.com. The home region you pick cannot be changed and
  Always Free compute only runs there; choose one close to your users. A card
  is required for verification; Always Free resources are not charged.
- The Always Free Arm allowance is 4 OCPUs and 24 GB in total. Give this VM all
  of it and a 100 GB boot volume; the Docker volumes live on the boot volume.
- If instance creation fails with "Out of capacity", try another availability
  domain, retry later, or upgrade to Pay As You Go: Always Free shapes stay free.
- Also create an Object Storage bucket (for example `memoryworks-backups`) with a
  lifecycle rule that deletes objects after 14 days, and extend the policy:

  `Allow dynamic-group orgmemory-vm to manage objects in compartment id <COMPARTMENT_OCID> where target.bucket.name = 'memoryworks-backups'`

**DNS.** At the domain's registrar, add two A records pointing at the VM's
reserved IP: `api` and `mcp`. Leave the apex and `www` on Vercel. Caddy issues
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
layer `deploy/oci/compose.api-only.yml`, which drops the frontend container and
serves only `api.` and `mcp.`. `verify.sh` also fails unless `/api/health`
reports `"storage":"durable"`.

**Backups.** Add the nightly job with `crontab -e`:

```cron
15 3 * * * $HOME/memoryworks/deploy/oci/backup.sh >> $HOME/memoryworks-backups/backup.log 2>&1
```

Run `deploy/oci/backup.sh` once by hand, then do the restore drill: create a
second VM the same way, copy the archive to it, run
`deploy/oci/restore.sh <archive>`, and check that you can sign in and see the
same memories. Delete the second VM afterwards.

**Cut over.** Point the site's forwarded paths at the VM in `vercel.json`,
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
