from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
from pathlib import Path
from threading import Thread
from urllib.parse import urlencode

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import RedirectResponse

from app.approvals import ApprovalService
from app.audit import AuditService
from app.auth import (
    ConnectorSecrets,
    OAuthStateStore,
    complete_google_oauth,
    google_oauth_url,
)
from app.auth.api_keys import (
    create_api_key,
    list_api_keys,
    revoke_api_key,
    verify_api_key,
)
from app.auth.app_auth import (
    bearer_token,
    create_dev_session,
    create_oauth_session,
    create_workspace,
    invite_member,
    issue_public_demo_session,
    list_workspaces,
    logout,
    me_from_token,
    request_email_login_code,
    send_invite_email,
    verify_email_login_code,
    workspace_members,
)
from app.auth.mcp_oauth import principal_from_mcp_token, register_mcp_client
from app.auth.security import frontend_redirect, oauth_redirect_uri
from app.company_context import CompanyContextService
from app.connectors.application import MemoryWorksSyncApplier
from app.connectors.base import WebhookRequest
from app.connectors.github import GitHubConnector
from app.connectors.runtime import ConnectorRuntime
from app.connectors.slack import SlackConnector
from app.connectors.stubs.registry import connector_catalog as product_connector_catalog
from app.connectors.sync import SyncEngine
from app.core.config import settings
from app.core.database import connect, decode, new_id, row, rows, utcnow
from app.execution import ExecutionError, available_executors
from app.execution import execute as execute_run
from app.execution import get as get_execution_run
from app.execution import list_runs as list_execution_runs
from app.execution import start as start_execution_run
from app.governance import ScopeService
from app.graph import get_graph_store
from app.hcag_adapter import HCAGAdapter
from app.importers import NotConnectedError, get_importer, importer_statuses
from app.ingestion.documents import (
    SUPPORTED_UPLOAD_SUFFIXES,
    UnsupportedDocumentError,
    extract_document,
)
from app.ingestion.maintenance import (
    rebuild_atomic_memories_from_index,
    rebuild_services_from_index,
    reset_project_derived_memory,
)
from app.ingestion.repository import RepositoryIngestor
from app.ingestion.service import IngestionService
from app.ingestion.slack import SlackIngestor
from app.ingestion.web import WebFetchError, fetch_web_document
from app.intelligence import (
    DriftService,
    SimulationService,
    blast_radius,
    correlate_changes,
)
from app.llm import model_catalog
from app.llm.providers import configured_model
from app.memory import (
    ChangeIntelligenceService,
    CompanyBrainService,
    CompanyMemoryService,
    OperationalMemoryService,
    briefing,
)
from app.memory.change_intelligence import github_diff
from app.memory.company import MEMORY_TYPES
from app.orgops.seed import seed_launch_scenario
from app.outcomes import (
    export_training_records,
    record_action,
    record_context,
    record_outcome,
)
from app.outcomes import stats as outcome_stats
from app.reliability import ChangeImpactService, OperationalAssertionService
from app.retrieval import RetrievalService
from app.retrieval.memory_search import rank as rank_memories
from app.runbooks import RunbookService
from app.skills import get as get_learned_skill
from app.skills import list_skills as list_learned_skills
from app.skills import matches as learned_skill_matches
from app.skills import retire as retire_learned_skill_record
from app.webmcp_agent import AgentSessionStore, WebMCPAgentRunner
from app.work import MemoryWorkService

from .schemas import (
    ActionRecordRequest,
    AgentSessionRequest,
    ApiKeyCreateRequest,
    ArtifactSaveRequest,
    AskRequest,
    AssertionDecisionRequest,
    BriefingOutcomeRequest,
    BriefingRequest,
    BulkAssertionReviewRequest,
    ChangeImpactAnalyzeRequest,
    ConnectorSyncRequest,
    ConnectorToolInvokeRequest,
    ConnectorToolResolveRequest,
    CorrelateRequest,
    CustomConnectorCreateRequest,
    DemoLoginRequest,
    DevLoginRequest,
    EmailCodeRequest,
    EmailCodeVerifyRequest,
    ExecuteRequest,
    ExtractRequest,
    GitHubBulkIngestRequest,
    GitHubIngestRequest,
    ImporterRunRequest,
    InviteMemberRequest,
    MCPOAuthClientCreateRequest,
    MemoryProposalRequest,
    MemoryProposalResolutionRequest,
    MemoryRepairRequest,
    MemoryResolveRequest,
    MemoryWorkCompleteRequest,
    MemoryWorkCreateRequest,
    MemoryWorkResolveRequest,
    OutcomeRecordRequest,
    ProjectCreateRequest,
    ProjectTeamRequest,
    ProposeRequest,
    RepositoryRefreshProposalRequest,
    RepositoryRefreshResolutionRequest,
    ResolveRequest,
    RestSourceCreateRequest,
    SemanticChangeInterpretRequest,
    SimulateRequest,
    SkillCompileRequest,
    SlackIngestRequest,
    TeamCreateRequest,
    TeamMemberRequest,
    UploadRequest,
    WebIngestRequest,
    WorkspaceCreateRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
graph = get_graph_store()
audit = AuditService()
hcag = HCAGAdapter(graph)
company_context = CompanyContextService(graph, hcag.context_store)
ingestion = IngestionService(graph, hcag, audit)
retrieval = RetrievalService(hcag, audit)
runbooks = RunbookService(graph, hcag, audit)
approvals = ApprovalService(graph, runbooks, audit=audit)
drift = DriftService(graph, runbooks, hcag, audit)
simulation = SimulationService(runbooks, approvals.gate, audit)
memories = OperationalMemoryService(graph, audit)
company_memory = CompanyMemoryService(graph)
company_brain = CompanyBrainService(graph)
scopes = ScopeService()
assertions = OperationalAssertionService(graph, audit)
change_impacts = ChangeImpactService(graph, assertions, audit)
change_intelligence = ChangeIntelligenceService(graph)
memory_work = MemoryWorkService(retrieval, company_brain, audit)
connector_runtime = ConnectorRuntime(audit=audit)
connector_sync = SyncEngine(
    MemoryWorksSyncApplier(ingestion, graph),
    registry=connector_runtime.registry,
    audit=audit,
)


def fail(exc: Exception):
    raise HTTPException(status_code=400, detail=str(exc)) from exc


def _authenticate(authorization: str | None) -> dict:
    """Resolve a user session or workspace-scoped API key into one principal."""
    token = bearer_token(authorization)
    principal = me_from_token(token)
    if principal:
        return {**principal, "auth_type": "session"}

    api_key = verify_api_key(token or "")
    if api_key:
        if not api_key["workspace_id"]:
            # Keys made before workspace scoping cannot safely access tenant data.
            raise HTTPException(401, "API key is not workspace-scoped; create a replacement key")
        return {
            "id": f"key:{api_key['id']}",
            "email": "",
            "display_name": api_key["name"],
            "active_workspace_id": api_key["workspace_id"],
            "role": "member",
            "auth_type": "api_key",
            "api_key_id": api_key["id"],
        }

    oauth_principal = principal_from_mcp_token(token or "")
    if oauth_principal:
        return oauth_principal

    raise HTTPException(401, "Not authenticated")


def _authorize_workspace(
    authorization: str | None, workspace_id: str = "", *, admin: bool = False
) -> dict:
    """Authorize a principal against its active workspace and optional admin boundary."""
    principal = _authenticate(authorization)
    active_workspace_id = principal.get("active_workspace_id", "")
    if workspace_id and workspace_id != active_workspace_id:
        raise HTTPException(403, "Workspace is not available to this principal")
    if admin and (
        principal["auth_type"] in {"api_key", "mcp_oauth"}
        or principal["role"] not in {"owner", "admin"}
    ):
        raise HTTPException(403, "Owner or admin session required")
    return principal


def _authorize_project(project_id: str, authorization: str | None, write: bool = False) -> dict:
    """Authorize project data through the caller's active workspace."""
    principal = _authenticate(authorization)
    if not principal:
        raise HTTPException(401, "Not authenticated")
    return _authorize_project_for_principal(principal, project_id, write=write)


def _authorize_project_for_principal(principal: dict, project_id: str, write: bool = False) -> dict:
    """Project authorization for an already-resolved principal (HTTP or agent)."""
    if write and principal["role"] == "viewer":
        raise HTTPException(403, "Viewer role cannot make reliability decisions")
    if (
        write
        and principal.get("auth_type") == "mcp_oauth"
        and "write" not in principal.get("oauth_scopes", [])
    ):
        raise HTTPException(403, "MCP OAuth token does not include the write scope")
    if not row("SELECT id FROM projects WHERE id=?", (project_id,)):
        raise HTTPException(404, "Project not found")
    linked = rows("SELECT workspace_id FROM workspace_projects WHERE project_id=?", (project_id,))
    workspace_id = principal.get("active_workspace_id", "")
    if linked and workspace_id not in {item["workspace_id"] for item in linked}:
        raise HTTPException(403, "Project is not in the active workspace")
    if not linked:
        if principal["role"] not in {"owner", "admin"}:
            raise HTTPException(403, "Only an owner or admin can claim an unlinked project")
        with connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO workspace_projects VALUES (?,?)",
                (workspace_id, project_id),
            )
    if principal["role"] not in {"owner", "admin"}:
        team_ids = scopes.team_ids_for_user(workspace_id, principal["id"])
        if not scopes.can_access_project(project_id, team_ids, write=write):
            raise HTTPException(403, "Project is restricted to another team")
    return principal


def _principal_team_ids(principal: dict) -> list[str] | None:
    if principal.get("role") in {"owner", "admin"}:
        return None
    return scopes.team_ids_for_user(
        principal.get("active_workspace_id", ""), principal.get("id", "")
    )


def _validate_team_scope(principal: dict, team_ids: list[str]) -> list[str]:
    requested = sorted(set(team_ids))
    if not requested:
        return []
    workspace_teams = {item["id"] for item in scopes.list_teams(principal["active_workspace_id"])}
    if not set(requested).issubset(workspace_teams):
        raise HTTPException(403, "One or more teams are outside the active workspace")
    allowed = _principal_team_ids(principal)
    if allowed is not None and not set(requested).issubset(set(allowed)):
        raise HTTPException(403, "A source can only be shared with your teams")
    return requested


def _security_trim_graph_nodes(project_id: str, nodes: list[dict], principal: dict) -> list[dict]:
    team_ids = _principal_team_ids(principal)
    if team_ids is None:
        return nodes
    visible_memories = scopes.visible_memory_ids(project_id, team_ids)
    source_node_types = {
        "Source",
        "SourceRevision",
        "MemoryChangeSet",
        "File",
        "Issue",
        "PullRequest",
        "SlackMessage",
        "EvidenceSource",
        "KnowledgeItem",
        "KnowledgeChunk",
    }
    candidate_sources = {
        str(node.get("source_id") or node.get("id") or "")
        for node in nodes
        if node.get("node_type") in source_node_types
    }
    artifact_sources: dict[str, set[str]] = {}
    for node in nodes:
        node_type = node.get("node_type")
        revision = None
        if node_type == "Artifact":
            revision = row(
                "SELECT source_ids_json FROM artifact_revisions WHERE id=?",
                (node.get("current_revision_id"),),
            )
        elif node_type == "ArtifactRevision":
            revision = row(
                "SELECT source_ids_json FROM artifact_revisions WHERE id=?",
                (node.get("id"),),
            )
        if revision:
            artifact_sources[str(node.get("id") or "")] = set(
                json.loads(revision.get("source_ids_json") or "[]")
            )
            candidate_sources.update(artifact_sources[str(node.get("id") or "")])
    visible_sources = scopes.visible_source_ids(project_id, candidate_sources, team_ids)
    output: list[dict] = []
    for node in nodes:
        node_type = node.get("node_type")
        if (
            node_type == "MemoryUnit"
            and visible_memories is not None
            and node.get("id") not in visible_memories
        ):
            continue
        if node_type in source_node_types:
            source_id = str(node.get("source_id") or node.get("id") or "")
            if source_id not in visible_sources:
                continue
        if node_type == "ContextEnvelope" and node.get("principal_id") != principal.get("id"):
            continue
        if node_type == "SkillSpec" and node.get("team_id") not in {"", *team_ids}:
            continue
        if node_type in {"Artifact", "ArtifactRevision"}:
            dependencies = artifact_sources.get(str(node.get("id") or ""), set())
            if not dependencies.issubset(visible_sources):
                continue
        output.append(node)
    return output


def _security_trim_graph_edges(project_id: str, edges: list[dict], principal: dict) -> list[dict]:
    if _principal_team_ids(principal) is None:
        return edges
    visible_nodes = _security_trim_graph_nodes(
        project_id, graph.list_nodes(project_id, limit=100_000), principal
    )
    visible_ids = {str(node.get("id") or "") for node in visible_nodes}
    return [
        edge
        for edge in edges
        if str(edge.get("from_id") or "") in visible_ids
        and str(edge.get("to_id") or "") in visible_ids
    ]


def _security_trim_graph_summary(project_id: str, principal: dict) -> dict:
    if _principal_team_ids(principal) is None:
        return graph.graph_summary(project_id)
    nodes = _security_trim_graph_nodes(
        project_id, graph.list_nodes(project_id, limit=100_000), principal
    )
    edges = _security_trim_graph_edges(
        project_id, graph.list_edges(project_id, limit=100_000), principal
    )
    node_counts: dict[str, int] = {}
    edge_counts: dict[str, int] = {}
    for node in nodes:
        kind = str(node.get("node_type") or "Unknown")
        node_counts[kind] = node_counts.get(kind, 0) + 1
    for edge in edges:
        kind = str(edge.get("relationship") or "Unknown")
        edge_counts[kind] = edge_counts.get(kind, 0) + 1
    return {
        "project_id": project_id,
        "node_counts": node_counts,
        "edge_counts": edge_counts,
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "services": [node for node in nodes if node.get("node_type") == "Service"],
        "files": [node for node in nodes if node.get("node_type") == "File"],
    }


def _visible_project_ids(principal: dict) -> set[str] | None:
    """Return projects visible to the caller's active workspace."""
    linked = {
        item["project_id"]
        for item in rows(
            "SELECT project_id FROM workspace_projects WHERE workspace_id=?",
            (principal["active_workspace_id"],),
        )
    }
    team_ids = _principal_team_ids(principal)
    if team_ids is None:
        return linked
    return {project_id for project_id in linked if scopes.can_access_project(project_id, team_ids)}


@router.get("/health")
def health():
    return {
        "status": "ok",
        "product": "MemoryWorks",
        "semantic_index": hcag.backfill_status,
    }


@router.get("/auth/me")
def auth_me(authorization: str | None = Header(default=None)):
    token = bearer_token(authorization)
    principal = me_from_token(token)
    if principal:
        return principal
    raise HTTPException(401, "Not authenticated")


@router.get("/auth/providers")
def auth_providers():
    github = bool(settings.github_client_id and settings.github_client_secret)
    google = bool(settings.google_client_id and settings.google_client_secret)
    email = bool(
        settings.email_auth_enabled
        and (settings.auth_dev_mode or settings.smtp_host and settings.email_from)
    )
    return {
        "github": github,
        "google": google,
        "email": email,
        "development": settings.auth_dev_mode,
        "public_demo": settings.public_demo_mode,
        "details": {
            "github": {
                "configured": github,
                "setup": ("Ready" if github else "Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET"),
            },
            "google": {
                "configured": google,
                "setup": ("Ready" if google else "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET"),
            },
            "email": {
                "configured": email,
                "setup": (
                    "Ready" if email else "Set SMTP_HOST and EMAIL_FROM, or enable development auth"
                ),
            },
        },
    }


@router.get("/models")
def models():
    catalog = model_catalog()
    return {
        "models": catalog,
        "configured": sum(1 for item in catalog if item["configured"]),
        "default": settings.org_memory_default_model_provider,
        "fallback": "deterministic_grounding",
    }


