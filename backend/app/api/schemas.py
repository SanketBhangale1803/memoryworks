from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class GitHubIngestRequest(BaseModel):
    repo_url_or_path: str
    project_name: str
    workspace_id: str | None = None
    team_ids: list[str] = Field(default_factory=list, max_length=50)
    # Return the job at once and ingest after the response (poll the job).
    background: bool = False


class GitHubBulkIngestRequest(BaseModel):
    owner: str | None = Field(default=None, max_length=200)
    include_archived: bool = False
    max_repositories: int = Field(500, ge=1, le=500)
    workspace_id: str | None = None


class RepositoryRefreshProposalRequest(BaseModel):
    project_id: str = Field(min_length=4, max_length=128)
    reason: str = Field(min_length=5, max_length=800)


class RepositoryRefreshResolutionRequest(BaseModel):
    approved: bool


class MemoryProposalRequest(BaseModel):
    """A proposed addition to company memory that a person must approve.

    Browser agents (or the API) propose; only an explicit human decision
    persists anything into the durable memory graph.
    """

    project_id: str = Field(min_length=4, max_length=128)
    kind: str = Field(default="fact", max_length=32)
    subject: str = Field(min_length=3, max_length=300)
    content: str = Field(min_length=3, max_length=4000)
    service: str = Field(default="", max_length=120)
    reason: str = Field(default="", max_length=800)


class MemoryProposalResolutionRequest(BaseModel):
    approved: bool


class AgentSessionRequest(BaseModel):
    """One live agent session over the page's WebMCP tool surface."""

    question: str = Field(min_length=5, max_length=1000)
    project_id: str = Field(default="", max_length=128)
    model: str = Field(default="", max_length=60)


class UploadRequest(BaseModel):
    project_id: str = Field(min_length=4, max_length=128)
    source_type: Literal[
        "incident",
        "slack_export",
        "gmail_export",
        "clickup_ticket",
        "github_issue_export",
        "log",
        "doc",
        "support_ticket",
        "other",
        "slack",
        "report",
    ]
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=2_000_000)
    source_url: str = Field(default="", max_length=2_000)
    source_id: str | None = Field(default=None, max_length=500)
    team_ids: list[str] = Field(default_factory=list, max_length=50)
    artifact_type: str = Field(default="", max_length=100)
    artifact_name: str = Field(default="", max_length=500)


class SlackIngestRequest(BaseModel):
    project_id: str
    channel_id: str
    limit: int = Field(200, ge=1, le=1000)
    team_ids: list[str] = Field(default_factory=list, max_length=50)


class ChatImportRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    project_id: str = Field(default="", max_length=128)


class WebIngestRequest(BaseModel):
    project_id: str = Field(min_length=4, max_length=128)
    url: str = Field(min_length=4, max_length=2000)
    team_ids: list[str] = Field(default_factory=list, max_length=50)


class RestSourceCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    base_url: str = Field(min_length=8, max_length=2000)
    config: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    version: str = Field("1.0.0", max_length=32)


class TokenRequest(BaseModel):
    token: str
    display_name: str = "Local connection"


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    # Long enough to carry a real question, short enough that twenty of them
    # cannot be used to smuggle a payload into the prompt.
    content: str = Field("", max_length=4_000)


class AskRequest(BaseModel):
    project_id: str = Field(min_length=4, max_length=128)
    query: str = Field(min_length=3, max_length=4_000)
    token_budget: int = Field(6000, ge=500, le=32000)
    model: Literal["glm", "gpt", "claude", "gemini", "grok", "kimi"] | None = None
    # Which surface asked. Recorded on the context event so the outcome corpus can
    # separate what works for a person in the web chat from what works for an
    # agent calling through MCP.
    surface: str = Field("web", max_length=32)
    # "workspace" searches every project the caller can see — the default for the
    # chat, where nobody is thinking in repositories. "project" restores the hard
    # single-repository boundary. "auto" leaves the decision to the router.
    scope: Literal["auto", "workspace", "project"] = "auto"
    # Prior turns, oldest first, so a follow-up like "why is it failing" can be
    # bound to the subject the asker already named. Capped because only the last
    # few exchanges carry the live subject, and an unbounded history is an
    # unbounded prompt.
    history: list[HistoryTurn] = Field(default_factory=list, max_length=20)


