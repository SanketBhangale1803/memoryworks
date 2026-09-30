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
git clone https://github.com/SanketBhangale1803/orgmemory.git
cd orgmemory
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