@router.get("/platforms")
def platforms():
    """Public source and delivery catalog for the marketing surface.

    The authenticated `/connectors/catalog` route serves the same list; this one
    carries no workspace data so the landing page can show the real integration
    status instead of a hand-maintained copy.
    """
    catalog = product_connector_catalog()
    return {
        "platforms": catalog,
        "live": sum(1 for item in catalog if item["status"] == "live"),
    }


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=7 * 24 * 60 * 60,
        httponly=True,
        # Secure wherever the product is served over HTTPS — production or a
        # staging deployment on a real domain — not only when the environment
        # string says "production".
        secure=(
            settings.environment.casefold() == "production"
            or settings.frontend_url.startswith("https://")
        ),
        samesite="lax",
        domain=settings.session_cookie_domain or None,
        path="/",
    )


def _seed_public_demo_for(session: dict) -> None:
    """Land a real sign-in inside the shared public demo workspace.

    On the hosted demo, a real identity still has to see the seeded launch
    scenario — an empty personal workspace would make the deployment look
    broken. The identity stays real (provider, external id, email); only the
    destination workspace is shared. Production deployments without the
    public-demo profile keep per-user workspaces.
    """
    if not settings.public_demo_mode:
        return
    try:
        seed_launch_scenario(
            session["user"]["active_workspace_id"],
            ingestion.create_project,
            company_memory,
        )
    except Exception:  # noqa: BLE001 - login must not fail on re-seeding
        logger.exception("Public demo scenario seed after OAuth sign-in failed")


@router.post("/auth/dev-login")
def auth_dev_login(request: DevLoginRequest, response: Response):
    try:
        result = create_dev_session(request.email, request.display_name)
        _set_session_cookie(response, result["token"])
        return result
    except Exception as exc:
        fail(exc)


@router.post("/auth/demo-login")
def auth_demo_login(request: DemoLoginRequest, response: Response):
    """Enter the isolated challenge workspace without external OAuth.

    Google and GitHub are persona choices in this profile. No provider token is
    requested, received, or stored. Normal production deployments do not expose
    this authentication option.
    """
    if not settings.public_demo_mode:
        raise HTTPException(404, "Public demo access is not enabled")
    try:
        result = issue_public_demo_session(
            request.identity,
            request.display_name,
        )
        seed_launch_scenario(
            result["user"]["active_workspace_id"],
            ingestion.create_project,
            company_memory,
        )
        _set_session_cookie(response, result["token"])
        return {"expires_at": result["expires_at"], "user": result["user"]}
    except Exception as exc:
        fail(exc)


@router.post("/auth/email/request")
def auth_email_request(request: EmailCodeRequest):
    try:
        return request_email_login_code(request.email)
    except Exception as exc:
        fail(exc)


@router.post("/auth/email/verify")
def auth_email_verify(request: EmailCodeVerifyRequest, response: Response):
    try:
        result = verify_email_login_code(request.email, request.code)
        _set_session_cookie(response, result["token"])
        return result
    except Exception as exc:
        fail(exc)


@router.post("/auth/logout")
def auth_logout(response: Response, authorization: str | None = Header(default=None)):
    result = logout(bearer_token(authorization))
    response.delete_cookie(
        settings.session_cookie_name,
        domain=settings.session_cookie_domain or None,
        path="/",
    )
    return result


@router.get("/workspaces")
def workspaces(authorization: str | None = Header(default=None)):
    return list_workspaces(bearer_token(authorization))


@router.post("/workspaces")
def create_workspace_endpoint(
    request: WorkspaceCreateRequest, authorization: str | None = Header(default=None)
):
    try:
        return create_workspace(request.name, bearer_token(authorization))
    except Exception as exc:
        fail(exc)


@router.get("/workspaces/{workspace_id}/members")
def members(workspace_id: str, authorization: str | None = Header(default=None)):
    _authorize_workspace(authorization, workspace_id, admin=True)
    return workspace_members(workspace_id)


@router.post("/workspaces/{workspace_id}/members/invite")
def invite(
    workspace_id: str,
    request: InviteMemberRequest,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
):
    principal = _authorize_workspace(authorization, workspace_id, admin=True)
    try:
        result = invite_member(workspace_id, request.email, request.role)
    except Exception as exc:
        fail(exc)
    workspace = row("SELECT name FROM workspaces WHERE id=?", (workspace_id,)) or {}
    background_tasks.add_task(
        send_invite_email,
        request.email,
        str(workspace.get("name") or "your workspace"),
        request.role,
        principal.get("display_name") or "",
    )
    audit.record(
        "workspace.member.invited",
        f"Invited {request.email} to {workspace.get('name') or workspace_id}",
        actor=str(principal.get("id") or "system"),
        payload={
            "workspace_id": workspace_id,
            "email": request.email,
            "role": request.role,
            "invite_delivery": result.get("invite_delivery"),
        },
    )
    return result


@router.get("/workspaces/{workspace_id}/teams")
def list_workspace_teams(workspace_id: str, authorization: str | None = Header(default=None)):
    _authorize_workspace(authorization, workspace_id)
    return scopes.list_teams(workspace_id)


@router.post("/workspaces/{workspace_id}/teams")
def create_workspace_team(
    workspace_id: str,
    request: TeamCreateRequest,
    authorization: str | None = Header(default=None),
):
    _authorize_workspace(authorization, workspace_id, admin=True)
    try:
        team = scopes.create_team(workspace_id, request.name, request.parent_team_id)
        graph.upsert_node("Team", team)
        return team
    except Exception as exc:
        fail(exc)


@router.post("/teams/{team_id}/members")
def add_team_member(
    team_id: str,
    request: TeamMemberRequest,
    authorization: str | None = Header(default=None),
):
    team = row("SELECT * FROM teams WHERE id=?", (team_id,))
    if not team:
        raise HTTPException(404, "Team not found")
    _authorize_workspace(authorization, team["workspace_id"], admin=True)
    try:
        return scopes.add_member(team_id, request.user_id, request.role)
    except Exception as exc:
        fail(exc)


@router.post("/projects/{project_id}/teams")
def assign_project_team(
    project_id: str,
    request: ProjectTeamRequest,
    authorization: str | None = Header(default=None),
):
    principal = _authorize_project(project_id, authorization, write=True)
    if principal["role"] not in {"owner", "admin"}:
        raise HTTPException(403, "Owner or admin session required")
    team = row("SELECT workspace_id FROM teams WHERE id=?", (request.team_id,))
    if not team or team["workspace_id"] != principal["active_workspace_id"]:
        raise HTTPException(404, "Team not found")
    try:
        return scopes.assign_project(project_id, request.team_id, request.access_level)
    except Exception as exc:
        fail(exc)


@router.get("/health/graph")
def graph_health():
    try:
        return graph.health()
    except Exception as exc:
        return {
            "backend": "arcadedb",
            "connected": False,
            "database": settings.arcadedb_database,
            "error": str(exc),
        }


@router.get("/projects/{project_id}/graph/summary")
def project_graph_summary(project_id: str, authorization: str | None = Header(default=None)):
    principal = _authorize_project(project_id, authorization)
    try:
        return _security_trim_graph_summary(project_id, principal)
    except Exception as exc:
        fail(exc)


@router.get("/projects/{project_id}/graph/nodes")
def project_graph_nodes(
    project_id: str,
    node_type: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    authorization: str | None = Header(default=None),
):
    principal = _authorize_project(project_id, authorization)
    try:
        return _security_trim_graph_nodes(
            project_id, graph.list_nodes(project_id, node_type, limit), principal
        )
    except Exception as exc:
        fail(exc)


@router.get("/projects/{project_id}/graph/edges")
def project_graph_edges(
    project_id: str,
    edge_type: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    authorization: str | None = Header(default=None),
):
    principal = _authorize_project(project_id, authorization)
    try:
        return _security_trim_graph_edges(
            project_id, graph.list_edges(project_id, edge_type, limit), principal
        )
    except Exception as exc:
        fail(exc)


@router.get("/projects/{project_id}/graph/view")
def project_graph_view(
    project_id: str,
    limit: int = Query(300, ge=1, le=1000),
    authorization: str | None = Header(default=None),
):
    """Return one consistent ArcadeDB snapshot for the visual explorer."""
    principal = _authorize_project(project_id, authorization)
    try:
        nodes = _security_trim_graph_nodes(
            project_id, graph.list_nodes(project_id, limit=limit), principal
        )
        edges = _security_trim_graph_edges(
            project_id, graph.list_edges(project_id, limit=limit), principal
        )
        return {
            "summary": _security_trim_graph_summary(project_id, principal),
            "nodes": nodes,
            "edges": edges,
        }
    except Exception as exc:
        fail(exc)


@router.get("/projects/{project_id}/graph/service/{service_name}")
def project_service_graph(
    project_id: str, service_name: str, authorization: str | None = Header(default=None)
):
    _authorize_project(project_id, authorization)
    try:
        return graph.service_graph(project_id, service_name)
    except Exception as exc:
        fail(exc)


@router.get("/projects/{project_id}/graph/file")
def project_file_graph(
    project_id: str, path: str, authorization: str | None = Header(default=None)
):
    _authorize_project(project_id, authorization)
    try:
        return graph.file_graph(project_id, path)
    except Exception as exc:
        fail(exc)


@router.get("/projects/{project_id}/graph/trace")
def project_graph_trace(
    project_id: str,
    chunk_ids: str = "",
    authorization: str | None = Header(default=None),
):
    _authorize_project(project_id, authorization)
    try:
        return graph.get_retrieval_trace(
            project_id, [value for value in chunk_ids.split(",") if value]
        )
    except Exception as exc:
        fail(exc)


@router.get("/overview")
def overview(authorization: str | None = Header(default=None)):
    principal = _authorize_workspace(authorization)
    visible = _visible_project_ids(principal)
    project_ids = sorted(visible or set())
    placeholders = ",".join("?" for _ in project_ids) or "''"
    project_filter = f"project_id IN ({placeholders})"
    counts = {
        "projects": len(project_ids),
        "knowledge_items": row(
            f"SELECT COUNT(*) value FROM knowledge_items WHERE {project_filter}",
            tuple(project_ids),
        )["value"],
        "runbooks": row(
            f"SELECT COUNT(*) value FROM runbooks WHERE {project_filter}",
            tuple(project_ids),
        )["value"],
        "pending_approvals": row(
            f"SELECT COUNT(*) value FROM actions WHERE status='pending' AND {project_filter}",
            tuple(project_ids),
        )["value"],
        "recent_queries": row(
            f"SELECT COUNT(*) value FROM audit_events WHERE event_type='query.answered' AND {project_filter}",
            tuple(project_ids),
        )["value"],
        "connected_sources": row(
            "SELECT COUNT(*) value FROM workspace_connector_accounts WHERE workspace_id=? AND status='connected'",
            (principal["active_workspace_id"],),
        )["value"],
        "memory_units": row(
            f"SELECT COUNT(*) value FROM memory_units WHERE is_latest=1 AND {project_filter}",
            tuple(project_ids),
        )["value"],
        "decisions": row(
            f"SELECT COUNT(*) value FROM memory_units WHERE is_latest=1 AND type='decision' AND {project_filter}",
            tuple(project_ids),
        )["value"],
        "policies": row(
            f"SELECT COUNT(*) value FROM memory_units WHERE is_latest=1 AND type='policy' AND {project_filter}",
            tuple(project_ids),
        )["value"],
        "memory_conflicts": row(
            f"SELECT COUNT(*) value FROM memory_relationships WHERE relationship='CONTRADICTS' AND {project_filter}",
            tuple(project_ids),
        )["value"],
        "memory_changes": row(
            f"SELECT COUNT(*) value FROM memory_change_sets WHERE {project_filter}",
            tuple(project_ids),
        )["value"],
    }
    activity = [
        item for item in audit.list(limit=100) if item.get("project_id") in set(project_ids)
    ][:8]
    return {**counts, "graph": graph_health(), "recent_activity": activity}


@router.get("/settings/runtime")
def runtime_settings(authorization: str | None = Header(default=None)):
    """Deployment/runtime surface consumed by the settings, admin, and IDE
    access pages. Requires a workspace-authenticated principal. Deployment
    internals (environment name, auth mode, graph backend, database name) are
    owner/admin-only; workspace members get the connection flags their UI needs.
    """
    principal = _authorize_workspace(authorization)
    admin_view = principal["role"] in {"owner", "admin"} and principal["auth_type"] == "session"
    member_view = {
        "github_oauth_configured": bool(
            settings.github_client_id and settings.github_client_secret
        ),
        "slack_oauth_configured": bool(settings.slack_client_id and settings.slack_client_secret),
        "google_oauth_configured": bool(
            settings.google_client_id and settings.google_client_secret
        ),
        "email_auth_configured": bool(
            settings.email_auth_enabled
            and (settings.auth_dev_mode or settings.smtp_host and settings.email_from)
        ),
        "models_configured": sum(1 for item in model_catalog() if item["configured"]),
        # Where an IDE or desktop agent connects. The setup instructions are
        # generated from this rather than written into the docs by hand, so a
        # self-hosted deployment shows its own URLs instead of localhost.
        "mcp_http_url": settings.mcp_public_url.rstrip("/") + "/mcp",
        "mcp_oauth_issuer": settings.mcp_oauth_issuer_url.rstrip("/"),
        "api_url": settings.api_url.rstrip("/"),
    }
    if not admin_view:
        return member_view
    return {
        **member_view,
        "environment": settings.environment,
        "auth_dev_mode": settings.auth_dev_mode,
        "runbook_demo_mode": settings.runbook_demo_mode,
        "allow_local_command_execution": settings.allow_local_command_execution,
        "graph_backend": settings.graph_backend,
        "arcadedb_database": settings.arcadedb_database,
        "hcag_enabled": True,
        "agentgate_enabled": True,
        "embedding_provider": settings.runbook_embedding_provider,
        "embedding_model": (
            settings.runbook_embedding_model
            if settings.runbook_embedding_provider == "fastembed"
            else settings.runbook_openai_embedding_model
        ),
        "semantic_retrieval": settings.runbook_embedding_provider in {"fastembed", "openai"},
        "reranker": (
            settings.runbook_reranker_model
            if settings.runbook_reranker_provider == "fastembed"
            else "disabled"
        ),
        "vector_store": "arcadedb_exact_cosine",
        "github_live_updates": bool(settings.github_webhook_secret),
        "slack_live_updates": bool(settings.slack_signing_secret),
    }


@router.get("/projects")
def list_projects(authorization: str | None = Header(default=None)):
    principal = _authenticate(authorization)
    visible = _visible_project_ids(principal)
    records = rows(
        "SELECT p.*, (SELECT COUNT(*) FROM knowledge_items k WHERE k.project_id=p.id) knowledge_items, (SELECT COUNT(*) FROM runbooks r WHERE r.project_id=p.id) runbooks FROM projects p ORDER BY created_at DESC"
    )
    return records if visible is None else [item for item in records if item["id"] in visible]


@router.post("/projects")
def create_memory_project(
    request: ProjectCreateRequest, authorization: str | None = Header(default=None)
):
    principal = _authorize_workspace(authorization)
    if principal["role"] == "viewer":
        raise HTTPException(403, "Viewer role cannot create a project")
    team_ids = _validate_team_scope(principal, request.team_ids)
    project_id = ingestion.create_project(request.name)
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO workspace_projects VALUES (?,?)",
            (principal["active_workspace_id"], project_id),
        )
    for team_id in team_ids:
        scopes.assign_project(project_id, team_id, "write")
    return {"id": project_id, "name": request.name, "status": "ready"}


