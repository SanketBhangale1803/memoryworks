/* Shapes shared by the pages. They mirror what the backend's JSON API returns
 * for the resources more than one page reads. Fields are optional where a
 * record may come from an older row or an endpoint that trims payloads. */

export type AuthWorkspace = {
  id: string;
  name: string;
  slug: string;
  role: string;
};

export type AuthUser = {
  id: string;
  email: string;
  display_name?: string;
  auth_provider?: string;
  active_workspace_id?: string;
  role?: string;
  workspaces?: AuthWorkspace[];
};

export type Project = {
  id: string;
  name: string;
  repository?: string;
  knowledge_items?: number;
  runbooks?: number;
  created_at?: string;
};

export type WorkspaceMember = {
  id: string;
  user_id?: string;
  email: string;
  display_name?: string;
  role: string;
  status: string;
};

export type Team = {
  id: string;
  name: string;
  member_count?: number;
  project_count?: number;
};

export type ApiKey = {
  id: string;
  name: string;
  key_prefix: string;
  status: string;
  created_at?: string;
  last_used_at?: string;
  revoked_at?: string;
};

export type CreatedApiKey = ApiKey & { api_key: string };

export type ConnectorAccount = {
  display_name?: string;
  updated_at?: string;
  status?: string;
};

export type ConnectorManifestTool = {
  name?: string;
  kind?: string;
  label?: string;
};

export type ConnectorManifest = {
  name?: string;
  resources?: { label?: string }[];
  tools?: ConnectorManifestTool[];
};

export type Connector = {
  provider: string;
  connected?: boolean;
  available?: boolean;
  accounts?: ConnectorAccount[];
  manifest?: ConnectorManifest;
};

export type ConnectorCatalogEntry = {
  provider: string;
  label: string;
  category?: string;
  memory?: string[];
  status?: string;
};

export type ConnectorCoverageSource = {
  provider: string;
  connected?: boolean;
  indexed?: Record<string, unknown>;
  refresh_mode?: string;
};

export type ConnectorCoverage = {
  sources?: ConnectorCoverageSource[];
};

export type GitHubRepository = {
  id: string;
  name: string;
  full_name: string;
  clone_url: string;
  private?: boolean;
};

export type SlackChannel = {
  id: string;
  name: string;
  is_private?: boolean;
};

export type IngestJob = {
  id: string;
  source?: string;
  source_ref?: string;
  status: string;
  progress?: number;
  error?: string;
  knowledge_items_created?: number;
  knowledge_chunks_created?: number;
  files_scanned?: number;
  issues_scanned?: number;
  pull_requests_scanned?: number;
  graph_nodes_created?: number;
  graph_edges_created?: number;
  updated_at?: string;
  result?: Record<string, unknown>;
};

export type ConnectorSyncJob = {
  id: string;
  status: string;
  last_error?: string;
  cursor?: {
    offset?: number;
    failures?: string[];
  };
};

export type RuntimeSettings = {
  graph_backend?: string;
  arcadedb_database?: string;
  hcag_enabled?: boolean;
  auth_dev_mode?: boolean;
  environment?: string;
  github_oauth_configured?: boolean;
  slack_oauth_configured?: boolean;
  github_live_updates?: boolean;
  slack_live_updates?: boolean;
  mcp_http_url?: string;
};

export type AuditEvent = {
  id: string;
  summary: string;
  event_type?: string;
  actor?: string;
  project_id?: string;
  created_at?: string;
};

/* A sourced memory shown under an answer or in an Agent's evidence panel. */
export type MemoryUnit = {
  id: string;
  type: string;
  content: string;
  title?: string;
  subject?: string;
  confidence?: number;
  space_name?: string;
};

export type EvidenceSource = {
  id?: string;
  url?: string;
  title?: string;
  type?: string;
  status?: string;
};

export type EvidencePanelState = {
  loading?: boolean;
  error?: string;
  memory?: MemoryUnit;
  sources?: EvidenceSource[];
};
