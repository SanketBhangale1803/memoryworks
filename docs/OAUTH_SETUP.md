# OAuth provider setup

MemoryWorks never asks a user to paste a GitHub personal access token or Slack bot
token into the browser. OAuth integrations use provider-hosted authorization code
flows, server-side code exchange, single-use state, and encrypted workspace storage.

How sign-in and source access relate:

- **GitHub sign-in also connects GitHub.** One consent screen identifies the
  person and grants repository access; the grant is stored as that person's
  GitHub connection in the active workspace, so they go straight to choosing
  repositories.
- Google sign-in and passwordless email (a six-digit, ten-minute, one-time code)
  identify the person only. They connect GitHub, Slack, and other sources from
  **Sources** afterwards.
- Every source grant is per person and per workspace, encrypted by the token vault.

## GitHub

Create one GitHub OAuth App under **Settings → Developer settings → OAuth Apps**.

| Field | Local value | Production value (memoryworks.app) |
| --- | --- | --- |
| Application name | `MemoryWorks Local` | `MemoryWorks` |
| Homepage URL | `http://localhost:3000` | `https://memoryworks.app` |
| Authorization callback URL | `http://localhost:8000/api/auth/github/callback` | `https://memoryworks.app/api/auth/github/callback` |

A GitHub OAuth App accepts **one** callback URL, so use a separate OAuth App for
local development. On a split-domain deployment (for example the `api.`
subdomain in `deploy/server/`), the callback is on the API host.

Copy the client ID and generate a client secret, then set the server-only values:

```dotenv
GITHUB_CLIENT_ID=...
GITHUB_CLIENT_SECRET=...
GITHUB_REDIRECT_URI=http://localhost:8000/api/auth/github/callback
```

Restart MemoryWorks. Sign-in and **Connect GitHub** use the same OAuth App and
the same callback, and both request `repo read:org read:user user:email`:
`repo read:org` is what a GitHub OAuth App needs to discover and clone private
repositories. When the returned grant includes `repo`, sign-in stores it as the
person's GitHub connection. If someone narrows the grant and `repo` is missing,
nothing is stored — Sources shows GitHub as not connected rather than a
connection that cannot read anything — and **Connect GitHub** starts the
authorization again directly.

GitHub OAuth does not offer a read-only source-code scope. A GitHub App would
allow repository-level selection and short-lived, fine-grained installation
tokens; that migration is on the roadmap.

For an organization using SAML SSO or third-party application restrictions, an
organization owner may also need to approve or authorize the OAuth App.

If the login screen says GitHub is unavailable, inspect the readiness endpoint:

```bash
curl http://localhost:8000/api/auth/providers
```

The response names the missing environment variables without exposing any secret.

## Google

Create an OAuth 2.0 **Web application** in Google Cloud, configure its consent
screen, and add these authorized redirect URIs:

| Environment | Redirect URI |
| --- | --- |
| Local | `http://localhost:8000/api/auth/google/callback` |
| Production (memoryworks.app) | `https://memoryworks.app/api/auth/google/callback` |

Then set:

```dotenv
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=http://localhost:8000/api/auth/google/callback
```

Google sign-in requests `openid email profile`, uses PKCE, consumes OAuth state
once, creates the user/workspace session server-side, and stores no Google access
token because identity login does not need one after the profile is read.

## Passwordless email

Development mode returns the code only in the `/api/auth/email/request` response
so local sign-in works without a mail service. Production refuses to enable email
delivery unless SMTP is configured:

```dotenv
EMAIL_AUTH_ENABLED=true
EMAIL_CODE_TTL_MINUTES=10
EMAIL_CODE_RESEND_SECONDS=45
EMAIL_FROM=memory@company.com
SMTP_HOST=smtp.company.com
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...
SMTP_STARTTLS=true
```

Only an HMAC of the code is stored. A newer code invalidates older codes, codes
expire after the configured TTL, and a verified code cannot be reused.

## Slack

Create a Slack app **From an app manifest** and use this local manifest:

```yaml
display_information:
  name: MemoryWorks Local
oauth_config:
  redirect_urls:
    - http://localhost:8000/api/auth/slack/callback
  scopes:
    user:
      - channels:read
      - channels:history
      - groups:read
      - groups:history
      - chat:write
settings:
  org_deploy_enabled: false
  socket_mode_enabled: false
  token_rotation_enabled: false
```

Copy the client ID and client secret from **Basic Information**, then set:

```dotenv
SLACK_CLIENT_ID=...
SLACK_CLIENT_SECRET=...
SLACK_REDIRECT_URI=http://localhost:8000/api/auth/slack/callback
```

Restart MemoryWorks and select **Connect Slack**. The connecting user chooses the
workspace on Slack. MemoryWorks stores the returned user-scoped token and can list
the public and private conversations that person is allowed to access. It does
not gain access to channels the person cannot see.

## Production origin

On a hosted deployment MemoryWorks derives every OAuth callback from
`PUBLIC_BASE_URL` (memoryworks.app sets it to `https://memoryworks.app`) and
carries it through the signed OAuth state, so a local `*_REDIRECT_URI` default
cannot leak into a production authorization request. Changing the domain means
updating `PUBLIC_BASE_URL` and the other URL settings, redeploying, and updating
the callback in each provider's console at the same time.

For production, replace every redirect URL with HTTPS before issuing any
credentials, set `AUTH_DEV_MODE=false`, set a non-default `JWT_SECRET`, and
provide a stable KMS-managed `INTEGRATION_ENCRYPTION_KEY`.