@router.get("/projects/{project_id}/context")
def project_context(project_id: str, authorization: str | None = Header(default=None)):
    _authorize_project(project_id, authorization)
    try:
        return company_context.briefing(project_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        fail(exc)


@router.post("/projects/{project_id}/memory/repair")
def repair_project_memory(
    project_id: str,
    request: MemoryRepairRequest,
    authorization: str | None = Header(default=None),
):
    principal = _authorize_project(project_id, authorization, write=True)
    if principal["role"] not in {"owner", "admin"}:
        raise HTTPException(403, "Owner or admin session required")
    project = row("SELECT * FROM projects WHERE id=?", (project_id,)) or {}
    try:
        cleanup = reset_project_derived_memory(
            graph,
            project_id,
            repository_only=request.repository_only,
            clear_work_history=request.clear_work_history,
        )
        reingestion = {}
        if project.get("repository"):
            connector = GitHubConnector(
                ConnectorSecrets(
                    principal["active_workspace_id"],
                    principal["id"],
                )
            )
            reingestion = RepositoryIngestor(ingestion, graph, connector).ingest(
                project["repository"],
                project["name"],
            )
        retained_memories = rebuild_atomic_memories_from_index(
            graph,
            project_id,
            source_types={
                "doc",
                "document",
                "incident",
                "log",
                "report",
                "slack",
                "slack_export",
                "text",
                "upload",
            },
        )
        service_count = rebuild_services_from_index(graph, project_id)
        audit.record(
            "memory.repaired",
            f"Rebuilt current memory for {project.get('name') or project_id}",
            project_id,
            principal.get("display_name") or principal["id"],
            {
                **cleanup,
                "repository_only": request.repository_only,
                "services": service_count,
            },
        )
        return {
            "status": "repaired",
            "project_id": project_id,
            "cleanup": cleanup,
            "reingestion": reingestion,
            "retained_memories": retained_memories,
            "current_memories": len(company_memory.list(project_id, latest=True, limit=10_000)),
            "services": service_count,
        }
    except Exception as exc:
        fail(exc)


def _github_webhook_project(repository: str) -> dict:
    for project in rows(
        """SELECT p.*,wp.workspace_id FROM projects p
        LEFT JOIN workspace_projects wp ON wp.project_id=p.id"""
    ):
        if GitHubConnector.slug(str(project.get("repository") or "")) == repository:
            return project
    raise HTTPException(404, "No MemoryWorks project is connected to this GitHub repository")


def _process_github_webhook_event(event_id: str, payload: dict, workspace_id: str) -> None:
    try:
        connector = GitHubConnector(ConnectorSecrets(workspace_id))
        repository = str((payload.get("repository") or {}).get("full_name") or "")
        project = _github_webhook_project(repository)
        # A webhook is a source revision signal, not just a notification.
        # Reconcile the repository corpus first so HCAG can answer against the
        # new commit immediately after this background task completes.
        RepositoryIngestor(ingestion, graph, connector).ingest(
            str(project.get("repository") or ""),
            str(project.get("name") or repository),
        )
        diff, metadata = github_diff(payload, connector)
        change_intelligence.process(
            event_id,
            diff,
            {
                "event": payload.get("action") or "push",
                "commit_sha": metadata["commit_sha"],
                "scope": {"repo": metadata["repository"]},
            },
        )
    except Exception as exc:
        change_intelligence.fail(event_id, str(exc))


def _process_slack_memory_event(event: dict) -> None:
    channel_id = str(event.get("channel") or "")
    subtype = str(event.get("subtype") or "")
    message = event.get("message") if subtype == "message_changed" else event
    previous = event.get("previous_message") or {}
    timestamp = str(
        (message or {}).get("ts")
        or previous.get("ts")
        or event.get("deleted_ts")
        or event.get("ts")
        or ""
    )
    if not channel_id or not timestamp:
        return
    projects = rows(
        """SELECT DISTINCT project_id FROM knowledge_items
        WHERE source_type IN ('slack','slack_export')
        AND json_extract(metadata_json,'$.channel_id')=?""",
        (channel_id,),
    )
    for project in projects:
        project_id = project["project_id"]
        existing = row(
            """SELECT * FROM knowledge_items WHERE project_id=?
            AND source_type IN ('slack','slack_export')
            AND json_extract(metadata_json,'$.channel_id')=?
            AND json_extract(metadata_json,'$.timestamp')=? LIMIT 1""",
            (project_id, channel_id, timestamp),
        )
        if subtype == "message_deleted":
            if existing:
                company_memory.retire_source_memories(project_id, existing["source_id"])
                graph.delete_source_knowledge(
                    project_id, existing["source_id"], existing["source_type"]
                )
                with connect() as conn:
                    conn.execute("DELETE FROM knowledge_items WHERE id=?", (existing["id"],))
            continue
        text = str((message or {}).get("text") or "").strip()
        if not text or ((message or {}).get("bot_id") and subtype != "message_changed"):
            continue
        source_id = (
            existing["source_id"]
            if existing
            else f"slack-message:{project_id}:{channel_id}:{timestamp}"
        )
        if existing and existing["content"] == text:
            continue
        if existing:
            company_memory.retire_source_memories(project_id, source_id)
            graph.delete_source_knowledge(project_id, source_id, existing["source_type"])
            with connect() as conn:
                conn.execute("DELETE FROM knowledge_items WHERE id=?", (existing["id"],))
        ingestion.ingest_item(
            project_id,
            "slack",
            f"Slack #{channel_id} at {timestamp}",
            text,
            source_id=source_id,
            metadata={
                "channel_id": channel_id,
                "channel_name": channel_id,
                "user": (message or {}).get("user", ""),
                "timestamp": timestamp,
                "source_updated_at": event.get("event_ts") or timestamp,
                "actor": "slack_event",
            },
        )


@router.post("/webhooks/{provider}/{workspace_id}", status_code=202)
async def connector_webhook(
    provider: str,
    workspace_id: str,
    request: Request,
):
    """Signed, workspace-bound webhook entry point for every connector package."""
    try:
        return connector_sync.receive_webhook(
            provider,
            workspace_id,
            WebhookRequest(
                headers={key.casefold(): value for key, value in request.headers.items()},
                body=await request.body(),
            ),
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        message = str(exc)
        status = 401 if any(word in message.casefold() for word in ("signature", "stale")) else 400
        raise HTTPException(status, message) from exc


@router.post("/webhooks/github", status_code=202)
async def github_change_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str = Header(default=""),
    x_github_delivery: str = Header(default=""),
    x_github_event: str = Header(default="push"),
):
    if not settings.github_webhook_secret:
        raise HTTPException(503, "GitHub webhook verification is not configured")
    body = await request.body()
    expected = (
        "sha256="
        + hmac.new(settings.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    )
    if not x_hub_signature_256 or not hmac.compare_digest(expected, x_hub_signature_256):
        raise HTTPException(401, "Invalid GitHub webhook signature")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "GitHub webhook body is not valid JSON") from exc
    repository = str((payload.get("repository") or {}).get("full_name") or "")
    project = _github_webhook_project(repository)
    delivery_id = x_github_delivery or hashlib.sha256(body).hexdigest()
    pull_request = payload.get("pull_request") or {}
    commit_sha = str(((pull_request.get("head") or {}).get("sha")) or payload.get("after") or "")
    source_url = str(
        pull_request.get("html_url")
        or (payload.get("head_commit") or {}).get("url")
        or (payload.get("repository") or {}).get("html_url")
        or ""
    )
    event, created = change_intelligence.observe(
        project["id"],
        delivery_id,
        f"github_{x_github_event}",
        repository,
        commit_sha,
        source_url,
        payload,
    )
    if created:
        background_tasks.add_task(
            _process_github_webhook_event,
            event["id"],
            payload,
            str(project.get("workspace_id") or ""),
        )
    return {**event, "accepted": created, "replayed": not created}


@router.post("/webhooks/slack")
async def slack_memory_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_slack_request_timestamp: str = Header(default=""),
    x_slack_signature: str = Header(default=""),
):
    if not settings.slack_signing_secret:
        raise HTTPException(503, "Slack webhook verification is not configured")
    body = await request.body()
    try:
        timestamp = int(x_slack_request_timestamp)
    except (TypeError, ValueError) as exc:
        raise HTTPException(401, "Invalid Slack request timestamp") from exc
    if abs(int(time.time()) - timestamp) > 300:
        raise HTTPException(401, "Stale Slack webhook request")
    expected = (
        "v0="
        + hmac.new(
            settings.slack_signing_secret.encode(),
            f"v0:{x_slack_request_timestamp}:".encode() + body,
            hashlib.sha256,
        ).hexdigest()
    )
    if not x_slack_signature or not hmac.compare_digest(expected, x_slack_signature):
        raise HTTPException(401, "Invalid Slack webhook signature")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Slack webhook body is not valid JSON") from exc
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}
    event = payload.get("event") or {}
    if event.get("type") == "message":
        background_tasks.add_task(_process_slack_memory_event, event)
    return {"accepted": True}


@router.post("/memory/semantic-changes/interpret")
def interpret_semantic_change(
    request: SemanticChangeInterpretRequest,
    authorization: str | None = Header(default=None),
):
    _authorize_project(request.project_id, authorization, write=True)
    project = row("SELECT * FROM projects WHERE id=?", (request.project_id,)) or {}
    repository = request.repository or GitHubConnector.slug(project.get("repository", "")) or ""
    delivery_id = (
        request.delivery_id
        or hashlib.sha256(
            f"{request.project_id}:{request.commit_sha}:{request.diff}".encode()
        ).hexdigest()
    )
    event, created = change_intelligence.observe(
        request.project_id,
        delivery_id,
        "github_commit",
        repository,
        request.commit_sha,
        request.source_url,
        {"manual": True, "context": request.context},
    )
    if created:
        return change_intelligence.process(
            event["id"],
            request.diff,
            {
                **request.context,
                "scope": {"repo": repository, **request.context.get("scope", {})},
            },
        )
    return change_intelligence.detail(event["id"])


@router.get("/memory/semantic-changes")
def semantic_changes(
    project_id: str,
    limit: int = Query(100, ge=1, le=500),
    authorization: str | None = Header(default=None),
):
    _authorize_project(project_id, authorization)
    return change_intelligence.list(project_id, limit)


@router.get("/memory/semantic-changes/{event_id}")
def semantic_change_detail(event_id: str, authorization: str | None = Header(default=None)):
    event = change_intelligence.get(event_id)
    if not event:
        raise HTTPException(404, "Semantic change event not found")
    _authorize_project(event["project_id"], authorization)
    return change_intelligence.detail(event_id)


@router.post("/ingest/github")
def ingest_github(request: GitHubIngestRequest, authorization: str | None = Header(default=None)):
    principal = _authorize_workspace(authorization, request.workspace_id or "")
    team_ids = _validate_team_scope(principal, request.team_ids)
    connector = GitHubConnector(ConnectorSecrets(principal["active_workspace_id"], principal["id"]))
    job_id = _create_job(
        source="github",
        source_ref=request.repo_url_or_path,
        workspace_id=principal["active_workspace_id"],
    )
    try:
        result = RepositoryIngestor(ingestion, graph, connector).ingest(
            request.repo_url_or_path, request.project_name
        )
        with connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO workspace_projects VALUES (?,?)",
                (principal["active_workspace_id"], result["project_id"]),
            )
        for team_id in team_ids:
            scopes.assign_project(result["project_id"], team_id, "write")
        repository_resource = (
            GitHubConnector.slug(request.repo_url_or_path) or request.repo_url_or_path
        )
        connector_sync.enqueue(
            "github",
            principal["active_workspace_id"],
            principal["id"],
            repository_resource,
            project_id=result["project_id"],
            cursor={"repository": repository_resource},
            idempotency_key=f"initial:{result['project_id']}:{repository_resource}",
        )
        if result.get("change", {}).get("changed_files"):
            result["change_impact"] = change_impacts.analyze(result["project_id"], result["change"])
        _finish_job(job_id, "succeeded", result)
        return {"job_id": job_id, **result}
    except Exception as exc:
        _fail_job(job_id, exc)
        fail(exc)


@router.post("/ingest/github/all")
def ingest_all_github(
    request: GitHubBulkIngestRequest,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
):
    """Index every repository visible to this workspace's GitHub grant."""
    principal = _authorize_workspace(authorization, request.workspace_id or "", admin=True)
    workspace_id = principal["active_workspace_id"]
    queued = _queue_github_inventory(
        workspace_id,
        principal["id"],
        background_tasks,
        include_archived=request.include_archived,
        max_repositories=request.max_repositories,
        owner=request.owner or "",
    )
    return {"status": "queued" if queued else "nothing_to_index", "repositories_queued": queued}


def _queue_github_inventory(
    workspace_id: str,
    user_id: str,
    background_tasks: BackgroundTasks | None,
    *,
    include_archived: bool = False,
    max_repositories: int = 500,
    owner: str = "",
) -> int:
    """Queue every visible repository for ingestion. Returns how many were queued.

    Best-effort by design: this runs inside an OAuth redirect, where raising
    would strand the user on an error page after their account connected fine.
    """
    try:
        connector = GitHubConnector(ConnectorSecrets(workspace_id, user_id))
        repositories = connector.list_repositories()
    except Exception:
        return 0
    wanted = owner.casefold()
    selected = [
        item
        for item in repositories
        if (include_archived or not item.get("archived"))
        and (not wanted or str((item.get("owner") or {}).get("login", "")).casefold() == wanted)
    ][:max_repositories]
    queued: list[dict[str, str]] = []
    for repository in selected:
        source = repository.get("clone_url") or repository.get("html_url")
        full_name = repository.get("full_name") or repository.get("name") or source
        job_id = _create_job("github", source, workspace_id=workspace_id)
        queued.append({"job_id": job_id, "repository": full_name, "source": source})
    if not queued:
        return 0
    if background_tasks is not None:
        background_tasks.add_task(_run_github_inventory, queued, connector, workspace_id)
    else:
        Thread(
            target=_run_github_inventory,
            args=(queued, connector, workspace_id),
            daemon=True,
        ).start()
    return len(queued)


def _run_github_inventory(
    queued: list[dict[str, str]],
    connector: GitHubConnector,
    workspace_id: str = "",
) -> None:
    """Complete a GitHub inventory outside the request/response timeout."""
    ingestor = RepositoryIngestor(ingestion, graph, connector)
    for item in queued:
        try:
            result = ingestor.ingest(item["source"], item["repository"])
            # Without this link the repository is ingested but invisible: it never
            # appears in the workspace, and asking about it fails authorization.
            # The single-repository route has always done this; bulk did not.
            if workspace_id:
                with connect() as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO workspace_projects VALUES (?,?)",
                        (workspace_id, result["project_id"]),
                    )
            _finish_job(item["job_id"], "succeeded", result)
        except Exception as exc:
            _fail_job(item["job_id"], exc)


@router.post("/ingest/upload")
def ingest_upload(request: UploadRequest, authorization: str | None = Header(default=None)):
    principal = _authorize_project(request.project_id, authorization, write=True)
    team_ids = _validate_team_scope(principal, request.team_ids)
    job_id = _create_job("upload", request.title, project_id=request.project_id)
    try:
        result = ingestion.ingest_item(
            request.project_id,
            request.source_type,
            request.title,
            request.content,
            request.source_url,
            request.source_id,
            {
                "team_ids": team_ids,
                "artifact_type": request.artifact_type,
                "artifact_name": request.artifact_name or request.title,
                "actor": principal["id"],
            },
        )
        payload = {"project_id": request.project_id, **result, "status": "success"}
        _finish_job(job_id, "succeeded", payload)
        return {"job_id": job_id, **payload}
    except Exception as exc:
        _fail_job(job_id, exc)
        fail(exc)


@router.post("/ingest/file")
async def ingest_file(
    project_id: str = Form(...),
    source_type: str = Form("doc"),
    team_ids: str = Form(""),
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
):
    """Ingest any supported artifact: code and text files, PDF, DOCX, XLSX,
    PPTX, ODT, RTF, HTML, and mail exports. Binary formats are converted to
    plain text (page/slide/sheet markers preserved) before chunking, so
    retrieval and provenance behave exactly like any other source."""
    principal = _authorize_project(project_id, authorization, write=True)
    filename = file.filename or "upload.txt"
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_UPLOAD_SUFFIXES:
        raise HTTPException(
            415,
            "Unsupported file type. Supported: PDF, DOCX, XLSX, PPTX, ODT, RTF, HTML, "
            "EML, CSV, JSON, YAML, Markdown, and source files.",
        )
    data = await file.read()
    try:
        document = extract_document(filename, data)
    except UnsupportedDocumentError as exc:
        raise HTTPException(415, str(exc)) from exc
    scoped_team_ids = _validate_team_scope(
        principal, [item for item in team_ids.replace(",", " ").split() if item]
    )
    source_type = "document" if source_type == "doc" else source_type
    result = ingestion.ingest_item(
        project_id,
        source_type,
        document.title or filename,
        document.text,
        metadata={
            **document.metadata,
            "path": filename,
            "team_ids": scoped_team_ids,
            "actor": principal["id"],
        },
    )
    return {
        "project_id": project_id,
        **result,
        "status": "success",
        "format": document.format,
        "warnings": document.warnings,
    }