class ExecuteRequest(BaseModel):
    project_id: str = Field(min_length=4, max_length=128)
    # The handoff envelope from an /api/ask response, passed back verbatim.
    handoff: dict[str, Any]
    context_event_id: str = Field("", max_length=128)
    executor: Literal["cursor", "claude"] | None = None
    # Publishing to a shared remote is opt-in per request *and* per deployment.
    push: bool = False


class ActionRecordRequest(BaseModel):
    context_event_id: str = Field(min_length=4, max_length=128)
    action_type: str = Field(min_length=2, max_length=64)
    target: str = Field("", max_length=64)
    surface: str = Field("", max_length=32)
    detail: dict[str, Any] = Field(default_factory=dict)


class OutcomeRecordRequest(BaseModel):
    context_event_id: str = Field(min_length=4, max_length=128)
    outcome: Literal["succeeded", "failed", "partial", "abandoned", "unknown"]
    action_event_id: str = Field("", max_length=128)
    signal: str = Field("human", max_length=32)
    reason: str = Field("", max_length=2_000)
    detail: dict[str, Any] = Field(default_factory=dict)


class BriefingRequest(BaseModel):
    """What an agent is about to do, before it does it."""

    task: str = Field(min_length=3, max_length=2_000)
    service: str = Field("", max_length=120)
    project_id: str = Field("", max_length=128)
    # Where the agent is working when it asks. Recorded on the context event so
    # the outcome corpus can tell a briefing served to a GitHub pull request from
    # one served inside the MemoryWorks workspace.
    surface: str = Field("webmcp", max_length=64)


class BriefingOutcomeRequest(BaseModel):
    """What the agent did with a briefing, and whether it worked."""

    briefing_id: str = Field(min_length=4, max_length=128)
    action: str = Field(min_length=2, max_length=64)
    outcome: Literal["succeeded", "failed", "partial", "abandoned", "unknown"] = "unknown"
    target: str = Field("", max_length=200)
    surface: str = Field("webmcp", max_length=64)
    reason: str = Field("", max_length=2_000)
    detail: dict[str, Any] = Field(default_factory=dict)


class MemoryWorkCreateRequest(BaseModel):
    project_id: str = Field(min_length=4, max_length=128)
    objective: str = Field(min_length=3, max_length=4_000)


class MemoryWorkResolveRequest(BaseModel):
    approved: bool
    channel_id: str = Field(default="", max_length=100)
    message: str = Field(default="", max_length=35_000)


class MemoryWorkCompleteRequest(BaseModel):
    output: dict[str, Any] = Field(default_factory=dict)


class MemoryRepairRequest(BaseModel):
    repository_only: bool = False
    clear_work_history: bool = True


class SemanticChangeInterpretRequest(BaseModel):
    project_id: str = Field(min_length=4, max_length=128)
    diff: str = Field(min_length=1, max_length=500_000)
    repository: str = Field(default="", max_length=500)
    commit_sha: str = Field(default="", max_length=100)
    source_url: str = Field(default="", max_length=2_000)
    delivery_id: str = Field(default="", max_length=200)
    context: dict[str, Any] = Field(default_factory=dict)


class TeamCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_team_id: str | None = None


class TeamMemberRequest(BaseModel):
    user_id: str
    role: Literal["lead", "member", "viewer"] = "member"


class ProjectTeamRequest(BaseModel):
    team_id: str
    access_level: Literal["read", "write", "owner"] = "read"


