# Connectors

Application login and source connectors are separate concerns: a user logs
into MemoryWorks, then connects sources. Connector tokens are verified against
the provider before encrypted storage (Fernet; AWS KMS or OCI Vault in
production).

## GitHub

- OAuth: create a GitHub OAuth App with callback
  `http://localhost:8000/api/auth/github/callback`, set `GITHUB_CLIENT_ID`
  and `GITHUB_CLIENT_SECRET`, then use **Connect with GitHub**. Scopes
  `repo read:org` allow private repository discovery.
- Token fallback: paste a fine-grained PAT in the UI or set `GITHUB_TOKEN`.
- Endpoints: `GET /api/connectors/github/auth/start`,
  `GET /api/connectors/github/auth/callback`,
  `POST /api/connectors/github/token`, `GET /api/connectors/github/status`,
  `GET /api/connectors/github/repos`, `POST /api/ingest/github`.
- Ingestion covers files, imports, endpoints, env vars, compose files,
  workflows, Jenkinsfiles, package manifests, issues, and PRs with original
  URLs; re-ingestion replaces prior repository knowledge for the project.

## Slack

- OAuth: create a Slack app with redirect
  `http://localhost:8000/api/auth/slack/callback`, set `SLACK_CLIENT_ID` and
  `SLACK_CLIENT_SECRET`. The app must be invited to private channels.
- Token fallback: bot token via UI or `SLACK_BOT_TOKEN`.
- Endpoints: `GET /api/connectors/slack/auth/start`,
  `GET /api/connectors/slack/auth/callback`,
  `POST /api/connectors/slack/token`, `GET /api/connectors/slack/status`,
  `GET /api/connectors/slack/channels`, `POST /api/ingest/slack`.
- Pasted Slack exports work without OAuth through `POST /api/ingest/upload`
  with `source_type: slack_export`.

## Notion

- OAuth: create a Notion integration (public) with redirect
  `http://localhost:8000/api/connectors/notion/auth/callback`, set
  `NOTION_CLIENT_ID` and `NOTION_CLIENT_SECRET`.
- Sync pulls every page the integration can access (pages render as
  markdown-ish text with headings, lists, and quotes; databases are expanded
  row by row). Enqueue with `POST /api/connectors/notion/sync`; the worker
  streams large workspaces page by page.
- Read tools: `search`, `get_page`.

## Google Drive

- OAuth: reuse the Google OAuth client (`GOOGLE_CLIENT_ID` /
  `GOOGLE_CLIENT_SECRET`) and add the redirect
  `http://localhost:8000/api/connectors/google_drive/auth/callback`. Scopes:
  `drive.readonly`.
- Sync exports Google-native documents as text/CSV and downloads binaries
  (PDF, DOCX, XLSX, PPTX, …) into the shared document extractor. Files that
  fail to parse are recorded as job warnings instead of failing the batch.
- Read tools: `search_files`, `read_file`.

## Microsoft Teams

- OAuth: register an Azure AD app with redirect
  `http://localhost:8000/api/connectors/teams/auth/callback`, set
  `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, and (optionally)
  `MICROSOFT_TENANT_ID`. Scopes: `ChannelMessage.Read.All` plus
  `offline_access` for silent token refresh.
- Sync flattens every joined team's channel messages (HTML bodies converted
  to text) with author, channel, and permalink metadata.
- Read tools: `list_teams`, `get_channel_messages`.

## Websites and hosted documents

`POST /api/ingest/website` fetches any public URL and ingests it as a
`web_page` source. SSRF protections: the URL is re-resolved and re-validated
before every request and every redirect hop; private, loopback, and
special-use addresses are refused; response size and redirect depth are
capped. Hosted PDFs and office documents are parsed by the same extractor.

## Universal documents

`POST /api/ingest/upload` (JSON text) and `POST /api/ingest/file` (multipart)
accept Markdown, text, code, CSV/TSV, JSON, YAML, logs, and binary
documents: **PDF, DOCX, XLSX, PPTX, ODT, RTF, HTML, and EML**. Extraction is
stdlib-first (zip+XML for OOXML/OpenDocument, stdlib mail and HTML parsers)
with pypdf for PDFs; page, slide, and sheet markers are preserved so
retrieval citations point at exact locations. Content sniffing recovers
files uploaded with a wrong extension, and hard caps protect against
decompression bombs.

## Any platform with an API: custom REST sources

Workspace admins register a JSON-over-HTTPS endpoint with
`POST /api/connectors/custom/rest-sources`:

```json
{
  "name": "Linear issues",
  "base_url": "https://api.linear.app/v1/issues",
  "config": {
    "items_path": "data.issues",
    "id_field": "id", "title_field": "title",
    "content_fields": ["description"], "url_field": "url",
    "updated_field": "updated_at", "page_param": "page"
  },
  "headers": {"Authorization": "Bearer …"}
}
```

The platform becomes a first-class sync source through the same verified
sync engine as built-ins. Header credentials are encrypted under the
workspace scope and never returned through the API. Disable with
`CONNECTOR_REST_SOURCES_ENABLED=false`.

Custom remote MCP servers remain available via
`POST /api/connectors/custom/registrations`.

## Ingestion jobs

All repo/Slack/upload/website ingestion is job-recorded (`queued/running/
succeeded/failed/partial`) with files/issues/PRs scanned, graph nodes/edges
written, warnings, and errors: `GET /api/ingest/jobs`,
`GET /api/ingest/jobs/{id}`.

## Sync engine reliability

Connector sync jobs are durable rows with exponential-backoff retries and a
10-minute lease: a worker that dies mid-job has its job reclaimed
automatically, and the poller survives per-iteration failures.

## Planned connectors

Gmail, Microsoft 365 (SharePoint/OneDrive), Outlook, Atlassian (Jira and
Confluence), Linear, and ClickUp have registry entries that render honestly
in the UI as planned/not configured. No connector ever pretends to be
connected.

## Incident-tool importers

The migration layer (`app/importers`) is a connector class of its own:
PagerDuty has a live REST importer (incidents, services) activated by
`PAGERDUTY_API_TOKEN`; Rootly, incident.io, Opsgenie, Statuspage, Jira
Service Management, and ServiceNow expose the same
`IncidentToolImporter` interface and report `not_connected` /
not-implemented rather than faking imports. See `/api/importers`.