@router.post("/ingest/website")
def ingest_website(request: WebIngestRequest, authorization: str | None = Header(default=None)):
    """Ingest a public web page or hosted document (PDF/DOCX/… served over HTTP).

    SSRF-guarded: the URL is re-resolved and re-validated before every request
    and every redirect hop, and private or special-use addresses are refused.
    """
    principal = _authorize_project(request.project_id, authorization, write=True)
    team_ids = _validate_team_scope(principal, request.team_ids)
    job_id = _create_job("website", request.url, project_id=request.project_id)
    try:
        document, fetch_metadata = fetch_web_document(request.url)
        result = ingestion.ingest_item(
            request.project_id,
            "web_page",
            document.title or request.url,
            document.text,
            source_url=fetch_metadata["final_url"],
            metadata={
                **document.metadata,
                **fetch_metadata,
                "team_ids": team_ids,
                "actor": principal["id"],
            },
        )
        _finish_job(job_id, "succeeded", {"project_id": request.project_id, **result})
        return {
            "job_id": job_id,
            "project_id": request.project_id,
            **result,
            "status": "success",
            "format": document.format,
            "final_url": fetch_metadata["final_url"],
            "warnings": document.warnings,
        }
    except (WebFetchError, UnsupportedDocumentError) as exc:
        _fail_job(job_id, exc)
        fail(exc)
    except Exception as exc:
        _fail_job(job_id, exc)
        fail(exc)


@router.post("/ingest/slack")
def ingest_slack(request: SlackIngestRequest, authorization: str | None = Header(default=None)):
    principal = _authorize_project(request.project_id, authorization, write=True)
    team_ids = _validate_team_scope(principal, request.team_ids)
    job_id = _create_job("slack", request.channel_id, project_id=request.project_id)
    try:
        connector = SlackConnector(
            ConnectorSecrets(principal["active_workspace_id"], principal["id"])
        )
        result = SlackIngestor(ingestion, graph, connector).ingest_channel(
            request.project_id, request.channel_id, request.limit, team_ids
        )
        connector_sync.enqueue(
            "slack",
            principal["active_workspace_id"],
            principal["id"],
            request.channel_id,
            project_id=request.project_id,
            cursor={"channel_id": request.channel_id, "limit": request.limit},
            idempotency_key=f"initial:{request.project_id}:{request.channel_id}",
        )
        _finish_job(job_id, "succeeded", result)
        return {"job_id": job_id, **result}
    except Exception as exc:
        _fail_job(job_id, exc)
        fail(exc)


@router.get("/ingest/jobs")
def ingest_jobs(project_id: str | None = None, authorization: str | None = Header(default=None)):
    principal = _authorize_workspace(authorization)
    if project_id:
        _authorize_project(project_id, authorization)
        return [
            decode(item)
            for item in rows(
                "SELECT * FROM ingestion_jobs WHERE project_id=? ORDER BY created_at DESC",
                (project_id,),
            )
        ]
    return [
        decode(item)
        for item in rows(
            """SELECT DISTINCT j.* FROM ingestion_jobs j
            LEFT JOIN workspace_projects wp ON wp.project_id=j.project_id
            WHERE j.workspace_id=? OR wp.workspace_id=?
            ORDER BY j.created_at DESC LIMIT 200""",
            (principal["active_workspace_id"], principal["active_workspace_id"]),
        )
    ]


@router.get("/ingest/jobs/{job_id}")
def ingest_job(job_id: str, authorization: str | None = Header(default=None)):
    principal = _authorize_workspace(authorization)
    item = row("SELECT * FROM ingestion_jobs WHERE id=?", (job_id,))
    if not item:
        raise HTTPException(404, "Ingestion job not found")
    if item.get("workspace_id") != principal["active_workspace_id"]:
        if not item.get("project_id"):
            raise HTTPException(404, "Ingestion job not found")
        _authorize_project(item["project_id"], authorization)
    return decode(item)


@router.post("/ask")
def ask(request: AskRequest, authorization: str | None = Header(default=None)):
    principal = _authorize_project(request.project_id, authorization)
    visible = _visible_project_ids(principal)
    return retrieval.ask(
        request.project_id,
        request.query,
        sorted(visible or []),
        principal=principal,
        allowed_team_ids=_principal_team_ids(principal),
        token_budget=request.token_budget,
        model_provider=request.model,
        surface=request.surface,
        scope=request.scope,
        history=[turn.model_dump() for turn in request.history],
    )


@router.post("/execute")
def execute_handoff(
    request: ExecuteRequest,
    background: BackgroundTasks,
    authorization: str | None = Header(default=None),
):
    """Apply a handoff to the repository with a headless coding agent.

    Returns as soon as the run is queued — the agent takes minutes, so the
    client polls `/execute/{run_id}` rather than holding a request open.
    """
    # The handoff names the repository its files actually came from, which is not
    # necessarily the project selected in the UI — retrieval searches the whole
    # workspace. Editing the wrong checkout is the failure this prevents.
    target_project = str(request.handoff.get("project_id") or "") or request.project_id
    principal = _authorize_project(target_project, authorization, write=True)
    project = row("SELECT repository FROM projects WHERE id=?", (target_project,))
    try:
        run = start_execution_run(
            project_id=target_project,
            handoff=request.handoff,
            repository=str((project or {}).get("repository") or ""),
            workspace_id=principal["active_workspace_id"],
            context_event_id=request.context_event_id,
            executor=request.executor or "",
            requested_by=str(principal.get("display_name") or principal["id"]),
            push=request.push,
        )
    except ExecutionError as exc:
        raise HTTPException(422, str(exc)) from None
    background.add_task(execute_run, run["id"], push=request.push)
    return run


@router.get("/execute/{run_id}")
def execution_run(run_id: str, authorization: str | None = Header(default=None)):
    principal = _authorize_workspace(authorization)
    run = get_execution_run(run_id)
    if not run or run.get("workspace_id") != principal["active_workspace_id"]:
        raise HTTPException(404, "Execution run not found")
    return run


@router.get("/execute")
def execution_runs(
    project_id: str = "",
    limit: int = Query(50, ge=1, le=200),
    authorization: str | None = Header(default=None),
):
    principal = _authorize_workspace(authorization)
    if project_id:
        _authorize_project(project_id, authorization)
    return {
        "runs": list_execution_runs(principal["active_workspace_id"], project_id, limit),
        "executors": available_executors(),
    }


@router.get("/skills/learned")
def learned_skills(
    project_id: str = "",
    status: str = "",
    limit: int = Query(100, ge=1, le=500),
    authorization: str | None = Header(default=None),
):
    """Skills distilled from work that verifiably worked in this workspace."""
    principal = _authorize_workspace(authorization)
    if project_id:
        _authorize_project(project_id, authorization)
    items = list_learned_skills(
        principal["active_workspace_id"], project_id=project_id, status=status, limit=limit
    )
    return {
        "skills": items,
        "active": sum(1 for item in items if item["status"] == "active"),
        "proposed": sum(1 for item in items if item["status"] == "proposed"),
        "retired": sum(1 for item in items if item["status"] == "retired"),
    }


@router.post("/skills/learned/{skill_id}/retire")
def retire_learned_skill(
    skill_id: str,
    reason: str = "",
    authorization: str | None = Header(default=None),
):
    """Prune a skill by hand. A library nobody curates becomes a confident mess."""
    principal = _authorize_workspace(authorization)
    skill = get_learned_skill(skill_id)
    if not skill or skill.get("workspace_id") != principal["active_workspace_id"]:
        raise HTTPException(404, "Skill not found")
    return retire_learned_skill_record(skill_id, reason or "Retired by a reviewer.")


@router.post("/outcomes/actions")
def record_context_action(
    request: ActionRecordRequest,
    authorization: str | None = Header(default=None),
):
    """Log what was done with a served context — the second leg of the loop."""
    principal = _authorize_workspace(authorization)
    try:
        return record_action(
            context_event_id=request.context_event_id,
            action_type=request.action_type,
            workspace_id=principal["active_workspace_id"],
            actor=str(principal.get("display_name") or principal["id"]),
            surface=request.surface,
            target=request.target,
            detail=request.detail,
        )
    except LookupError:
        raise HTTPException(404, "Unknown context event") from None


@router.post("/outcomes/outcomes")
def record_context_outcome(
    request: OutcomeRecordRequest,
    authorization: str | None = Header(default=None),
):
    """Log whether the action worked — the leg that turns the log into a label."""
    principal = _authorize_workspace(authorization)
    try:
        return record_outcome(
            context_event_id=request.context_event_id,
            outcome=request.outcome,
            workspace_id=principal["active_workspace_id"],
            action_event_id=request.action_event_id,
            signal=request.signal,
            reason=request.reason,
            detail=request.detail,
        )
    except LookupError:
        raise HTTPException(404, "Unknown context event") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/outcomes/stats")
def outcome_loop_stats(
    project_id: str = "",
    authorization: str | None = Header(default=None),
):
    principal = _authorize_workspace(authorization)
    if project_id:
        _authorize_project(project_id, authorization)
    return outcome_stats(principal["active_workspace_id"], project_id)


@router.get("/outcomes/export")
def outcome_training_export(
    project_id: str = "",
    labelled_only: bool = True,
    limit: int = Query(1000, ge=1, le=10000),
    authorization: str | None = Header(default=None),
):
    """The labelled corpus this workspace has accumulated.

    Scoped to the caller's workspace: an outcome record is the most
    company-specific data MemoryWorks holds and never crosses that boundary.
    """
    principal = _authorize_workspace(authorization)
    if project_id:
        _authorize_project(project_id, authorization)
    records = export_training_records(
        principal["active_workspace_id"],
        project_id=project_id,
        labelled_only=labelled_only,
        limit=limit,
    )
    return {"records": records, "count": len(records)}


@router.post("/briefings")
def create_briefing(
    request: BriefingRequest,
    authorization: str | None = Header(default=None),
):
    """What this company already knows about a change an agent is about to make.

    Every other retrieval endpoint answers a question. This one answers an
    intent, and returns the constraints that intent is about to run into:
    decisions that bind it, incidents that started the same way, the components
    a change here reaches, and whether a person has to agree first.

    It is deliberately model-free. An agent standing in front of a production
    change needs the same answer twice, and it needs every line to carry a
    memory id somebody can open. Serving the briefing also opens a row in the
    outcome ledger, so what the agent does next can be attributed back to the
    context that informed it.
    """
    principal = _authenticate(authorization)
    if request.project_id:
        _authorize_project_for_principal(principal, request.project_id)
    team_ids = _principal_team_ids(principal)

    def search(task: str, project_id: str = "", limit: int = 12) -> dict:
        return _memory_search_core(principal, task, project_id=project_id, limit=limit)

    def list_by_kind(kind: str, project_id: str) -> list[dict]:
        scoped = [project_id] if project_id else _briefing_project_ids(principal)
        found: list[dict] = []
        project_names = {item["id"]: item["name"] for item in rows("SELECT id,name FROM projects")}
        for candidate in scoped:
            for unit in company_memory.list(
                candidate,
                latest=True,
                kind=kind,
                limit=400,
                allowed_team_ids=team_ids,
            ):
                found.append(
                    {
                        **_public_memory_unit(unit),
                        "project_name": project_names.get(candidate, ""),
                    }
                )
        return found

    def precedents(task: str) -> list[dict]:
        # Precedent is per-project, and a briefing without a project has no
        # place to look for one. That is a real absence, not an error.
        if not request.project_id:
            return []
        return learned_skill_matches(request.project_id, task)

    brief = briefing.build(
        task=request.task,
        service=request.service,
        project_id=request.project_id,
        search=search,
        list_by_kind=list_by_kind,
        precedents=precedents,
    )

    # The ledger row is what lets record_briefing_outcome close this loop later.
    # It is best-effort by design: a briefing must still be served if the
    # instrumentation behind it fails. It records every memory the agent was
    # shown, in display order, so an outcome can be attributed to the context
    # that preceded it rather than only to the wording of the task.
    briefing_id = record_context(
        project_id=request.project_id,
        query=f"[briefing] {request.task}",
        result={
            "answer": brief["headline"],
            "answer_scope": "briefing",
            "answer_kind": brief["verdict"],
            "answer_sufficient": brief["verdict"] != "no_memory",
            "evidence": _briefing_evidence(brief),
            "confidence": 0.0,
            "context_envelope": {},
        },
        workspace_id=principal.get("active_workspace_id", ""),
        principal_id=str(principal.get("id") or ""),
        surface=request.surface or "webmcp",
    )
    audit.record(
        "briefing.served",
        f"Briefing ({brief['verdict']}): {request.task[:80]}",
        request.project_id or None,
        actor=str(principal["id"]),
        payload={"briefing_id": briefing_id, "verdict": brief["verdict"]},
    )
    return {**brief, "briefing_id": briefing_id or None}


@router.post("/briefings/outcome")
def record_briefing_outcome(
    request: BriefingOutcomeRequest,
    authorization: str | None = Header(default=None),
):
    """Close the loop a briefing opened: what was done, and whether it worked.

    Both legs are written together because an agent reporting back from another
    site gets one round trip, not two. Nothing here touches company memory —
    this appends an observation to the ledger, which is why it needs no
    approval while proposing a memory does.
    """
    principal = _authorize_workspace(authorization)
    workspace_id = principal["active_workspace_id"]
    try:
        action = record_action(
            context_event_id=request.briefing_id,
            action_type=request.action,
            workspace_id=workspace_id,
            actor=str(principal.get("display_name") or principal["id"]),
            surface=request.surface,
            target=request.target,
            detail=request.detail,
        )
        outcome = record_outcome(
            context_event_id=request.briefing_id,
            outcome=request.outcome,
            workspace_id=workspace_id,
            action_event_id=action["id"],
            signal="agent",
            reason=request.reason,
            detail=request.detail,
        )
    except LookupError:
        raise HTTPException(404, "Unknown briefing") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return {
        "briefing_id": request.briefing_id,
        "action": action,
        "outcome": outcome,
        "recorded": True,
    }


BRIEFING_CITED_GROUPS = (
    "must_read",
    "constraints",
    "prior_incidents",
    "blast_radius",
    "procedures",
)


def _briefing_evidence(brief: dict) -> list[dict]:
    """The memories a briefing showed, one ledger evidence row per memory.

    A briefing never repeats a memory across groups, so display order is also
    rank order. The source is the memory's first one; that is enough to join
    an outcome back to the document a person would open.
    """
    evidence: list[dict] = []
    for group in BRIEFING_CITED_GROUPS:
        for item in brief.get(group) or []:
            memory_id = str(item.get("memory_id") or "")
            if not memory_id:
                continue
            sources = (company_memory.get(memory_id) or {}).get("source_ids") or []
            evidence.append(
                {"memory_id": memory_id, "source_id": sources[0] if sources else "", "group": group}
            )
    return evidence


def _briefing_project_ids(principal: dict) -> list[str]:
    """Every project a briefing may draw on, newest first."""
    visible = _visible_project_ids(principal)
    if visible is not None:
        return sorted(visible)
    return [
        item["project_id"]
        for item in rows(
            "SELECT project_id FROM workspace_projects WHERE workspace_id=?",
            (principal.get("active_workspace_id", ""),),
        )
    ]


@router.post("/work")
def create_memory_work(
    request: MemoryWorkCreateRequest,
    authorization: str | None = Header(default=None),
):
    principal = _authorize_project(request.project_id, authorization, write=True)
    visible = _visible_project_ids(principal)
    try:
        return memory_work.create(
            request.project_id,
            request.objective,
            workspace_id=principal["active_workspace_id"],
            requested_by=principal.get("display_name") or principal["id"],
            workspace_project_ids=sorted(visible or []),
            principal=principal,
            allowed_team_ids=_principal_team_ids(principal),
        )
    except Exception as exc:
        fail(exc)


