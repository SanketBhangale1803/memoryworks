from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .config import settings


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:20]}"


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, repository TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
  auth_provider TEXT NOT NULL, external_id TEXT NOT NULL, role_hint TEXT NOT NULL DEFAULT 'member',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspaces (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspace_members (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
  role TEXT NOT NULL, status TEXT NOT NULL, invited_email TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(workspace_id, user_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS workspace_projects (
  workspace_id TEXT NOT NULL, project_id TEXT NOT NULL,
  PRIMARY KEY(workspace_id, project_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS teams (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, name TEXT NOT NULL,
  slug TEXT NOT NULL, parent_team_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(workspace_id, slug),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
  FOREIGN KEY(parent_team_id) REFERENCES teams(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS team_members (
  team_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'member',
  created_at TEXT NOT NULL, PRIMARY KEY(team_id, user_id),
  FOREIGN KEY(team_id) REFERENCES teams(id) ON DELETE CASCADE,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS project_teams (
  project_id TEXT NOT NULL, team_id TEXT NOT NULL, access_level TEXT NOT NULL DEFAULT 'read',
  created_at TEXT NOT NULL, PRIMARY KEY(project_id, team_id),
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
  FOREIGN KEY(team_id) REFERENCES teams(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, workspace_id TEXT NOT NULL DEFAULT '', token_hash TEXT NOT NULL UNIQUE,
  expires_at TEXT NOT NULL, created_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS knowledge_items (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, source_type TEXT NOT NULL,
  source_id TEXT NOT NULL, source_title TEXT NOT NULL, source_url TEXT NOT NULL,
  content TEXT NOT NULL, metadata_json TEXT NOT NULL, created_at TEXT NOT NULL,
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS runbooks (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, runbook_key TEXT NOT NULL,
  name TEXT NOT NULL, description TEXT NOT NULL, risk_level TEXT NOT NULL,
  confidence REAL NOT NULL, yaml_path TEXT NOT NULL, json_path TEXT NOT NULL,
  payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(project_id, runbook_key), FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS actions (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, runbook_id TEXT NOT NULL,
  step_id TEXT NOT NULL, action_type TEXT NOT NULL, summary TEXT NOT NULL,
  command_preview TEXT NOT NULL, params_json TEXT NOT NULL, decision TEXT NOT NULL,
  status TEXT NOT NULL, reason TEXT NOT NULL, risk_score INTEGER NOT NULL,
  requested_at TEXT NOT NULL, resolved_at TEXT, resolved_by TEXT
);
CREATE TABLE IF NOT EXISTS audit_events (
  id TEXT PRIMARY KEY, project_id TEXT, event_type TEXT NOT NULL, actor TEXT NOT NULL,
  summary TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS connector_accounts (
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT NOT NULL,
  display_name TEXT NOT NULL, status TEXT NOT NULL, secret_encrypted TEXT NOT NULL,
  metadata_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(provider, external_id)
);
CREATE TABLE IF NOT EXISTS oauth_states (
  state TEXT PRIMARY KEY, provider TEXT NOT NULL, expires_at TEXT NOT NULL, used_at TEXT
);
CREATE TABLE IF NOT EXISTS oauth_flows (
  state TEXT PRIMARY KEY, provider TEXT NOT NULL, intent TEXT NOT NULL,
  workspace_id TEXT NOT NULL DEFAULT '', user_id TEXT NOT NULL DEFAULT '',
  code_verifier TEXT NOT NULL DEFAULT '', expires_at TEXT NOT NULL, used_at TEXT
);
CREATE TABLE IF NOT EXISTS email_login_codes (
  id TEXT PRIMARY KEY, email TEXT NOT NULL, code_hash TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, expires_at TEXT NOT NULL,
  used_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspace_connector_accounts (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, user_id TEXT NOT NULL DEFAULT '',
  provider TEXT NOT NULL, external_id TEXT NOT NULL, display_name TEXT NOT NULL,
  status TEXT NOT NULL, secret_encrypted TEXT NOT NULL, metadata_json TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(workspace_id, provider, external_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS oauth_token_grants (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
  provider TEXT NOT NULL, external_id TEXT NOT NULL, display_name TEXT NOT NULL,
  status TEXT NOT NULL, access_token_encrypted TEXT NOT NULL,
  refresh_token_encrypted TEXT NOT NULL DEFAULT '', token_expires_at TEXT,
  scopes_json TEXT NOT NULL DEFAULT '[]', metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, revoked_at TEXT,
  UNIQUE(workspace_id, user_id, provider, external_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS connector_packages (
  provider TEXT PRIMARY KEY, package TEXT NOT NULL, version TEXT NOT NULL,
  manifest_digest TEXT NOT NULL, signing_key_id TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL, status TEXT NOT NULL, installed_at TEXT NOT NULL,
  revoked_at TEXT, revocation_reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS custom_connectors (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, created_by TEXT NOT NULL,
  provider TEXT NOT NULL, name TEXT NOT NULL, server_url TEXT NOT NULL,
  version TEXT NOT NULL, oauth_json TEXT NOT NULL DEFAULT '{}',
  manifest_json TEXT NOT NULL, manifest_digest TEXT NOT NULL,
  signing_key_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, revoked_at TEXT,
  UNIQUE(workspace_id, provider),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
  FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS connector_sync_jobs (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
  provider TEXT NOT NULL, resource_id TEXT NOT NULL, project_id TEXT NOT NULL DEFAULT '',
  cursor_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 5,
  next_attempt_at TEXT NOT NULL, last_error TEXT NOT NULL DEFAULT '',
  idempotency_key TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  completed_at TEXT, UNIQUE(workspace_id, provider, idempotency_key),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS background_jobs (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 2, error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, heartbeat_at TEXT,
  completed_at TEXT
);
CREATE INDEX IF NOT EXISTS background_jobs_due ON background_jobs(status, created_at);
CREATE TABLE IF NOT EXISTS answer_activity (
  id TEXT PRIMARY KEY, started_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS repository_refresh_requests (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
  project_id TEXT NOT NULL, repository TEXT NOT NULL, reason TEXT NOT NULL,
  idempotency_key TEXT NOT NULL, status TEXT NOT NULL,
  requested_at TEXT NOT NULL, resolved_at TEXT, resolved_by TEXT,
  started_at TEXT, completed_at TEXT, result_json TEXT NOT NULL DEFAULT '{}',
  error TEXT NOT NULL DEFAULT '',
  UNIQUE(workspace_id, user_id, project_id, idempotency_key),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS memory_proposals (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
  project_id TEXT NOT NULL, kind TEXT NOT NULL, subject TEXT NOT NULL,
  content TEXT NOT NULL, service TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL DEFAULT 'webmcp',
  idempotency_key TEXT NOT NULL, status TEXT NOT NULL,
  requested_at TEXT NOT NULL, resolved_at TEXT, resolved_by TEXT,
  memory_id TEXT NOT NULL DEFAULT '',
  UNIQUE(workspace_id, project_id, idempotency_key),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS connector_webhook_deliveries (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, provider TEXT NOT NULL,
  delivery_id TEXT NOT NULL, payload_hash TEXT NOT NULL, event_type TEXT NOT NULL,
  resource_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
  received_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(workspace_id, provider, delivery_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS connector_applied_records (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, provider TEXT NOT NULL,
  record_id TEXT NOT NULL, record_version TEXT NOT NULL, operation TEXT NOT NULL,
  content_hash TEXT NOT NULL, applied_at TEXT NOT NULL,
  UNIQUE(workspace_id, provider, record_id, record_version, operation),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS connector_tool_calls (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
  provider TEXT NOT NULL, tool_name TEXT NOT NULL, tool_kind TEXT NOT NULL,
  risk_level TEXT NOT NULL, arguments_encrypted TEXT NOT NULL,
  argument_summary_json TEXT NOT NULL DEFAULT '{}', idempotency_key TEXT NOT NULL,
  status TEXT NOT NULL, requested_at TEXT NOT NULL, resolved_at TEXT,
  resolved_by TEXT, executed_at TEXT, result_json TEXT NOT NULL DEFAULT '{}',
  error TEXT NOT NULL DEFAULT '',
  UNIQUE(workspace_id, provider, tool_name, idempotency_key),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS mcp_oauth_clients (
  id TEXT PRIMARY KEY, client_id TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  client_secret_hash TEXT NOT NULL DEFAULT '', redirect_uris_json TEXT NOT NULL,
  allowed_scopes_json TEXT NOT NULL, created_by TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS mcp_oauth_codes (
  code_hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, user_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL, redirect_uri TEXT NOT NULL, scopes_json TEXT NOT NULL,
  code_challenge TEXT NOT NULL, expires_at TEXT NOT NULL, used_at TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mcp_refresh_tokens (
  token_hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, user_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL, scopes_json TEXT NOT NULL, expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL, revoked_at TEXT, rotated_to_hash TEXT
);
CREATE TABLE IF NOT EXISTS ingestion_jobs (
  id TEXT PRIMARY KEY, project_id TEXT, workspace_id TEXT, source TEXT NOT NULL,
  source_ref TEXT NOT NULL, status TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
  files_scanned INTEGER NOT NULL DEFAULT 0, issues_scanned INTEGER NOT NULL DEFAULT 0,
  pull_requests_scanned INTEGER NOT NULL DEFAULT 0, knowledge_items_created INTEGER NOT NULL DEFAULT 0,
  knowledge_chunks_created INTEGER NOT NULL DEFAULT 0, graph_nodes_created INTEGER NOT NULL DEFAULT 0,
  graph_edges_created INTEGER NOT NULL DEFAULT 0, warnings_json TEXT NOT NULL DEFAULT '[]',
  error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  completed_at TEXT, result_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS operational_memories (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, statement TEXT NOT NULL,
  memory_type TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'project',
  owner TEXT NOT NULL DEFAULT '', approval_policy TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL, status TEXT NOT NULL,
  source_item_ids_json TEXT NOT NULL DEFAULT '[]',
  evidence_json TEXT NOT NULL DEFAULT '[]',
  last_verified TEXT NOT NULL, approved_by TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_units (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL DEFAULT '', project_id TEXT NOT NULL,
  type TEXT NOT NULL, subject TEXT NOT NULL, content TEXT NOT NULL,
  scope_json TEXT NOT NULL DEFAULT '{}', source_ids_json TEXT NOT NULL DEFAULT '[]',
  confidence REAL NOT NULL, valid_from TEXT NOT NULL, valid_to TEXT,
  is_latest INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS memory_relationships (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, from_memory_id TEXT NOT NULL,
  to_memory_id TEXT NOT NULL, relationship TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(from_memory_id, to_memory_id, relationship)
);
CREATE TABLE IF NOT EXISTS beliefs (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL DEFAULT '', project_id TEXT NOT NULL,
  claim TEXT NOT NULL, claim_key TEXT NOT NULL, current_value TEXT NOT NULL,
  previous_value TEXT, confidence REAL NOT NULL, valid_from TEXT NOT NULL,
  valid_until TEXT, scope_json TEXT NOT NULL DEFAULT '{}', scope_key TEXT NOT NULL,
  authority_tier TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS belief_evidence (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, belief_id TEXT NOT NULL,
  source_type TEXT NOT NULL, source_id TEXT NOT NULL, source_timestamp TEXT NOT NULL,
  confidence REAL NOT NULL, role TEXT NOT NULL DEFAULT 'supporting',
  metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
  UNIQUE(belief_id, source_type, source_id, role),
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
  FOREIGN KEY(belief_id) REFERENCES beliefs(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS belief_relationships (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, from_belief_id TEXT NOT NULL,
  to_belief_id TEXT NOT NULL, relationship TEXT NOT NULL, evidence_id TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
  UNIQUE(from_belief_id, to_belief_id, relationship),
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
  FOREIGN KEY(from_belief_id) REFERENCES beliefs(id) ON DELETE CASCADE,
  FOREIGN KEY(to_belief_id) REFERENCES beliefs(id) ON DELETE CASCADE,
  FOREIGN KEY(evidence_id) REFERENCES belief_evidence(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS semantic_change_events (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, delivery_id TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL, repository TEXT NOT NULL, commit_sha TEXT NOT NULL DEFAULT '',
  source_url TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'observed',
  payload_json TEXT NOT NULL DEFAULT '{}', interpretation_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT,
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS source_revisions (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, source_id TEXT NOT NULL,
  source_type TEXT NOT NULL, source_title TEXT NOT NULL, version INTEGER NOT NULL,
  content_hash TEXT NOT NULL, content TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'current', supersedes_revision_id TEXT,
  created_at TEXT NOT NULL, UNIQUE(project_id, source_id, version),
  UNIQUE(project_id, source_id, content_hash),
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS memory_change_sets (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, source_id TEXT NOT NULL,
  source_revision_id TEXT NOT NULL, actor TEXT NOT NULL DEFAULT 'ingestion',
  added_json TEXT NOT NULL DEFAULT '[]', updated_json TEXT NOT NULL DEFAULT '[]',
  invalidated_json TEXT NOT NULL DEFAULT '[]', conflicts_json TEXT NOT NULL DEFAULT '[]',
  affected_profiles_json TEXT NOT NULL DEFAULT '[]', affected_artifacts_json TEXT NOT NULL DEFAULT '[]',
  affected_skills_json TEXT NOT NULL DEFAULT '[]', review_status TEXT NOT NULL DEFAULT 'automatic',
  created_at TEXT NOT NULL,
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
  FOREIGN KEY(source_revision_id) REFERENCES source_revisions(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS source_scopes (
  source_id TEXT NOT NULL, team_id TEXT NOT NULL, project_id TEXT NOT NULL,
  created_at TEXT NOT NULL, PRIMARY KEY(source_id, team_id),
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
  FOREIGN KEY(team_id) REFERENCES teams(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS memory_scopes (
  memory_id TEXT NOT NULL, team_id TEXT NOT NULL, project_id TEXT NOT NULL,
  created_at TEXT NOT NULL, PRIMARY KEY(memory_id, team_id),
  FOREIGN KEY(memory_id) REFERENCES memory_units(id) ON DELETE CASCADE,
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
  FOREIGN KEY(team_id) REFERENCES teams(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS context_envelopes (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, principal_id TEXT NOT NULL DEFAULT '',
  query TEXT NOT NULL, task_type TEXT NOT NULL, authorized_team_ids_json TEXT NOT NULL DEFAULT '[]',
  target_entities_json TEXT NOT NULL DEFAULT '[]', memory_ids_json TEXT NOT NULL DEFAULT '[]',
  evidence_ids_json TEXT NOT NULL DEFAULT '[]', skill_ids_json TEXT NOT NULL DEFAULT '[]',
  source_version_vector_json TEXT NOT NULL DEFAULT '{}', retrieval_trace_json TEXT NOT NULL DEFAULT '{}',
  compiled_context_json TEXT NOT NULL DEFAULT '{}',
  activation_run_ids_json TEXT NOT NULL DEFAULT '[]',
  token_budget INTEGER NOT NULL DEFAULT 6000, expires_at TEXT NOT NULL, created_at TEXT NOT NULL,
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS context_activation_runs (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, principal_id TEXT NOT NULL DEFAULT '',
  query TEXT NOT NULL, status TEXT NOT NULL, token_budget INTEGER NOT NULL,
  selected_evidence_ids_json TEXT NOT NULL DEFAULT '[]',
  compiled_context_json TEXT NOT NULL DEFAULT '{}',
  agent_reports_json TEXT NOT NULL DEFAULT '[]',
  trace_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, completed_at TEXT NOT NULL,
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, artifact_type TEXT NOT NULL,
  current_revision_id TEXT, status TEXT NOT NULL DEFAULT 'current',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(project_id, artifact_type, name),
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS artifact_revisions (
  id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL, project_id TEXT NOT NULL,
  version INTEGER NOT NULL, content TEXT NOT NULL, content_hash TEXT NOT NULL,
  source_ids_json TEXT NOT NULL DEFAULT '[]', memory_ids_json TEXT NOT NULL DEFAULT '[]',
  context_envelope_id TEXT, status TEXT NOT NULL DEFAULT 'current', created_at TEXT NOT NULL,
  UNIQUE(artifact_id, version), UNIQUE(artifact_id, content_hash),
  FOREIGN KEY(artifact_id) REFERENCES artifacts(id) ON DELETE CASCADE,
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS artifact_impacts (
  id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL, artifact_revision_id TEXT,
  change_set_id TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'needs_review',
  created_at TEXT NOT NULL,
  FOREIGN KEY(artifact_id) REFERENCES artifacts(id) ON DELETE CASCADE,
  FOREIGN KEY(change_set_id) REFERENCES memory_change_sets(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS skill_specs (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, version INTEGER NOT NULL,
  team_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'current',
  spec_json TEXT NOT NULL, memory_ids_json TEXT NOT NULL DEFAULT '[]',
  source_version_vector_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
  UNIQUE(project_id, name, team_id, version),
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS project_context_states (
  project_id TEXT PRIMARY KEY, active_domain TEXT NOT NULL DEFAULT '',
  active_subdomain TEXT NOT NULL DEFAULT '', context_window TEXT NOT NULL DEFAULT '',
  active_service TEXT NOT NULL DEFAULT '', boundary_type TEXT NOT NULL DEFAULT 'none',
  query_type TEXT NOT NULL DEFAULT 'single_hop', query_count INTEGER NOT NULL DEFAULT 0,
  last_query TEXT NOT NULL DEFAULT '', resolved_query TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS memory_dynamics (
  project_id TEXT NOT NULL, source_id TEXT NOT NULL, content_hash TEXT NOT NULL,
  fast_weight REAL NOT NULL DEFAULT 0, slow_weight REAL NOT NULL DEFAULT 0,
  reinforcement_count INTEGER NOT NULL DEFAULT 0,
  first_seen_at TEXT NOT NULL, last_reinforced_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY(project_id, source_id, content_hash),
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS operational_assertions (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL, claim TEXT NOT NULL,
  subject_type TEXT NOT NULL, subject_id TEXT NOT NULL, environment_scope TEXT NOT NULL DEFAULT 'unknown',
  status TEXT NOT NULL, confidence REAL NOT NULL, trust_score REAL NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_verified_at TEXT,
  valid_from TEXT, valid_to TEXT, source_version TEXT NOT NULL DEFAULT '', commit_sha TEXT NOT NULL DEFAULT '',
  source_updated_at TEXT, verification_owner TEXT NOT NULL DEFAULT '', verification_reason TEXT NOT NULL DEFAULT '',
  evidence_json TEXT NOT NULL DEFAULT '[]', affected_runbook_ids_json TEXT NOT NULL DEFAULT '[]',
  affected_runbook_step_ids_json TEXT NOT NULL DEFAULT '[]', approval_requirement TEXT NOT NULL DEFAULT 'human_review_required',
  policy_status TEXT NOT NULL DEFAULT 'unverified'
);
CREATE TABLE IF NOT EXISTS change_impacts (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, change_type TEXT NOT NULL, change_ref TEXT NOT NULL,
  status TEXT NOT NULL, severity TEXT NOT NULL, summary TEXT NOT NULL, payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL DEFAULT '', name TEXT NOT NULL,
  key_prefix TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE,
  created_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
  last_used_at TEXT, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS memory_work (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL DEFAULT '', project_id TEXT NOT NULL,
  objective TEXT NOT NULL, status TEXT NOT NULL, requested_by TEXT NOT NULL DEFAULT '',
  target_connector TEXT NOT NULL DEFAULT '', context_envelope_id TEXT NOT NULL DEFAULT '',
  artifact_id TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL DEFAULT 0,
  context_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS memory_work_steps (
  id TEXT PRIMARY KEY, work_id TEXT NOT NULL, position INTEGER NOT NULL,
  step_type TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL,
  status TEXT NOT NULL, approval_required INTEGER NOT NULL DEFAULT 0,
  risk_level TEXT NOT NULL DEFAULT 'low', connector TEXT NOT NULL DEFAULT '',
  input_json TEXT NOT NULL DEFAULT '{}', output_json TEXT NOT NULL DEFAULT '{}',
  resolved_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(work_id, position),
  FOREIGN KEY(work_id) REFERENCES memory_work(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS memory_work_events (
  id TEXT PRIMARY KEY, work_id TEXT NOT NULL, event_type TEXT NOT NULL,
  summary TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
  FOREIGN KEY(work_id) REFERENCES memory_work(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS context_events (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL DEFAULT '', project_id TEXT NOT NULL,
  principal_id TEXT NOT NULL DEFAULT '', surface TEXT NOT NULL DEFAULT 'api',
  query TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '',
  answer_scope TEXT NOT NULL DEFAULT '', answer_kind TEXT NOT NULL DEFAULT '',
  answer_sufficient INTEGER NOT NULL DEFAULT 0,
  model_provider TEXT NOT NULL DEFAULT '', selected_lens TEXT NOT NULL DEFAULT '',
  candidate_count INTEGER NOT NULL DEFAULT 0, judged INTEGER NOT NULL DEFAULT 0,
  candidates_json TEXT NOT NULL DEFAULT '[]',
  evidence_ids_json TEXT NOT NULL DEFAULT '[]', evidence_count INTEGER NOT NULL DEFAULT 0,
  source_ids_json TEXT NOT NULL DEFAULT '[]',
  confidence REAL NOT NULL DEFAULT 0, trust_score REAL NOT NULL DEFAULT 0,
  context_envelope_id TEXT NOT NULL DEFAULT '',
  handoff_offered INTEGER NOT NULL DEFAULT 0, handoff_files_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS action_events (
  id TEXT PRIMARY KEY, context_event_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL DEFAULT '', project_id TEXT NOT NULL DEFAULT '',
  action_type TEXT NOT NULL, actor TEXT NOT NULL DEFAULT '',
  surface TEXT NOT NULL DEFAULT '', target TEXT NOT NULL DEFAULT '',
  detail_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
  FOREIGN KEY(context_event_id) REFERENCES context_events(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS outcome_events (
  id TEXT PRIMARY KEY, context_event_id TEXT NOT NULL, action_event_id TEXT,
  workspace_id TEXT NOT NULL DEFAULT '', project_id TEXT NOT NULL DEFAULT '',
  outcome TEXT NOT NULL, signal TEXT NOT NULL DEFAULT 'human',
  reason TEXT NOT NULL DEFAULT '', detail_json TEXT NOT NULL DEFAULT '{}',
  observed_at TEXT NOT NULL, created_at TEXT NOT NULL,
  FOREIGN KEY(context_event_id) REFERENCES context_events(id) ON DELETE CASCADE,
  FOREIGN KEY(action_event_id) REFERENCES action_events(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS learned_skills (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL DEFAULT '', project_id TEXT NOT NULL,
  name TEXT NOT NULL, trigger TEXT NOT NULL, trigger_tokens_json TEXT NOT NULL DEFAULT '[]',
  approach TEXT NOT NULL DEFAULT '', files_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'proposed',
  uses INTEGER NOT NULL DEFAULT 0, successes INTEGER NOT NULL DEFAULT 0,
  failures INTEGER NOT NULL DEFAULT 0, confidence REAL NOT NULL DEFAULT 0,
  run_ids_json TEXT NOT NULL DEFAULT '[]', context_ids_json TEXT NOT NULL DEFAULT '[]',
  commits_json TEXT NOT NULL DEFAULT '[]', retired_reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_learned_skills_project
  ON learned_skills(project_id, status, confidence);
CREATE INDEX IF NOT EXISTS idx_learned_skills_workspace
  ON learned_skills(workspace_id, status);
CREATE TABLE IF NOT EXISTS execution_runs (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL DEFAULT '', project_id TEXT NOT NULL,
  context_event_id TEXT NOT NULL DEFAULT '', executor TEXT NOT NULL,
  status TEXT NOT NULL, task TEXT NOT NULL, prompt TEXT NOT NULL,
  repository TEXT NOT NULL DEFAULT '', worktree TEXT NOT NULL DEFAULT '',
  branch TEXT NOT NULL DEFAULT '', base_branch TEXT NOT NULL DEFAULT '',
  commit_sha TEXT NOT NULL DEFAULT '', files_changed_json TEXT NOT NULL DEFAULT '[]',
  diff_stat TEXT NOT NULL DEFAULT '', diff TEXT NOT NULL DEFAULT '',
  agent_output TEXT NOT NULL DEFAULT '', skill_ids_json TEXT NOT NULL DEFAULT '[]',
  pushed INTEGER NOT NULL DEFAULT 0,
  pull_request_url TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
  requested_by TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_execution_runs_project
  ON execution_runs(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_execution_runs_workspace
  ON execution_runs(workspace_id, created_at);
CREATE INDEX IF NOT EXISTS idx_context_events_project
  ON context_events(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_context_events_workspace
  ON context_events(workspace_id, created_at);
CREATE INDEX IF NOT EXISTS idx_action_events_context ON action_events(context_event_id);
CREATE INDEX IF NOT EXISTS idx_outcome_events_context ON outcome_events(context_event_id);
CREATE INDEX IF NOT EXISTS idx_items_project ON knowledge_items(project_id);
CREATE INDEX IF NOT EXISTS idx_memories_project ON operational_memories(project_id, status);
CREATE INDEX IF NOT EXISTS idx_memory_units_project ON memory_units(project_id, is_latest, type);
CREATE INDEX IF NOT EXISTS idx_memory_units_subject ON memory_units(project_id, subject);
CREATE INDEX IF NOT EXISTS idx_memory_relationships_project ON memory_relationships(project_id, relationship);
CREATE INDEX IF NOT EXISTS idx_beliefs_current ON beliefs(project_id, claim_key, scope_key, status, valid_until);
CREATE INDEX IF NOT EXISTS idx_belief_evidence_belief ON belief_evidence(belief_id, role);
CREATE INDEX IF NOT EXISTS idx_belief_relationships_project ON belief_relationships(project_id, relationship);
CREATE INDEX IF NOT EXISTS idx_semantic_changes_project ON semantic_change_events(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_context_states_updated ON project_context_states(updated_at);
CREATE INDEX IF NOT EXISTS idx_memory_dynamics_source ON memory_dynamics(project_id, source_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_assertions_project ON operational_assertions(project_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_assertions_subject ON operational_assertions(project_id, subject_type, subject_id);
CREATE INDEX IF NOT EXISTS idx_impacts_project ON change_impacts(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_runbooks_project ON runbooks(project_id);
CREATE INDEX IF NOT EXISTS idx_audit_project ON audit_events(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_project ON ingestion_jobs(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_workspace_members ON workspace_members(workspace_id, user_id);
CREATE INDEX IF NOT EXISTS idx_workspace_connectors ON workspace_connector_accounts(workspace_id, provider, status);
CREATE INDEX IF NOT EXISTS idx_oauth_grants_user ON oauth_token_grants(workspace_id,user_id,provider,status);
CREATE INDEX IF NOT EXISTS idx_connector_sync_due ON connector_sync_jobs(status,next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_repository_refresh_requests ON repository_refresh_requests(workspace_id,status,requested_at);
CREATE INDEX IF NOT EXISTS idx_connector_tool_calls_status ON connector_tool_calls(workspace_id,status,requested_at);
CREATE INDEX IF NOT EXISTS idx_email_login_codes ON email_login_codes(email, created_at);
CREATE INDEX IF NOT EXISTS idx_team_members_user ON team_members(user_id, team_id);
CREATE INDEX IF NOT EXISTS idx_project_teams_project ON project_teams(project_id, team_id);
CREATE INDEX IF NOT EXISTS idx_source_revisions_source ON source_revisions(project_id, source_id, version);
CREATE INDEX IF NOT EXISTS idx_change_sets_project ON memory_change_sets(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_source_scopes_project ON source_scopes(project_id, source_id);
CREATE INDEX IF NOT EXISTS idx_memory_scopes_project ON memory_scopes(project_id, memory_id);
CREATE INDEX IF NOT EXISTS idx_context_envelopes_project ON context_envelopes(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_context_activation_runs_project
  ON context_activation_runs(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_artifacts_project ON artifacts(project_id, status);
CREATE INDEX IF NOT EXISTS idx_skill_specs_project ON skill_specs(project_id, status);
CREATE TABLE IF NOT EXISTS org_tasks (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL DEFAULT '', project_id TEXT NOT NULL,
  title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open', owner TEXT NOT NULL DEFAULT '',
  priority TEXT NOT NULL DEFAULT 'normal', kind TEXT NOT NULL DEFAULT 'task',
  source_memory_ids_json TEXT NOT NULL DEFAULT '[]',
  depends_on_json TEXT NOT NULL DEFAULT '[]',
  external_key TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS org_watches (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, name TEXT NOT NULL,
  space_ids_json TEXT NOT NULL DEFAULT '[]', checks_json TEXT NOT NULL DEFAULT '[]',
  interval_seconds INTEGER NOT NULL DEFAULT 900, status TEXT NOT NULL DEFAULT 'active',
  created_by TEXT NOT NULL DEFAULT '', last_run_at TEXT, last_error TEXT NOT NULL DEFAULT '',
  runs INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS org_watch_findings (
  id TEXT PRIMARY KEY, watch_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
  kind TEXT NOT NULL, headline TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL DEFAULT '{}', fingerprint TEXT NOT NULL,
  plan_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL, resolved_at TEXT,
  UNIQUE(watch_id, fingerprint),
  FOREIGN KEY(watch_id) REFERENCES org_watches(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS org_action_plans (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, project_id TEXT NOT NULL DEFAULT '',
  created_by TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL DEFAULT 'webmcp',
  summary TEXT NOT NULL DEFAULT '', operations_json TEXT NOT NULL DEFAULT '[]',
  results_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'pending_approval',
  created_at TEXT NOT NULL, resolved_at TEXT, resolved_by TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_memory_work_project ON memory_work(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_memory_work_steps ON memory_work_steps(work_id, position);
CREATE INDEX IF NOT EXISTS idx_memory_work_events ON memory_work_events(work_id, created_at);
CREATE INDEX IF NOT EXISTS idx_org_tasks_project ON org_tasks(project_id, status);
CREATE INDEX IF NOT EXISTS idx_org_tasks_external ON org_tasks(project_id, external_key);
CREATE INDEX IF NOT EXISTS idx_org_action_plans ON org_action_plans(workspace_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_org_watches ON org_watches(workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_org_watch_findings ON org_watch_findings(watch_id, status, created_at);
CREATE TABLE IF NOT EXISTS live_issues (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  subject TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  source_ids_json TEXT NOT NULL DEFAULT '[]',
  scope_constraints_json TEXT NOT NULL DEFAULT '[]',
  severity TEXT NOT NULL CHECK(severity IN ('info','warning','error','critical')),
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  occurrences INTEGER NOT NULL DEFAULT 0 CHECK(occurrences >= 0),
  status TEXT NOT NULL CHECK(status IN ('open','resolved','muted')),
  latest_signal_id TEXT NOT NULL DEFAULT '',
  resolved_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(workspace_id,fingerprint)
);
CREATE TABLE IF NOT EXISTS live_signals (
  id TEXT PRIMARY KEY,
  issue_id TEXT REFERENCES live_issues(id) ON DELETE CASCADE,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  subject TEXT NOT NULL,
  severity TEXT NOT NULL CHECK(severity IN ('info','warning','error','critical')),
  state TEXT NOT NULL CHECK(state IN ('failure','success')),
  observed_at TEXT NOT NULL,
  collected_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  evidence_url TEXT NOT NULL DEFAULT '',
  fingerprint TEXT NOT NULL,
  observation_key TEXT NOT NULL,
  generation_at TEXT NOT NULL,
  generation_id INTEGER NOT NULL DEFAULT 0,
  attempt INTEGER NOT NULL DEFAULT 1,
  source_ids_json TEXT NOT NULL DEFAULT '[]',
  source_grants_json TEXT NOT NULL DEFAULT '{}',
  details_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(workspace_id,fingerprint,observation_key)
);
CREATE INDEX IF NOT EXISTS live_issues_scope_status
  ON live_issues(workspace_id,project_id,status,last_seen);
CREATE INDEX IF NOT EXISTS live_signals_issue_time
  ON live_signals(issue_id,observed_at);
CREATE INDEX IF NOT EXISTS live_signals_freshness
  ON live_signals(workspace_id,project_id,expires_at);
"""


# Schema creation is expensive (a 600-line executescript plus PRAGMA introspection)
# and must not repeat for every connection. Keyed by resolved path so a test (or a
# re-pointed deployment) that swaps sqlite_path re-initializes for the new file.
_initialized_paths: set[str] = set()


def init_db() -> None:
    db_path = settings.sqlite_path
    resolved = str(db_path.resolve())
    if resolved in _initialized_paths:
        return
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Multiple processes can cold-start against a fresh database at the same
    # moment (parallel test collection, horizontally scaled instances). Schema
    # creation retries through the transient "database is locked" window that
    # a first-time WAL setup can produce.
    last_error: sqlite3.OperationalError | None = None
    for attempt in range(5):
        try:
            # closing() as well as the transaction context: sqlite3's own context
            # manager commits but never closes, so the bare form leaks a
            # descriptor per call.
            with closing(sqlite3.connect(db_path, timeout=30.0)) as conn, conn:
                conn.execute("PRAGMA busy_timeout=30000")
                conn.executescript(SCHEMA)
                _migrate_columns(conn)
            _initialized_paths.add(resolved)
            return
        except sqlite3.OperationalError as exc:
            last_error = exc
            if "locked" not in str(exc).casefold() and "busy" not in str(exc).casefold():
                raise
            time.sleep(0.25 * (attempt + 1))
    raise last_error if last_error else RuntimeError("init_db failed")


def _migrate_columns(conn: sqlite3.Connection) -> None:
    session_columns = {
        record[1] for record in conn.execute("PRAGMA table_info(sessions)").fetchall()
    }
    if "workspace_id" not in session_columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN workspace_id TEXT NOT NULL DEFAULT ''")
    envelope_columns = {
        record[1] for record in conn.execute("PRAGMA table_info(context_envelopes)").fetchall()
    }
    if "compiled_context_json" not in envelope_columns:
        conn.execute(
            "ALTER TABLE context_envelopes "
            "ADD COLUMN compiled_context_json TEXT NOT NULL DEFAULT '{}'"
        )
    if "activation_run_ids_json" not in envelope_columns:
        conn.execute(
            "ALTER TABLE context_envelopes "
            "ADD COLUMN activation_run_ids_json TEXT NOT NULL DEFAULT '[]'"
        )
    # CREATE TABLE IF NOT EXISTS is a no-op once a table exists, so a column
    # added to the schema above never reaches a database that already has it.
    # Tests run on fresh files and cannot catch this; only a real deployment can.
    run_columns = {
        record[1] for record in conn.execute("PRAGMA table_info(execution_runs)").fetchall()
    }
    if run_columns and "skill_ids_json" not in run_columns:
        conn.execute(
            "ALTER TABLE execution_runs ADD COLUMN skill_ids_json TEXT NOT NULL DEFAULT '[]'"
        )
    job_columns = {
        record[1] for record in conn.execute("PRAGMA table_info(ingestion_jobs)").fetchall()
    }
    if job_columns and "result_json" not in job_columns:
        conn.execute("ALTER TABLE ingestion_jobs ADD COLUMN result_json TEXT NOT NULL DEFAULT '{}'")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    init_db()
    conn = sqlite3.connect(settings.sqlite_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    # WAL allows concurrent readers alongside one writer; NORMAL is the durable
    # pairing (fsync only at checkpoints). busy_timeout keeps short multi-process
    # write contention from surfacing as "database is locked" errors.
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def row(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    result = rows(sql, params)
    return result[0] if result else None


def decode(record: dict[str, Any]) -> dict[str, Any]:
    output = dict(record)
    for key in list(output):
        if key.endswith("_json"):
            output[key.removesuffix("_json")] = json.loads(output.pop(key) or "{}")
    return output