class ArtifactSaveRequest(BaseModel):
    project_id: str
    name: str = Field(min_length=1, max_length=500)
    artifact_type: Literal["report", "brief", "profile", "document"] = "report"
    content: str = Field(min_length=1, max_length=2_000_000)
    source_ids: list[str] = Field(default_factory=list, max_length=500)
    memory_ids: list[str] = Field(default_factory=list, max_length=500)
    context_envelope_id: str = ""


class SkillCompileRequest(BaseModel):
    project_id: str
    name: str = Field(min_length=1, max_length=200)
    team_id: str = ""


class ExtractRequest(BaseModel):
    project_id: str
    query: str
    scope: Literal["repo", "service", "incident", "workflow", "all"] = "all"
    service_name: str | None = None


class ProposeRequest(BaseModel):
    project_id: str
    runbook_id: str
    action_id: str
    params: dict[str, Any] = Field(default_factory=dict)


class ResolveRequest(BaseModel):
    action_id: str
    resolved_by: str = "demo-user"


class DevLoginRequest(BaseModel):
    email: str = "demo@runbook.local"
    display_name: str = "Demo User"


class DemoLoginRequest(BaseModel):
    identity: Literal["google", "github", "guest", "email"] = "guest"
    display_name: str = Field(default="Demo User", min_length=1, max_length=80)


class EmailCodeRequest(BaseModel):
    email: str = Field(
        min_length=5,
        max_length=320,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )


class EmailCodeVerifyRequest(BaseModel):
    email: str = Field(
        min_length=5,
        max_length=320,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class WorkspaceCreateRequest(BaseModel):
    name: str


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    team_ids: list[str] = Field(default_factory=list, max_length=50)


class InviteMemberRequest(BaseModel):
    email: str
    role: Literal["owner", "admin", "member", "viewer"] = "member"


class SimulateRequest(BaseModel):
    project_id: str
    runbook_id: str | None = None
    scenario: str = ""
    environment: str = "production"
    params: dict[str, Any] = Field(default_factory=dict)


class CorrelateRequest(BaseModel):
    project_id: str
    query: str = ""
    service_name: str | None = None


class MemoryResolveRequest(BaseModel):
    resolved_by: str = "demo-user"


class ApiKeyCreateRequest(BaseModel):
    name: str
    workspace_id: str = ""


class ImporterRunRequest(BaseModel):
    project_id: str
    resource: Literal[
        "incidents", "services", "escalation_policies", "postmortems", "status_updates"
    ] = "incidents"
    limit: int = Field(50, ge=1, le=200)


class ChangeImpactAnalyzeRequest(BaseModel):
    type: Literal["github_pull_request", "commit", "repository_reingestion", "manual"] = "manual"
    ref: str = ""
    changed_files: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    environment_variables: list[str] = Field(default_factory=list)
    config_keys: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    workflows: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class AssertionDecisionRequest(BaseModel):
    actor: str = "demo-user"
    reason: str = ""
    superseded_by: str = ""


class BulkAssertionReviewRequest(BaseModel):
    assertion_ids: list[str] = Field(min_length=1, max_length=200)
    action: Literal["verify", "dismiss", "mark_stale", "supersede"] = "verify"
    actor: str = "demo-user"
    reason: str = "Bulk review approved against current evidence."
    owner: str = ""


class ConnectorToolInvokeRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(default="", max_length=200)


class ConnectorToolResolveRequest(BaseModel):
    approved: bool


class ConnectorSyncRequest(BaseModel):
    resource_id: str = Field(min_length=1, max_length=500)
    project_id: str = Field(default="", max_length=128)
    cursor: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(default="", max_length=200)


class CustomConnectorCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    server_url: str = Field(min_length=8, max_length=2_000)
    version: str = Field(default="1.0.0", min_length=1, max_length=100)
    oauth: dict[str, Any] = Field(default_factory=dict)
    manifest: dict[str, Any] = Field(default_factory=dict)
    signing_key_id: str = Field(default="", max_length=200)


class MCPOAuthClientCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    redirect_uris: list[str] = Field(min_length=1, max_length=20)
    scopes: list[Literal["read", "write"]] = Field(default_factory=lambda: ["read"])