@router.get("/work")
def list_memory_work(
    project_id: str = "",
    limit: int = Query(100, ge=1, le=500),
    authorization: str | None = Header(default=None),
):
    if project_id:
        _authorize_project(project_id, authorization)
        return memory_work.list(project_id, limit)
    principal = _authorize_workspace(authorization)
    visible = _visible_project_ids(principal) or set()
    return [
        item
        for item in memory_work.list(limit=limit)
        if item.get("project_id") in visible
        and item.get("workspace_id") == principal["active_workspace_id"]
    ]


def _authorize_memory_work(work_id: str, authorization: str | None, *, write: bool = False):
    item = row("SELECT project_id FROM memory_work WHERE id=?", (work_id,))
    if not item:
        raise HTTPException(404, "Memory Work not found")
    principal = _authorize_project(item["project_id"], authorization, write=write)
    return item, principal


@router.get("/work/{work_id}")
def get_memory_work(work_id: str, authorization: str | None = Header(default=None)):
    _authorize_memory_work(work_id, authorization)
    return memory_work.get(work_id)


@router.post("/work/{work_id}/steps/{step_id}/resolve")
def resolve_memory_work_step(
    work_id: str,
    step_id: str,
    request: MemoryWorkResolveRequest,
    authorization: str | None = Header(default=None),
):
    _, principal = _authorize_memory_work(work_id, authorization, write=True)
    try:
        step = row(
            "SELECT connector FROM memory_work_steps WHERE id=? AND work_id=?",
            (step_id, work_id),
        )
        if not step:
            raise ValueError("Work step not found")
        if request.approved and step["connector"] == "slack":
            return memory_work.approve_and_post_slack(
                work_id,
                step_id,
                request.channel_id,
                request.message,
                SlackConnector(_connector_secrets(principal)),
                principal.get("display_name") or principal["id"],
            )
        return memory_work.resolve_step(
            work_id,
            step_id,
            request.approved,
            principal.get("display_name") or principal["id"],
        )
    except ValueError as exc:
        fail(exc)


@router.post("/work/{work_id}/steps/{step_id}/complete")
def complete_memory_work_step(
    work_id: str,
    step_id: str,
    request: MemoryWorkCompleteRequest,
    authorization: str | None = Header(default=None),
):
    _, principal = _authorize_memory_work(work_id, authorization, write=True)
    try:
        return memory_work.complete_step(
            work_id,
            step_id,
            request.output,
            principal.get("display_name") or principal["id"],
        )
    except ValueError as exc:
        fail(exc)


@router.get("/memory/graph/summary")
def memory_graph_summary(project_id: str, authorization: str | None = Header(default=None)):
    principal = _authorize_project(project_id, authorization)
    team_ids = _principal_team_ids(principal)
    summary = _security_trim_graph_summary(project_id, principal)
    return {
        **summary,
        "memory_units": len(company_memory.list(project_id, allowed_team_ids=team_ids)),
        "updates": len(company_memory.relationships(project_id, "UPDATES", team_ids)),
        "conflicts": len(company_memory.relationships(project_id, "CONTRADICTS", team_ids)),
    }


@router.get("/memory/graph/nodes")
def memory_graph_nodes(
    project_id: str,
    node_type: str | None = None,
    limit: int = 500,
    authorization: str | None = Header(default=None),
):
    principal = _authorize_project(project_id, authorization)
    return _security_trim_graph_nodes(
        project_id, graph.list_nodes(project_id, node_type, min(limit, 2000)), principal
    )


@router.get("/memory/graph/edges")
def memory_graph_edges(
    project_id: str,
    relationship: str | None = None,
    limit: int = 500,
    authorization: str | None = Header(default=None),
):
    principal = _authorize_project(project_id, authorization)
    return _security_trim_graph_edges(
        project_id,
        graph.list_edges(project_id, relationship, min(limit, 2000)),
        principal,
    )


@router.get("/memory/units")
def memory_units(
    project_id: str,
    type: str = "",
    latest: bool | None = None,
    authorization: str | None = Header(default=None),
):
    principal = _authorize_project(project_id, authorization)
    return company_memory.list(
        project_id,
        latest=latest,
        kind=type,
        allowed_team_ids=_principal_team_ids(principal),
    )


@router.get("/memory/units/{memory_id}")
def memory_unit(memory_id: str, authorization: str | None = Header(default=None)):
    item = company_memory.get(memory_id)
    if not item:
        raise HTTPException(404, "Memory unit not found")
    principal = _authorize_project(item["project_id"], authorization)
    visible = scopes.visible_memory_ids(item["project_id"], _principal_team_ids(principal))
    if visible is not None and memory_id not in visible:
        raise HTTPException(404, "Memory unit not found")
    return {
        **item,
        "relationships": [
            rel
            for rel in company_memory.relationships(item["project_id"])
            if memory_id in (rel["from_memory_id"], rel["to_memory_id"])
        ],
    }


@router.get("/memory/conflicts")
def memory_conflicts(project_id: str, authorization: str | None = Header(default=None)):
    principal = _authorize_project(project_id, authorization)
    return company_memory.relationships(project_id, "CONTRADICTS", _principal_team_ids(principal))


@router.get("/memory/updates")
def memory_updates(project_id: str, authorization: str | None = Header(default=None)):
    principal = _authorize_project(project_id, authorization)
    return company_memory.relationships(project_id, "UPDATES", _principal_team_ids(principal))


def _public_memory_unit(unit: dict) -> dict:
    return {
        "id": unit.get("id"),
        "project_id": unit.get("project_id"),
        "type": unit.get("type"),
        "subject": unit.get("subject"),
        "content": unit.get("content"),
        "scope": unit.get("scope") or {},
        "confidence": unit.get("confidence"),
        "source_ids": unit.get("source_ids", []),
        "valid_from": unit.get("valid_from"),
        "valid_to": unit.get("valid_to"),
        "is_latest": unit.get("is_latest"),
        "created_at": unit.get("created_at"),
        "updated_at": unit.get("updated_at"),
    }


@router.get("/memory/search")
def memory_search(
    q: str = Query(default="", max_length=400),
    project_id: str = "",
    type: str = "",
    limit: int = Query(10, ge=1, le=50),
    authorization: str | None = Header(default=None),
):
    """Search current company memory across the caller's authorized projects.

    This is a structured retrieval endpoint for agents and UI: it never runs an
    LLM and never returns unauthorized rows. Workspace-wide when no project is
    given, team-trimmed per project through the existing scope service. A type
    filter alone lists that kind of memory (all incidents, all decisions); a
    query ranks by term overlap with structured fields weighted highest.
    """
    principal = _authenticate(authorization)
    return _memory_search_core(principal, q, project_id=project_id, type=type, limit=limit)


def _memory_search_core(
    principal: dict,
    q: str,
    project_id: str = "",
    type: str = "",
    limit: int = 10,
) -> dict:
    """Shared search core for the HTTP route and the WebMCP agent runner."""
    if type and type not in MEMORY_TYPES:
        raise HTTPException(400, f"Unknown memory type; use one of {sorted(MEMORY_TYPES)}")
    workspace_id = principal.get("active_workspace_id", "")
    if project_id:
        _authorize_project_for_principal(principal, project_id)
        project_ids = [project_id]
    else:
        visible = _visible_project_ids(principal)
        if visible is not None:
            project_ids = sorted(visible)
        else:
            project_ids = [
                item["project_id"]
                for item in rows(
                    "SELECT project_id FROM workspace_projects WHERE workspace_id=?",
                    (workspace_id,),
                )
            ]
    team_ids = _principal_team_ids(principal)
    terms = [term for term in re.split(r"\W+", q.casefold()) if term]
    if not terms and not type:
        raise HTTPException(400, "Provide a query, a memory type, or both")
    project_names = {item["id"]: item["name"] for item in rows("SELECT id,name FROM projects")}
    units = [
        {**unit, "project_name": project_names.get(candidate_project, "")}
        for candidate_project in project_ids
        for unit in company_memory.list(
            candidate_project,
            latest=True,
            kind=type,
            limit=2000,
            allowed_team_ids=team_ids,
        )
    ]
    if terms:
        # Ranked across every visible project at once, so a word's rarity is
        # judged against everything this caller could have been shown.
        ranked = rank_memories(units, q, limit)
    else:
        units.sort(key=lambda unit: unit.get("updated_at") or "", reverse=True)
        ranked = [(unit, 1.0) for unit in units]
    matches = [
        {
            **_public_memory_unit(unit),
            "project_name": unit["project_name"],
            "score": round(score, 3),
        }
        for unit, score in ranked
    ]
    return {
        "query": q,
        "project_id": project_id or None,
        "searched_projects": len(project_ids),
        "results": matches[:limit],
    }


@router.get("/memory/units/{memory_id}/related")
def memory_unit_related(memory_id: str, authorization: str | None = Header(default=None)):
    """Resolve the relationships around one memory into readable units.

    Covers UPDATES/CONTRADICTS/SUPPORTS/EXTENDS/DERIVES edges in both
    directions plus same-subject current memories, so an agent can follow the
    history of a subject without stitching relationships itself.
    """
    principal = _authenticate(authorization)
    return _memory_related_core(principal, memory_id)


def _memory_related_core(principal: dict, memory_id: str) -> dict:
    """Shared related-memories core for the HTTP route and the agent runner."""
    item = company_memory.get(memory_id)
    if not item:
        raise HTTPException(404, "Memory unit not found")
    _authorize_project_for_principal(principal, item["project_id"])
    team_ids = _principal_team_ids(principal)
    visible = scopes.visible_memory_ids(item["project_id"], team_ids)
    related: dict[str, dict] = {}

    def add(other_id: str, relationship: str, linked_at: str) -> None:
        if other_id == memory_id or other_id in related:
            return
        if visible is not None and other_id not in visible:
            return
        other = company_memory.get(other_id)
        if other:
            related[other_id] = {
                "relationship": relationship,
                "linked_at": linked_at,
                "memory": _public_memory_unit(other),
            }

    for rel in company_memory.relationships(item["project_id"], "", team_ids):
        if rel["from_memory_id"] == memory_id:
            add(rel["to_memory_id"], rel["relationship"], rel.get("created_at", ""))
        elif rel["to_memory_id"] == memory_id:
            add(rel["from_memory_id"], rel["relationship"], rel.get("created_at", ""))
    for unit in company_memory.list(
        item["project_id"], latest=True, limit=2000, allowed_team_ids=team_ids
    ):
        if unit["subject"].casefold() == item["subject"].casefold():
            add(unit["id"], "SAME_SUBJECT", unit.get("updated_at", ""))
    return {
        "memory_id": memory_id,
        "related": sorted(related.values(), key=lambda entry: entry["linked_at"], reverse=True),
    }


def _authorized_space_ids(principal: dict) -> list[str]:
    """Every project in the active workspace this principal may read."""
    visible = _visible_project_ids(principal)
    if visible is not None:
        return sorted(visible)
    return [
        item["project_id"]
        for item in rows(
            "SELECT project_id FROM workspace_projects WHERE workspace_id=?",
            (principal.get("active_workspace_id", ""),),
        )
    ]


def _service_context_core(principal: dict, service: str) -> list[dict]:
    """Assembled service profile per authorized space, skipping empty ones."""
    service = service.strip()
    if not service:
        raise HTTPException(400, "service is required")
    team_ids = _principal_team_ids(principal)
    project_names = {item["id"]: item["name"] for item in rows("SELECT id,name FROM projects")}
    entries: list[dict] = []
    for project_id in _authorized_space_ids(principal):
        try:
            profile = company_memory.profile(
                project_id, "service", service, allowed_team_ids=team_ids
            )
        except Exception:
            continue
        total = sum(
            len(profile.get(group) or [])
            for group in (
                "current_facts",
                "decisions",
                "incidents",
                "dependencies",
                "owners",
                "procedures",
                "policies",
            )
        )
        if total <= 0:
            continue
        entries.append(
            {
                "project_id": project_id,
                "project_name": project_names.get(project_id, ""),
                "profile": profile,
            }
        )
    return entries


def _visible_runbooks_core(principal: dict, service: str = "", issue: str = "") -> list[dict]:
    """Runbooks the principal can see, optionally filtered by service and issue."""
    visible = _visible_project_ids(principal)
    records = runbooks.list()
    if visible is not None:
        records = [item for item in records if item["project_id"] in visible]
    project_names = {item["id"]: item["name"] for item in rows("SELECT id,name FROM projects")}
    needle = service.strip().casefold()
    issue_needle = issue.strip().casefold()
    matches: list[dict] = []
    for record in records:
        haystack = " ".join(
            str(part or "")
            for part in (
                record.get("key"),
                record.get("title"),
                record.get("trigger"),
                json.dumps(record.get("procedures") or []),
                json.dumps(record.get("steps") or []),
            )
        ).casefold()
        if needle and needle not in haystack:
            continue
        if issue_needle and issue_needle not in haystack:
            continue
        matches.append({**record, "project_name": project_names.get(record["project_id"], "")})
    return matches


@router.get("/memory/profiles/company")
def company_profile(project_id: str, authorization: str | None = Header(default=None)):
    principal = _authorize_project(project_id, authorization)
    return company_memory.profile(
        project_id, "company", allowed_team_ids=_principal_team_ids(principal)
    )


@router.get("/memory/profiles/project/{project_id}")
def project_memory_profile(project_id: str, authorization: str | None = Header(default=None)):
    principal = _authorize_project(project_id, authorization)
    return company_memory.profile(
        project_id, "project", allowed_team_ids=_principal_team_ids(principal)
    )


@router.get("/memory/profiles/repo/{repo_id}")
def repo_memory_profile(
    repo_id: str, project_id: str, authorization: str | None = Header(default=None)
):
    principal = _authorize_project(project_id, authorization)
    return company_memory.profile(project_id, "repo", repo_id, _principal_team_ids(principal))


@router.get("/memory/profiles/service/{service_name}")
def service_memory_profile(
    service_name: str, project_id: str, authorization: str | None = Header(default=None)
):
    principal = _authorize_project(project_id, authorization)
    return company_memory.profile(
        project_id, "service", service_name, _principal_team_ids(principal)
    )


@router.get("/memory/source-revisions")
def memory_source_revisions(
    project_id: str,
    source_id: str = "",
    authorization: str | None = Header(default=None),
):
    principal = _authorize_project(project_id, authorization)
    if source_id:
        visible = scopes.visible_source_ids(project_id, {source_id}, _principal_team_ids(principal))
        if source_id not in visible:
            raise HTTPException(404, "Source not found")
    return company_brain.list_revisions(project_id, source_id)


@router.get("/memory/change-sets")
def memory_change_sets(
    project_id: str,
    limit: int = Query(100, ge=1, le=500),
    authorization: str | None = Header(default=None),
):
    principal = _authorize_project(project_id, authorization)
    records = company_brain.list_change_sets(project_id, limit)
    team_ids = _principal_team_ids(principal)
    if team_ids is None:
        return records
    source_ids = {item["source_id"] for item in records}
    visible = scopes.visible_source_ids(project_id, source_ids, team_ids)
    return [item for item in records if item["source_id"] in visible]


@router.post("/memory/artifacts")
def save_memory_artifact(
    request: ArtifactSaveRequest, authorization: str | None = Header(default=None)
):
    principal = _authorize_project(request.project_id, authorization, write=True)
    team_ids = _principal_team_ids(principal)
    visible_memories = scopes.visible_memory_ids(request.project_id, team_ids)
    if visible_memories is not None and not set(request.memory_ids).issubset(visible_memories):
        raise HTTPException(403, "Artifact references memory outside the authorized scope")
    visible_sources = scopes.visible_source_ids(
        request.project_id, set(request.source_ids), team_ids
    )
    if not set(request.source_ids).issubset(visible_sources):
        raise HTTPException(403, "Artifact references a source outside the authorized scope")
    return company_brain.save_artifact(
        request.project_id,
        request.name,
        request.artifact_type,
        request.content,
        request.source_ids,
        request.memory_ids,
        request.context_envelope_id,
    )


@router.get("/memory/artifacts")
def list_memory_artifacts(project_id: str, authorization: str | None = Header(default=None)):
    principal = _authorize_project(project_id, authorization)
    records = company_brain.list_artifacts(project_id)
    team_ids = _principal_team_ids(principal)
    if team_ids is None:
        return records
    output = []
    for artifact in records:
        current = next(
            (
                revision
                for revision in artifact.get("revisions", [])
                if revision["id"] == artifact.get("current_revision_id")
            ),
            {},
        )
        source_ids = set(current.get("source_ids", []))
        if source_ids.issubset(scopes.visible_source_ids(project_id, source_ids, team_ids)):
            output.append(artifact)
    return output


@router.post("/memory/skills/compile")
def compile_memory_skill(
    request: SkillCompileRequest, authorization: str | None = Header(default=None)
):
    principal = _authorize_project(request.project_id, authorization, write=True)
    team_ids = _principal_team_ids(principal)
    if request.team_id and team_ids is not None and request.team_id not in team_ids:
        raise HTTPException(403, "Skill scope is outside the caller's teams")
    current = company_memory.list(
        request.project_id, latest=True, limit=1000, allowed_team_ids=team_ids
    )
    try:
        return company_brain.compile_skill(
            request.project_id, request.name, current, request.team_id
        )
    except Exception as exc:
        fail(exc)


@router.get("/memory/skills")
def list_memory_skills(
    project_id: str,
    status: str = "",
    authorization: str | None = Header(default=None),
):
    principal = _authorize_project(project_id, authorization)
    team_ids = _principal_team_ids(principal)
    records = company_brain.list_skills(project_id, status)
    if team_ids is None:
        return records
    return [item for item in records if not item.get("team_id") or item["team_id"] in team_ids]


@router.get("/memory/context/{envelope_id}")
def get_context_envelope(envelope_id: str, authorization: str | None = Header(default=None)):
    item = row("SELECT * FROM context_envelopes WHERE id=?", (envelope_id,))
    if not item:
        raise HTTPException(404, "Context envelope not found")
    principal = _authorize_project(item["project_id"], authorization)
    if principal["role"] not in {"owner", "admin"} and item["principal_id"] != principal["id"]:
        raise HTTPException(403, "Context envelope belongs to another principal")
    return decode(item)


@router.get("/memory/swarm/{run_id}")
def get_context_activation_run(run_id: str, authorization: str | None = Header(default=None)):
    item = row("SELECT * FROM context_activation_runs WHERE id=?", (run_id,))
    if not item:
        raise HTTPException(404, "Context activation run not found")
    principal = _authorize_project(item["project_id"], authorization)
    if principal["role"] not in {"owner", "admin"} and item["principal_id"] != principal["id"]:
        raise HTTPException(403, "Context activation run belongs to another principal")
    return decode(item)


@router.post("/runbooks/extract")
def extract_runbooks(request: ExtractRequest, authorization: str | None = Header(default=None)):
    _authorize_project(request.project_id, authorization, write=True)
    return runbooks.extract(request.project_id, request.query)


@router.get("/runbooks")
def list_runbooks(project_id: str | None = None, authorization: str | None = Header(default=None)):
    principal = _authenticate(authorization)
    if project_id:
        _authorize_project(project_id, authorization)
        return runbooks.list(project_id)
    visible = _visible_project_ids(principal)
    records = runbooks.list()
    return (
        records if visible is None else [item for item in records if item["project_id"] in visible]
    )


@router.get("/runbooks/{runbook_id}")
def get_runbook(
    runbook_id: str,
    project_id: str | None = None,
    authorization: str | None = Header(default=None),
):
    result = runbooks.get(runbook_id, project_id)
    if not result:
        raise HTTPException(404, "Runbook not found")
    _authorize_project(result["project_id"], authorization)
    return result


@router.get("/projects/{project_id}/change-impacts")
def list_change_impacts(project_id: str, authorization: str | None = Header(default=None)):
    _authorize_project(project_id, authorization)
    return change_impacts.list(project_id)


@router.get("/change-impacts/{impact_id}")
def get_change_impact(impact_id: str, authorization: str | None = Header(default=None)):
    result = change_impacts.get(impact_id)
    if not result:
        raise HTTPException(404, "Change impact not found")
    _authorize_project(result["project_id"], authorization)
    return result


@router.post("/projects/{project_id}/change-impacts/analyze")
def analyze_change_impact(
    project_id: str,
    request: ChangeImpactAnalyzeRequest,
    authorization: str | None = Header(default=None),
):
    _authorize_project(project_id, authorization, write=True)
    try:
        return change_impacts.analyze(project_id, request.model_dump())
    except Exception as exc:
        fail(exc)


@router.get("/projects/{project_id}/assertions")
def list_assertions(
    project_id: str,
    status: str | None = None,
    authorization: str | None = Header(default=None),
):
    _authorize_project(project_id, authorization)
    return assertions.list(project_id, status)


@router.post("/projects/{project_id}/assertions/suggest-owners")
def suggest_assertion_owners(project_id: str, authorization: str | None = Header(default=None)):
    _authorize_project(project_id, authorization, write=True)
    return assertions.assign_suggested_owners(project_id)


@router.post("/projects/{project_id}/assertions/bulk-review")
def bulk_review_assertions(
    project_id: str,
    request: BulkAssertionReviewRequest,
    authorization: str | None = Header(default=None),
):
    principal = _authorize_project(project_id, authorization, write=True)
    try:
        return assertions.bulk_review(
            project_id,
            request.assertion_ids,
            request.action,
            principal.get("display_name") or request.actor,
            request.reason,
            request.owner,
        )
    except Exception as exc:
        fail(exc)


@router.get("/assertions/{assertion_id}")
def get_assertion(assertion_id: str, authorization: str | None = Header(default=None)):
    result = assertions.get(assertion_id)
    if not result:
        raise HTTPException(404, "Assertion not found")
    _authorize_project(result["project_id"], authorization)
    return result


def _assertion_decision(
    assertion_id: str,
    action: str,
    request: AssertionDecisionRequest,
    authorization: str | None,
):
    assertion = assertions.get(assertion_id)
    if not assertion:
        raise HTTPException(404, "Assertion not found")
    _authorize_project(assertion["project_id"], authorization, write=True)
    try:
        return assertions.transition(
            assertion_id,
            action,
            request.actor,
            request.reason,
            assertion["project_id"],
            request.superseded_by,
        )
    except Exception as exc:
        fail(exc)


@router.post("/assertions/{assertion_id}/verify")
def verify_assertion(
    assertion_id: str,
    request: AssertionDecisionRequest,
    authorization: str | None = Header(default=None),
):
    return _assertion_decision(assertion_id, "verify", request, authorization)


@router.post("/assertions/{assertion_id}/mark-stale")
def stale_assertion(
    assertion_id: str,
    request: AssertionDecisionRequest,
    authorization: str | None = Header(default=None),
):
    return _assertion_decision(assertion_id, "mark_stale", request, authorization)


@router.post("/assertions/{assertion_id}/supersede")
def supersede_assertion(
    assertion_id: str,
    request: AssertionDecisionRequest,
    authorization: str | None = Header(default=None),
):
    return _assertion_decision(assertion_id, "supersede", request, authorization)


@router.post("/assertions/{assertion_id}/dismiss")
def dismiss_assertion(
    assertion_id: str,
    request: AssertionDecisionRequest,
    authorization: str | None = Header(default=None),
):
    return _assertion_decision(assertion_id, "dismiss", request, authorization)


@router.post("/actions/propose")
def propose_action(request: ProposeRequest, authorization: str | None = Header(default=None)):
    _authorize_project(request.project_id, authorization, write=True)
    try:
        return approvals.propose(
            request.project_id, request.runbook_id, request.action_id, request.params
        )
    except Exception as exc:
        fail(exc)


def _resolve_action(request: ResolveRequest, approved: bool, authorization: str | None) -> dict:
    action = row("SELECT project_id FROM actions WHERE id=?", (request.action_id,))
    if not action:
        raise HTTPException(404, "Action not found")
    _authorize_project(action["project_id"], authorization, write=True)
    principal = _authorize_workspace(authorization, admin=True)
    try:
        return approvals.resolve(request.action_id, approved, principal["display_name"])
    except Exception as exc:
        fail(exc)


@router.post("/actions/approve")
def approve_action(request: ResolveRequest, authorization: str | None = Header(default=None)):
    return _resolve_action(request, True, authorization)


@router.post("/actions/deny")
def deny_action(request: ResolveRequest, authorization: str | None = Header(default=None)):
    return _resolve_action(request, False, authorization)


@router.get("/actions/pending")
def pending_actions(
    project_id: str | None = None, authorization: str | None = Header(default=None)
):
    principal = _authenticate(authorization)
    if project_id:
        _authorize_project(project_id, authorization)
        return approvals.list("pending", project_id)
    visible = _visible_project_ids(principal)
    records = approvals.list("pending")
    return (
        records if visible is None else [item for item in records if item["project_id"] in visible]
    )


@router.get("/actions")
def all_actions(project_id: str | None = None, authorization: str | None = Header(default=None)):
    principal = _authenticate(authorization)
    if project_id:
        _authorize_project(project_id, authorization)
        return approvals.list(None, project_id)
    visible = _visible_project_ids(principal)
    records = approvals.list()
    return (
        records if visible is None else [item for item in records if item["project_id"] in visible]
    )


@router.get("/audit/actions")
def action_audit(project_id: str | None = None, authorization: str | None = Header(default=None)):
    return all_actions(project_id, authorization)


@router.get("/audit")
def audit_log(
    project_id: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    authorization: str | None = Header(default=None),
):
    principal = _authenticate(authorization)
    if project_id:
        _authorize_project(project_id, authorization)
        return audit.list(project_id, limit)
    visible = _visible_project_ids(principal)
    records = audit.list(None, limit)
    return (
        records
        if visible is None
        else [item for item in records if item.get("project_id") in visible]
    )


@router.get("/projects/{project_id}/graph/blast-radius/{service_name}")
def project_blast_radius(
    project_id: str, service_name: str, authorization: str | None = Header(default=None)
):
    _authorize_project(project_id, authorization)
    try:
        return blast_radius(graph, project_id, service_name)
    except Exception as exc:
        fail(exc)


@router.post("/simulate")
def simulate(request: SimulateRequest, authorization: str | None = Header(default=None)):
    _authorize_project(request.project_id, authorization)
    try:
        return simulation.simulate(
            request.project_id,
            request.runbook_id,
            request.scenario,
            request.environment,
            request.params,
        )
    except Exception as exc:
        fail(exc)


@router.post("/correlate")
def correlate(request: CorrelateRequest, authorization: str | None = Header(default=None)):
    _authorize_project(request.project_id, authorization)
    try:
        route = hcag.route_query(request.project_id, request.query or request.service_name or "")
        evidence = hcag.retrieve_context(
            request.project_id,
            request.query or f"{request.service_name} failure",
            request.service_name or route.service_name,
        )
        return correlate_changes(
            request.project_id, evidence, request.service_name or route.service_name
        )
    except Exception as exc:
        fail(exc)


@router.get("/runbooks/{runbook_id}/drift")
def runbook_drift(
    runbook_id: str,
    project_id: str | None = None,
    authorization: str | None = Header(default=None),
):
    try:
        result = drift.check_runbook(runbook_id, project_id)
        _authorize_project(result["project_id"], authorization)
        return result
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        fail(exc)


@router.get("/projects/{project_id}/drift")
def project_drift(project_id: str, authorization: str | None = Header(default=None)):
    _authorize_project(project_id, authorization)
    try:
        return drift.check_project(project_id)
    except Exception as exc:
        fail(exc)


@router.get("/projects/{project_id}/memories")
def list_memories(
    project_id: str,
    status: str | None = None,
    authorization: str | None = Header(default=None),
):
    _authorize_project(project_id, authorization)
    return memories.list(project_id, status)


@router.post("/projects/{project_id}/memories/derive")
def derive_memories(project_id: str, authorization: str | None = Header(default=None)):
    _authorize_project(project_id, authorization, write=True)
    try:
        return memories.derive(project_id)
    except Exception as exc:
        fail(exc)


def _resolve_memory(
    memory_id: str,
    request: MemoryResolveRequest,
    approved: bool,
    authorization: str | None,
) -> dict:
    memory = memories.get(memory_id)
    if not memory:
        raise HTTPException(404, "Memory not found")
    _authorize_project(memory["project_id"], authorization, write=True)
    try:
        return memories.resolve(memory_id, approved, request.resolved_by)
    except Exception as exc:
        fail(exc)


@router.post("/memories/{memory_id}/approve")
def approve_memory(
    memory_id: str,
    request: MemoryResolveRequest,
    authorization: str | None = Header(default=None),
):
    return _resolve_memory(memory_id, request, True, authorization)


@router.post("/memories/{memory_id}/reject")
def reject_memory(
    memory_id: str,
    request: MemoryResolveRequest,
    authorization: str | None = Header(default=None),
):
    return _resolve_memory(memory_id, request, False, authorization)


@router.get("/importers")
def importers():
    return importer_statuses()


@router.post("/importers/{name}/import")
def run_importer(
    name: str,
    request: ImporterRunRequest,
    authorization: str | None = Header(default=None),
):
    _authorize_project(request.project_id, authorization, write=True)
    try:
        importer = get_importer(name)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    try:
        method = getattr(importer, f"import_{request.resource}")
        if request.resource == "incidents":
            result = method(ingestion, request.project_id, request.limit)
        else:
            result = method(ingestion, request.project_id)
        audit.record(
            "importer.ran",
            f"Imported {request.resource} from {importer.label}",
            request.project_id,
            payload=result,
        )
        return result
    except NotConnectedError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        fail(exc)


@router.get("/keys")
def api_keys(workspace_id: str = "", authorization: str | None = Header(default=None)):
    principal = _authorize_workspace(authorization, workspace_id, admin=True)
    return list_api_keys(principal["active_workspace_id"])


@router.post("/keys")
def create_key(request: ApiKeyCreateRequest, authorization: str | None = Header(default=None)):
    principal = _authorize_workspace(authorization, request.workspace_id, admin=True)
    try:
        result = create_api_key(
            request.name,
            principal["active_workspace_id"],
            created_by=principal["id"],
        )
        audit.record(
            "api_key.created",
            f"Created API key {request.name}",
            actor=principal["id"],
            payload={"key_id": result["id"], "workspace_id": result["workspace_id"]},
        )
        return result
    except Exception as exc:
        fail(exc)


@router.delete("/keys/{key_id}")
def revoke_key(key_id: str, authorization: str | None = Header(default=None)):
    principal = _authorize_workspace(authorization, admin=True)
    key = next(
        (item for item in list_api_keys(principal["active_workspace_id"]) if item["id"] == key_id),
        None,
    )
    if not key:
        raise HTTPException(404, "API key not found")
    try:
        result = revoke_api_key(key_id)
        audit.record("api_key.revoked", f"Revoked API key {key_id}", actor=principal["id"])
        return result
    except Exception as exc:
        fail(exc)


@router.get("/benchmarks")
def benchmark_reports():
    """Serve the latest HCAG benchmark report if one has been generated.

    Reports are only ever produced by running `make benchmark` in the hcag
    project; this endpoint never fabricates metrics.
    """
    candidates = [
        settings.hcag_path / "benchmark_reports" / "latest.json",
        settings.local_repo_mount / "hcag" / "benchmark_reports" / "latest.json",
    ]
    for path in candidates:
        try:
            if path.exists():
                return {
                    "available": True,
                    "path": str(path),
                    "report": json.loads(path.read_text()),
                }
        except Exception:
            continue
    return {
        "available": False,
        "report": None,
        "reason": "No benchmark report found. Run `make benchmark` in the hcag project to generate one.",
    }


def _connector_secrets(principal: dict) -> ConnectorSecrets:
    return ConnectorSecrets(principal["active_workspace_id"], principal["id"])


@router.get("/connectors")
def connectors(authorization: str | None = Header(default=None)):
    principal = _authorize_workspace(authorization)
    return connector_runtime.list_connectors(principal)


@router.get("/connectors/catalog")
def connectors_catalog(authorization: str | None = Header(default=None)):
    principal = _authorize_workspace(authorization)
    return connector_runtime.catalog(principal)


@router.get("/connectors/coverage")
def connector_coverage(authorization: str | None = Header(default=None)):
    principal = _authorize_workspace(authorization)
    project_ids = sorted(_visible_project_ids(principal) or set())
    counts: dict[str, int] = {}
    if project_ids:
        placeholders = ",".join("?" for _ in project_ids)
        counts = {
            item["source_type"]: int(item["count"])
            for item in rows(
                f"SELECT source_type,COUNT(*) count FROM knowledge_items WHERE project_id IN ({placeholders}) GROUP BY source_type",
                tuple(project_ids),
            )
        }
    statuses = {item["provider"]: item for item in connector_runtime.list_connectors(principal)}
    return {
        "scope": {
            "workspace_id": principal["active_workspace_id"],
            "projects_indexed": len(project_ids),
            "access_boundary": "currently authorized accounts only",
        },
        "sources": [
            {
                "provider": manifest.id,
                "connected": bool(statuses.get(manifest.id, {}).get("connected")),
                "indexed": {manifest.id: counts.get(manifest.id, 0)},
                "resources": [resource.type for resource in manifest.resources],
                "not_indexed": [
                    "resources outside the delegated user's grant",
                    "secret values and credentials",
                ],
                "refresh_mode": "durable incremental cursor plus signed webhooks",
                "data_policy": manifest.public_dict()["data_policy"],
            }
            for manifest in connector_runtime.registry.manifests()
        ],
        "safety": {
            "secret_values": "excluded before storage and embedding",
            "environment_variables": "names and documented purpose only",
            "credentials_in_ui_or_logs": False,
        },
    }


@router.get("/connectors/{provider}/status")
def connector_status(provider: str, authorization: str | None = Header(default=None)):
    principal = _authorize_workspace(authorization)
    try:
        return connector_runtime.connector(provider, principal).status().__dict__
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/connectors/{provider}/resources")
def connector_resources(provider: str, authorization: str | None = Header(default=None)):
    principal = _authorize_workspace(authorization)
    try:
        return connector_runtime.discover(provider, principal)
    except Exception as exc:
        fail(exc)


@router.post("/connectors/{provider}/tools/{tool_name}")
def invoke_connector_tool(
    provider: str,
    tool_name: str,
    request: ConnectorToolInvokeRequest,
    authorization: str | None = Header(default=None),
):
    principal = _authorize_workspace(authorization)
    try:
        return connector_runtime.invoke(
            provider,
            tool_name,
            request.arguments,
            principal,
            idempotency_key=request.idempotency_key,
        )
    except Exception as exc:
        fail(exc)


@router.get("/connector-tool-calls")
def connector_tool_calls(status: str = "", authorization: str | None = Header(default=None)):
    principal = _authorize_workspace(authorization)
    return connector_runtime.list_tool_calls(principal, status)


@router.post("/connector-tool-calls/{call_id}/resolve")
def resolve_connector_tool_call(
    call_id: str,
    request: ConnectorToolResolveRequest,
    authorization: str | None = Header(default=None),
):
    principal = _authorize_workspace(authorization)
    try:
        return connector_runtime.resolve_write(call_id, request.approved, principal)
    except Exception as exc:
        fail(exc)


@router.post("/connectors/{provider}/sync")
def enqueue_connector_sync(
    provider: str,
    request: ConnectorSyncRequest,
    authorization: str | None = Header(default=None),
):
    principal = _authorize_workspace(authorization)
    if request.project_id:
        _authorize_project(request.project_id, authorization, write=True)
    try:
        return connector_sync.enqueue(
            provider,
            principal["active_workspace_id"],
            principal["id"],
            request.resource_id,
            project_id=request.project_id,
            cursor=request.cursor,
            idempotency_key=request.idempotency_key,
        )
    except Exception as exc:
        fail(exc)


@router.get("/connector-sync-jobs")
def connector_sync_jobs(status: str = "", authorization: str | None = Header(default=None)):
    principal = _authorize_workspace(authorization)
    return connector_sync.list(principal["active_workspace_id"], status)


def _public_repository_refresh_request(record: dict) -> dict:
    result = {}
    try:
        result = json.loads(record.get("result_json") or "{}")
    except json.JSONDecodeError:
        result = {}
    requester = (
        row("SELECT display_name,email FROM users WHERE id=?", (record.get("user_id"),)) or {}
    )
    project = row("SELECT name FROM projects WHERE id=?", (record.get("project_id"),)) or {}
    return {
        key: record.get(key)
        for key in (
            "id",
            "project_id",
            "repository",
            "reason",
            "status",
            "requested_at",
            "resolved_at",
            "resolved_by",
            "started_at",
            "completed_at",
            "error",
        )
    } | {
        "result": result,
        # Whoever must decide this request should see who is asking without a
        # second lookup; an approval made blind is not much of a decision.
        "requested_by_id": record.get("user_id"),
        "requested_by_name": requester.get("display_name") or record.get("user_id"),
        "requested_by_email": requester.get("email") or "",
        "project_name": project.get("name") or "",
    }


def _run_repository_refresh(request_id: str) -> None:
    record = row("SELECT * FROM repository_refresh_requests WHERE id=?", (request_id,))
    if not record or record["status"] != "queued":
        return
    now = utcnow()
    with connect() as conn:
        claimed = conn.execute(
            """UPDATE repository_refresh_requests SET status='running',started_at=?
            WHERE id=? AND status='queued'""",
            (now, request_id),
        ).rowcount
    if not claimed:
        return
    try:
        project = row("SELECT name,repository FROM projects WHERE id=?", (record["project_id"],))
        if not project or not project["repository"]:
            raise ValueError("The selected project no longer has a GitHub repository")
        connector = GitHubConnector(ConnectorSecrets(record["workspace_id"], record["user_id"]))
        result = RepositoryIngestor(ingestion, graph, connector).ingest(
            project["repository"], project["name"]
        )
        repository_resource = GitHubConnector.slug(project["repository"]) or project["repository"]
        connector_sync.enqueue(
            "github",
            record["workspace_id"],
            record["user_id"],
            repository_resource,
            project_id=record["project_id"],
            cursor={"repository": repository_resource},
            idempotency_key=f"approved-refresh:{request_id}",
        )
        with connect() as conn:
            conn.execute(
                """UPDATE repository_refresh_requests
                SET status='succeeded',completed_at=?,result_json=?,error='' WHERE id=?""",
                (utcnow(), json.dumps(result), request_id),
            )
        audit.record(
            "repository.refresh.succeeded",
            f"Refreshed {project['name']}",
            record["project_id"],
            payload={
                "refresh_request_id": request_id,
                "files_scanned": int(result.get("files_scanned", 0) or 0),
                "sources_changed": int(
                    (result.get("incremental") or {}).get("sources_changed", 0) or 0
                ),
            },
        )
    except Exception as exc:
        with connect() as conn:
            conn.execute(
                """UPDATE repository_refresh_requests
                SET status='failed',completed_at=?,error=? WHERE id=?""",
                (utcnow(), str(exc), request_id),
            )
        audit.record(
            "repository.refresh.failed",
            "Repository refresh failed",
            record["project_id"],
            payload={"refresh_request_id": request_id, "error": str(exc)},
        )


@router.post("/repository-refresh-requests")
def propose_repository_refresh(
    request: RepositoryRefreshProposalRequest,
    authorization: str | None = Header(default=None),
):
    principal = _authorize_project(request.project_id, authorization, write=True)
    project = row("SELECT name,repository FROM projects WHERE id=?", (request.project_id,)) or {}
    repository = str(project.get("repository") or "")
    if not GitHubConnector.slug(repository):
        raise HTTPException(400, "This memory space is not connected to a GitHub repository")
    reason = request.reason.strip()
    idempotency_key = hashlib.sha256(
        f"{request.project_id}:{reason.casefold()}".encode()
    ).hexdigest()
    existing = row(
        """SELECT * FROM repository_refresh_requests
        WHERE workspace_id=? AND user_id=? AND project_id=? AND idempotency_key=?""",
        (principal["active_workspace_id"], principal["id"], request.project_id, idempotency_key),
    )
    if existing:
        return _public_repository_refresh_request(existing)
    request_id, now = new_id("refresh"), utcnow()
    with connect() as conn:
        conn.execute(
            """INSERT INTO repository_refresh_requests
            (id,workspace_id,user_id,project_id,repository,reason,idempotency_key,status,requested_at)
            VALUES (?,?,?,?,?,?,?,'pending_approval',?)""",
            (
                request_id,
                principal["active_workspace_id"],
                principal["id"],
                request.project_id,
                repository,
                reason,
                idempotency_key,
                now,
            ),
        )
    audit.record(
        "repository.refresh.proposed",
        f"Refresh requested for {project.get('name') or request.project_id}",
        request.project_id,
        actor=str(principal["id"]),
        payload={"refresh_request_id": request_id, "reason": reason, "repository": repository},
    )
    return _public_repository_refresh_request(
        row("SELECT * FROM repository_refresh_requests WHERE id=?", (request_id,)) or {}
    )


@router.get("/repository-refresh-requests")
def repository_refresh_requests(status: str = "", authorization: str | None = Header(default=None)):
    principal = _authorize_workspace(authorization)
    records = rows(
        """SELECT * FROM repository_refresh_requests WHERE workspace_id=?
        AND (?='' OR status=?) ORDER BY requested_at DESC""",
        (principal["active_workspace_id"], status, status),
    )
    visible_project_ids = _visible_project_ids(principal)
    if visible_project_ids is not None:
        records = [record for record in records if record["project_id"] in visible_project_ids]
    # Members can follow the requests they made, but they do not receive a
    # workspace-wide approval queue. That queue contains colleague identity and
    # operational intent, and belongs to the people responsible for decisions.
    if principal["role"] not in {"owner", "admin"}:
        records = [record for record in records if record["user_id"] == principal["id"]]
    return [_public_repository_refresh_request(record) for record in records]


@router.post("/repository-refresh-requests/{request_id}/resolve")
def resolve_repository_refresh(
    request_id: str,
    request: RepositoryRefreshResolutionRequest,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
):
    # The requester deliberately cannot approve their own operation. A WebMCP
    # call uses the same browser session as the person looking at the inbox, so
    # this boundary applies equally to buttons and browser-native tools.
    principal = _authorize_workspace(authorization, admin=True)
    record = row("SELECT * FROM repository_refresh_requests WHERE id=?", (request_id,))
    if not record or record["workspace_id"] != principal["active_workspace_id"]:
        raise HTTPException(404, "Repository refresh request not found")
    _authorize_project(record["project_id"], authorization, write=True)
    if record["status"] != "pending_approval":
        raise HTTPException(400, "Repository refresh request is not pending approval")
    status = "queued" if request.approved else "denied"
    now = utcnow()
    with connect() as conn:
        conn.execute(
            """UPDATE repository_refresh_requests SET status=?,resolved_at=?,resolved_by=?
            WHERE id=?""",
            (status, now, principal["id"], request_id),
        )
    audit.record(
        f"repository.refresh.{status}",
        f"Repository refresh {status}",
        record["project_id"],
        actor=str(principal["id"]),
        payload={"refresh_request_id": request_id},
    )
    if request.approved:
        background_tasks.add_task(_run_repository_refresh, request_id)
    return _public_repository_refresh_request(
        row("SELECT * FROM repository_refresh_requests WHERE id=?", (request_id,)) or {}
    )


PROPOSABLE_MEMORY_KINDS = (
    "fact",
    "decision",
    "incident",
    "procedure",
    "policy",
    "convention",
    "config",
    "ownership",
    "dependency",
    "preference",
    "open_question",
)


def _public_memory_proposal(record: dict) -> dict:
    requester = (
        row("SELECT display_name,email FROM users WHERE id=?", (record.get("user_id"),)) or {}
    )
    project = row("SELECT name FROM projects WHERE id=?", (record.get("project_id"),)) or {}
    return {
        key: record.get(key)
        for key in (
            "id",
            "project_id",
            "kind",
            "subject",
            "content",
            "service",
            "reason",
            "origin",
            "status",
            "requested_at",
            "resolved_at",
            "resolved_by",
            "memory_id",
        )
    } | {
        "requested_by_id": record.get("user_id"),
        "requested_by_name": requester.get("display_name") or record.get("user_id"),
        "requested_by_email": requester.get("email") or "",
        "project_name": project.get("name") or "",
    }


@router.post("/memory/proposals")
def propose_memory(
    request: MemoryProposalRequest, authorization: str | None = Header(default=None)
):
    """Queue a proposed memory for human approval; persist nothing yet.

    Browser agents and API clients use this to record verified organizational
    knowledge. The proposal is idempotent and side-effect free: company memory
    only changes after an owner or admin resolves the proposal.
    """
    principal = _authorize_project(request.project_id, authorization, write=True)
    return _propose_memory_core(
        principal,
        request.project_id,
        request.kind,
        request.subject,
        request.content,
        service=request.service,
        reason=request.reason,
    )


def _propose_memory_core(
    principal: dict,
    project_id: str,
    kind: str,
    subject: str,
    content: str,
    service: str = "",
    reason: str = "",
) -> dict:
    """Shared proposal core for the HTTP route and the WebMCP agent runner."""
    kind = kind.strip().casefold()
    if kind not in PROPOSABLE_MEMORY_KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(PROPOSABLE_MEMORY_KINDS)}")
    subject = subject.strip()
    content = content.strip()
    service = service.strip()
    reason = reason.strip()
    idempotency_key = hashlib.sha256(
        f"{kind}:{subject.casefold()}:{content.casefold()}".encode()
    ).hexdigest()
    existing = row(
        """SELECT * FROM memory_proposals
        WHERE workspace_id=? AND project_id=? AND idempotency_key=?
        AND status IN ('pending_approval','approved')""",
        (principal["active_workspace_id"], project_id, idempotency_key),
    )
    if existing:
        return _public_memory_proposal(existing)
    proposal_id, now = new_id("mprop"), utcnow()
    with connect() as conn:
        conn.execute(
            """INSERT INTO memory_proposals
            (id,workspace_id,user_id,project_id,kind,subject,content,service,reason,
             origin,idempotency_key,status,requested_at)
            VALUES (?,?,?,?,?,?,?,?,?,'webmcp',?,'pending_approval',?)""",
            (
                proposal_id,
                principal["active_workspace_id"],
                principal["id"],
                project_id,
                kind,
                subject,
                content,
                service,
                reason,
                idempotency_key,
                now,
            ),
        )
    audit.record(
        "memory.proposal.proposed",
        f"Proposed {kind} memory: {subject}",
        project_id,
        actor=str(principal["id"]),
        payload={"memory_proposal_id": proposal_id, "kind": kind, "reason": reason},
    )
    return _public_memory_proposal(
        row("SELECT * FROM memory_proposals WHERE id=?", (proposal_id,)) or {}
    )


agent_sessions = AgentSessionStore()
agent_runner = WebMCPAgentRunner()


def _run_agent_session(run_id: str, principal: dict, request: AgentSessionRequest) -> None:
    def on_step(step: dict) -> None:
        agent_sessions.append_step(run_id, step)

    try:
        result = agent_runner.run(
            principal=principal,
            question=request.question,
            project_id=request.project_id,
            model=request.model or None,
            on_step=on_step,
        )
        agent_sessions.update(
            run_id,
            status="complete",
            answer=result["answer"],
            memory_ids=result["memory_ids"],
            proposal=result["proposal"],
            thoughts=result["thoughts"],
            mode=result.get("mode", "model"),
        )
    except Exception as exc:
        logger.exception("Agent session failed")
        agent_sessions.update(run_id, status="error", error=str(exc))


@router.post("/webmcp/agent-sessions")
def start_agent_session(
    request: AgentSessionRequest, authorization: str | None = Header(default=None)
):
    """Run a live agent over the page's WebMCP tool surface.

    Returns immediately; the console polls the session while tools land one by
    one. Same authorization as every other endpoint, and the only write it can
    make is a proposal that waits for human approval.
    """
    principal = _authenticate(authorization)
    model = configured_model(request.model or None)
    run_id = agent_sessions.create(
        request.question, model.id if model else "", principal.get("active_workspace_id", "")
    )
    audit.record(
        "webmcp.agent_session.started",
        f"Agent session: {request.question[:80]}",
        request.project_id or None,
        actor=str(principal["id"]),
        payload={"agent_session_id": run_id},
    )
    thread = Thread(
        target=_run_agent_session,
        args=(run_id, principal, request),
        daemon=True,
    )
    thread.start()
    return agent_sessions.get(run_id, principal.get("active_workspace_id", ""))


@router.get("/webmcp/agent-sessions/{run_id}")
def agent_session(run_id: str, authorization: str | None = Header(default=None)):
    principal = _authenticate(authorization)
    record = agent_sessions.get(run_id, principal.get("active_workspace_id", ""))
    if not record:
        raise HTTPException(404, "Agent session not found")
    return record


@router.get("/memory/proposals")
def memory_proposals(status: str = "", authorization: str | None = Header(default=None)):
    principal = _authorize_workspace(authorization)
    records = rows(
        """SELECT * FROM memory_proposals WHERE workspace_id=?
        AND (?='' OR status=?) ORDER BY requested_at DESC""",
        (principal["active_workspace_id"], status, status),
    )
    visible_project_ids = _visible_project_ids(principal)
    if visible_project_ids is not None:
        records = [record for record in records if record["project_id"] in visible_project_ids]
    # Same inbox rule as refresh requests: members follow their own proposals,
    # the workspace-wide queue belongs to the people who make decisions.
    if principal["role"] not in {"owner", "admin"}:
        records = [record for record in records if record["user_id"] == principal["id"]]
    return [_public_memory_proposal(record) for record in records]


@router.post("/memory/proposals/{proposal_id}/resolve")
def resolve_memory_proposal(
    proposal_id: str,
    request: MemoryProposalResolutionRequest,
    authorization: str | None = Header(default=None),
):
    """Record a person's approve/deny decision and, on approval, persist memory.

    Approval is the only path from proposal to company memory; it reuses the
    standard memory creation path so conflicts, updates, and graph links behave
    exactly like any other verified memory.
    """
    principal = _authorize_workspace(authorization, admin=True)
    record = row("SELECT * FROM memory_proposals WHERE id=?", (proposal_id,))
    if not record or record["workspace_id"] != principal["active_workspace_id"]:
        raise HTTPException(404, "Memory proposal not found")
    _authorize_project(record["project_id"], authorization, write=True)
    if record["status"] != "pending_approval":
        raise HTTPException(400, "Memory proposal is not pending approval")
    now = utcnow()
    status = "approved" if request.approved else "denied"
    memory_id = ""
    if request.approved:
        created = company_memory.create(
            record["project_id"],
            record["kind"],
            record["subject"],
            record["content"],
            [],
            0.95,
            {
                "company": "",
                "project": record["project_id"],
                "repo": "",
                "service": record["service"] or "",
                "person": "",
            },
        )
        memory_id = str(created.get("id") or "")
    with connect() as conn:
        conn.execute(
            """UPDATE memory_proposals SET status=?,resolved_at=?,resolved_by=?,memory_id=?
            WHERE id=?""",
            (status, now, principal["id"], memory_id, proposal_id),
        )
    audit.record(
        f"memory.proposal.{status}",
        f"Memory proposal {status}: {record['subject']}",
        record["project_id"],
        actor=str(principal["id"]),
        payload={
            "memory_proposal_id": proposal_id,
            "memory_id": memory_id,
            "origin": record["origin"],
        },
    )
    return _public_memory_proposal(
        row("SELECT * FROM memory_proposals WHERE id=?", (proposal_id,)) or {}
    )


@router.get("/connectors/custom/registrations")
def custom_connector_registrations(authorization: str | None = Header(default=None)):
    principal = _authorize_workspace(authorization)
    return connector_runtime.list_custom(principal)


@router.post("/connectors/custom/registrations")
def register_custom_connector(
    request: CustomConnectorCreateRequest,
    authorization: str | None = Header(default=None),
):
    principal = _authorize_workspace(authorization, admin=True)
    if not settings.connector_custom_mcp_enabled:
        raise HTTPException(403, "Custom MCP registration is disabled")
    try:
        return connector_runtime.register_custom(
            principal,
            name=request.name,
            server_url=request.server_url,
            version=request.version,
            oauth=request.oauth,
            manifest_payload=request.manifest,
            signing_key_id=request.signing_key_id,
        )
    except Exception as exc:
        fail(exc)


@router.post("/connectors/custom/rest-sources")
def register_rest_source(
    request: RestSourceCreateRequest,
    authorization: str | None = Header(default=None),
):
    """Register any JSON-over-HTTPS platform as a scheduled sync source."""
    principal = _authorize_workspace(authorization, admin=True)
    if not settings.connector_rest_sources_enabled:
        raise HTTPException(403, "Custom API source registration is disabled")
    if not request.base_url.lower().startswith("https://"):
        raise HTTPException(422, "API source URLs must use HTTPS")
    try:
        return connector_runtime.register_rest_source(
            principal,
            name=request.name,
            base_url=request.base_url,
            config=request.config,
            headers=request.headers,
            version=request.version,
        )
    except Exception as exc:
        fail(exc)


@router.delete("/connectors/custom/registrations/{provider}")
def revoke_custom_connector_registration(
    provider: str,
    authorization: str | None = Header(default=None),
):
    principal = _authorize_workspace(authorization, admin=True)
    try:
        return connector_runtime.revoke_custom(provider, principal)
    except Exception as exc:
        fail(exc)


@router.post("/mcp/oauth/clients")
def create_mcp_oauth_client(
    request: MCPOAuthClientCreateRequest,
    authorization: str | None = Header(default=None),
):
    principal = _authorize_workspace(authorization, admin=True)
    try:
        result = register_mcp_client(
            request.name,
            request.redirect_uris,
            request.scopes,
            created_by=principal["id"],
        )
    except ValueError as exc:
        fail(exc)
    audit.record(
        "mcp.oauth_client.created",
        f"Registered MCP OAuth client {request.name}",
        actor=principal["id"],
        payload={
            "workspace_id": principal["active_workspace_id"],
            "client_id": result["client_id"],
            "scopes": request.scopes,
        },
    )
    return result


@router.get("/auth/github/start")
def github_auth_start(request: Request):
    try:
        flow = OAuthStateStore().create(
            "github", intent="login", use_pkce=settings.github_oauth_use_pkce
        )
        # Sign-in asks for repository access in the same consent screen, so a
        # person who signs in with GitHub never meets a second "Connect GitHub"
        # step before they can pick a repository.
        return RedirectResponse(
            GitHubConnector().oauth_url(
                flow,
                scopes=" ".join(GitHubConnector.manifest.oauth.scopes),
                redirect_uri=oauth_redirect_uri(
                    request, settings.github_redirect_uri, "/api/auth/github/callback"
                ),
            )
        )
    except Exception as exc:
        fail(exc)


@router.get("/auth/google/start")
def google_auth_start(request: Request):
    try:
        flow = OAuthStateStore().create("google", intent="login", use_pkce=True)
        return RedirectResponse(
            google_oauth_url(
                flow,
                redirect_uri=oauth_redirect_uri(
                    request, settings.google_redirect_uri, "/api/auth/google/callback"
                ),
            )
        )
    except Exception as exc:
        fail(exc)


@router.get("/auth/google/callback")
def google_auth_callback(code: str, state: str, request: Request):
    try:
        flow = OAuthStateStore().consume("google", state)
        identity = complete_google_oauth(
            code,
            flow,
            redirect_uri=oauth_redirect_uri(
                request, settings.google_redirect_uri, "/api/auth/google/callback"
            ),
        )
        session = create_oauth_session(
            "google",
            identity["external_id"],
            identity["email"],
            identity["display_name"],
            f"{identity['email'].split('@')[-1]}'s workspace",
        )
        _seed_public_demo_for(session)
        response = RedirectResponse(frontend_redirect(request, "/workspace"))
        _set_session_cookie(response, session["token"])
        return response
    except Exception as exc:
        query = urlencode({"error": str(exc)})
        return RedirectResponse(frontend_redirect(request, f"/login?{query}"))


@router.get("/connectors/{provider}/auth/start")
def connector_auth_start(
    provider: str,
    request: Request,
    scopes: str = "",
    authorization: str | None = Header(default=None),
):
    principal = _authorize_workspace(authorization)
    try:
        redirect_uri = (
            oauth_redirect_uri(request, settings.github_redirect_uri, "/api/auth/github/callback")
            if provider == "github"
            else ""
        )
        flow = OAuthStateStore().create(
            provider,
            intent="connect",
            workspace_id=principal["active_workspace_id"],
            user_id=principal["id"],
            use_pkce=settings.github_oauth_use_pkce if provider == "github" else True,
            redirect_uri=redirect_uri,
        )
        requested = [item for item in scopes.replace(",", " ").split() if item]
        return RedirectResponse(
            connector_runtime.authorize(provider, principal, flow, requested or None)
        )
    except Exception as exc:
        fail(exc)


@router.get("/auth/github/callback")
def github_auth_callback(
    code: str, state: str, request: Request, background_tasks: BackgroundTasks = None
):
    flow = None
    try:
        flow = OAuthStateStore().consume("github", state)
        redirect_uri = oauth_redirect_uri(
            request, settings.github_redirect_uri, "/api/auth/github/callback"
        )
        if flow["intent"] == "login":
            identity = GitHubConnector().complete_oauth(code, flow, redirect_uri=redirect_uri)
            session = create_oauth_session(
                "github",
                identity["external_id"],
                identity["email"],
                identity["display_name"],
                f"{identity['login']}'s workspace",
            )
            _seed_public_demo_for(session)
            _connect_github_from_sign_in(session, identity)
            response = RedirectResponse(frontend_redirect(request, "/workspace"))
            _set_session_cookie(response, session["token"])
            return response
        # The connector runtime performs the OAuth exchange and persists the
        # resulting token. A GitHub authorization code is single-use, so do
        # not exchange it here before passing it to the runtime.
        connector_runtime.complete_authorization("github", flow, code)
        return RedirectResponse(frontend_redirect(request, "/connectors?connected=github"))
    except Exception as exc:
        query = urlencode({"error": str(exc)})
        destination = "connectors" if flow and flow.get("intent") == "connect" else "login"
        return RedirectResponse(frontend_redirect(request, f"/{destination}?{query}"))


def _connect_github_from_sign_in(session: dict, identity: dict) -> None:
    """Keep the sign-in grant as this person's GitHub connection.

    Sign-in already asked GitHub for repository access, so throwing the token
    away and sending them through a second authorization to reach the same
    repositories is pure friction. The grant is stored exactly as the connector
    flow stores it: per workspace and per user, encrypted by the vault.

    A token that came back without repository scope (the person narrowed it,
    or an older OAuth App) is not stored — the connector page would then show
    GitHub as connected while every repository call failed. Storage failures
    never block sign-in; the connector page remains the way to retry.
    """
    granted = set(str(identity.get("scope") or "").replace(",", " ").split())
    if "repo" not in granted:
        return
    principal = session.get("user") or {}
    workspace_id = str(principal.get("active_workspace_id") or "")
    user_id = str(principal.get("id") or "")
    if not workspace_id or not user_id:
        return
    try:
        connector_runtime.vault(principal).save(
            "github",
            str(identity["external_id"]),
            str(identity.get("login") or identity.get("display_name") or identity["external_id"]),
            str(identity["token"]),
            {
                "login": identity.get("login", ""),
                "avatar_url": identity.get("avatar_url", ""),
                "scope": identity.get("scope", ""),
            },
        )
        audit.record(
            "connector.connected",
            "Connected GitHub during sign-in",
            actor=user_id,
            payload={"provider": "github", "workspace_id": workspace_id, "via": "sign_in"},
        )
    except Exception:  # noqa: BLE001 - sign-in must succeed even if this does not
        logger.exception("Storing the GitHub sign-in grant as a connection failed")


@router.get("/connectors/{provider}/auth/callback")
def connector_auth_callback(provider: str, code: str, state: str):
    try:
        flow = OAuthStateStore().consume(provider, state)
        connector_runtime.complete_authorization(provider, flow, code)
        return RedirectResponse(f"{settings.frontend_url}/connectors?connected={provider}")
    except Exception as exc:
        query = urlencode({"error": str(exc)})
        return RedirectResponse(f"{settings.frontend_url}/connectors?{query}")


@router.get("/auth/slack/callback")
@router.get("/connectors/slack/auth/callback")
def slack_auth_callback(code: str, state: str):
    try:
        flow = OAuthStateStore().consume("slack", state)
        connector_runtime.complete_authorization("slack", flow, code)
        return RedirectResponse(f"{settings.frontend_url}/connectors?connected=slack")
    except Exception as exc:
        query = urlencode({"error": str(exc)})
        return RedirectResponse(f"{settings.frontend_url}/connectors?{query}")


@router.delete("/connectors/{provider}")
def disconnect_connector(provider: str, authorization: str | None = Header(default=None)):
    principal = _authorize_workspace(authorization)
    try:
        connector = connector_runtime.connector(provider, principal)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    account = connector_runtime.vault(principal).account(provider)
    if not account:
        return {"disconnected": False}
    connector.revoke(account)
    audit.record(
        "connector.disconnected",
        f"Disconnected {provider}",
        actor=principal["id"],
        payload={
            "provider": provider,
            "workspace_id": principal["active_workspace_id"],
        },
    )
    return {"disconnected": True}


def _create_job(
    source: str,
    source_ref: str,
    project_id: str | None = None,
    workspace_id: str | None = None,
) -> str:
    job_id = new_id("job")
    now = utcnow()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO ingestion_jobs
            (id, project_id, workspace_id, source, source_ref, status, progress,
             warnings_json, created_at, updated_at)
            VALUES (?,?,?,?,?,'running',5,'[]',?,?)
            """,
            (job_id, project_id, workspace_id, source, source_ref, now, now),
        )
    audit.record(
        "ingestion.job_started",
        f"Started {source} ingestion",
        project_id,
        payload={"job_id": job_id, "source_ref": source_ref},
    )
    return job_id


def _finish_job(job_id: str, status: str, result: dict) -> None:
    now = utcnow()
    project_id = result.get("project_id")
    with connect() as conn:
        conn.execute(
            """
            UPDATE ingestion_jobs SET
              project_id=COALESCE(?, project_id), status=?, progress=100,
              files_scanned=?, issues_scanned=?, pull_requests_scanned=?,
              knowledge_items_created=?, knowledge_chunks_created=?,
              graph_nodes_created=?, graph_edges_created=?, warnings_json=?,
              updated_at=?, completed_at=?
            WHERE id=?
            """,
            (
                project_id,
                status,
                int(result.get("files_scanned", 0) or 0),
                int(result.get("issues_scanned", 0) or 0),
                int(result.get("pull_requests_scanned", 0) or 0),
                int(result.get("knowledge_items_created", 1 if result.get("item_id") else 0) or 0),
                int(result.get("knowledge_chunks_created", result.get("chunks_created", 0)) or 0),
                int(result.get("graph_nodes_created", 0) or 0),
                int(result.get("graph_edges_created", 0) or 0),
                json.dumps(result.get("warnings", [])),
                now,
                now,
                job_id,
            ),
        )
        job = conn.execute(
            "SELECT workspace_id FROM ingestion_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if job and job["workspace_id"] and project_id:
            conn.execute(
                "INSERT OR IGNORE INTO workspace_projects VALUES (?,?)",
                (job["workspace_id"], project_id),
            )
    audit.record(
        "ingestion.job_finished",
        f"Finished ingestion job {job_id}",
        project_id,
        payload={"job_id": job_id, "status": status},
    )


def _fail_job(job_id: str, exc: Exception) -> None:
    """Record why a job failed without ever replacing the failure itself.

    This runs inside an ``except`` block, so anything it raises would become the
    error the caller sees and the real cause would be lost. Recording the
    failure is best-effort; reporting it accurately is not.
    """
    now = utcnow()
    try:
        with connect() as conn:
            conn.execute(
                """
                UPDATE ingestion_jobs SET status='failed', progress=100, error=?,
                  updated_at=?, completed_at=? WHERE id=?
                """,
                (str(exc), now, now, job_id),
            )
        audit.record(
            "ingestion.job_failed",
            f"Ingestion job {job_id} failed",
            payload={"job_id": job_id, "error": str(exc)},
        )
    except Exception:
        logger.exception("Could not record failure for ingestion job %s", job_id)
