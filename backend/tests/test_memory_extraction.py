from app.audit import AuditService
from app.core.database import rows
from app.hcag_adapter import HCAGAdapter
from app.ingestion import IngestionService
from app.ingestion.extractors import extract_services
from app.ingestion.maintenance import reset_project_derived_memory
from app.memory.brain import CompanyBrainService
from app.memory.company import CompanyMemoryService


def test_ingestion_creates_source_backed_atomic_memory(graph):
    ingestion = IngestionService(graph, HCAGAdapter(graph), AuditService())
    project_id = ingestion.create_project("Memory extraction")
    result = ingestion.ingest_item(
        project_id,
        "slack",
        "storage decision",
        "The team decided Instagram media should be stored in MinIO.",
        source_id="slack:42",
    )
    units = CompanyMemoryService(graph).list(project_id)
    assert result["memory_units_created"] == 1
    assert units[0]["type"] == "decision"
    assert units[0]["source_ids"] == ["slack:42"]
    assert any(edge["relationship"] == "MEMORY_DERIVED_FROM_SOURCE" for edge in graph.edges)


def test_repository_code_does_not_promote_ui_and_error_fragments_to_policy(graph):
    ingestion = IngestionService(graph, HCAGAdapter(graph), AuditService())
    project_id = ingestion.create_project("Strict extraction")
    ingestion.ingest_item(
        project_id,
        "repo_file",
        "src/main.jsx",
        """
        setError('Employee ID was not found in the local directory fixture.');
        const steps = [{ id: 'policy', label: 'Policy', detail: 'Caps and risk' }];
        <p className="notice warn">SAP connection is not ready.</p>
        """,
        source_id=f"file:{project_id}:src/main.jsx",
        metadata={"path": "src/main.jsx"},
    )

    assert CompanyMemoryService(graph).list(project_id) == []


def test_code_promotes_only_explicit_service_declarations(graph):
    ingestion = IngestionService(graph, HCAGAdapter(graph), AuditService())
    project_id = ingestion.create_project("Explicit services")
    ingestion.ingest_item(
        project_id,
        "repo_file",
        "backend/service.py",
        """
        SERVICE_NAME = "purchase-gateway"
        def append_sap_client():
            return "sap-client"
        """,
        source_id=f"file:{project_id}:backend/service.py",
        metadata={"path": "backend/service.py"},
    )

    units = CompanyMemoryService(graph).list(project_id)
    assert [item["subject"] for item in units] == ["purchase-gateway"]


def test_repository_structure_extracts_real_services_and_endpoints(graph):
    ingestion = IngestionService(graph, HCAGAdapter(graph), AuditService())
    project_id = ingestion.create_project("SAP services")
    result = ingestion.ingest_item(
        project_id,
        "repo_file",
        "README.md",
        """
        | Service | Module | Responsibility |
        | --- | --- | --- |
        | `sap-requisition-policy-engine` | `backend/policy.py` | Risk and approvals |
        - `POST /api/requisition/validate` — validate a request before allocation
        """,
        source_id=f"file:{project_id}:README.md",
        metadata={"path": "README.md"},
    )

    units = CompanyMemoryService(graph).list(project_id)
    assert result["memory_units_created"] == 2
    assert any("sap-requisition-policy-engine" in item["content"] for item in units)
    assert any("POST /api/requisition/validate" in item["content"] for item in units)
    assert "sap-requisition-policy-engine" in extract_services(
        "SERVICE_NAME = 'sap-requisition-policy-engine'", "backend/policy.py"
    )


def test_repository_source_cannot_cross_project_boundaries(graph):
    ingestion = IngestionService(graph, HCAGAdapter(graph), AuditService())
    project_id = ingestion.create_project(
        "Allowed repository", "https://github.com/acme/allowed.git"
    )
    try:
        ingestion.ingest_item(
            project_id,
            "repo_file",
            "README.md",
            "This repository uses FastAPI.",
            "https://github.com/acme/other/blob/main/README.md",
            source_id=f"file:{project_id}:README.md",
            metadata={"path": "README.md", "repository": "acme/other"},
        )
    except ValueError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("A foreign repository source was accepted")


def test_memory_repair_removes_demo_sources_and_derived_nodes(graph):
    ingestion = IngestionService(graph, HCAGAdapter(graph), AuditService())
    project_id = ingestion.create_project("Clean project")
    ingestion.ingest_item(
        project_id,
        "repo_file",
        "README.md",
        "The purchase service is built with FastAPI.",
        source_id=f"file:{project_id}:README.md",
        metadata={"path": "README.md"},
    )
    ingestion.ingest_item(
        project_id,
        "slack",
        "Demo storage message",
        "The team decided instagram_service must use MinIO.",
        source_id="slack:demo-instagram",
    )
    graph.upsert_service(
        {
            "id": f"{project_id}:instagram_service",
            "project_id": project_id,
            "name": "instagram_service",
        }
    )
    graph.upsert_node(
        "SlackMessage",
        {"id": "slack:demo", "project_id": project_id, "content": "demo"},
    )
    graph.upsert_node(
        "Runbook",
        {"id": "runbook:demo", "project_id": project_id, "title": "demo"},
    )

    result = reset_project_derived_memory(graph, project_id, repository_only=True)

    assert result["removed_sources"] == 1
    assert {
        item["source_type"]
        for item in rows(
            "SELECT source_type FROM knowledge_items WHERE project_id=?",
            (project_id,),
        )
    } == {"repo_file"}
    assert CompanyMemoryService(graph).list(project_id) == []
    assert graph.list_nodes(project_id, "Service", 100) == []
    assert graph.list_nodes(project_id, "SlackMessage", 100) == []
    assert graph.list_nodes(project_id, "Runbook", 100) == []


def test_existing_source_revision_is_restored_to_graph_after_repair(graph):
    brain = CompanyBrainService(graph)
    project_id = IngestionService(graph, HCAGAdapter(graph), AuditService()).create_project(
        "Revision repair"
    )
    source_id = f"file:{project_id}:README.md"
    payload, created = brain.record_source_revision(
        project_id,
        source_id,
        "repo_file",
        "README.md",
        "A source-backed repository description.",
        {"path": "README.md"},
    )
    assert created is True

    graph.delete_project_nodes(project_id, ["Source", "SourceRevision"])
    restored, created = brain.record_source_revision(
        project_id,
        source_id,
        "repo_file",
        "README.md",
        "A source-backed repository description.",
        {"path": "README.md"},
    )

    assert created is False
    assert restored["id"] == payload["id"]
    assert graph.list_nodes(project_id, "Source", 10)
    assert graph.list_nodes(project_id, "SourceRevision", 10)


def test_incident_reports_are_promoted_to_memory(graph):
    ingestion = IngestionService(graph, HCAGAdapter(graph), AuditService())
    project_id = ingestion.create_project("Incident extraction")
    ingestion.ingest_item(
        project_id,
        "incident",
        "Outage: ledger-service pool exhaustion",
        "ledger-service failed for 47 minutes after a backfill opened 400 connections.",
        source_id="incident:ledger-pool",
    )

    kinds = {
        unit["type"]
        for unit in rows("SELECT type FROM memory_units WHERE project_id=?", (project_id,))
    }
    assert "incident" in kinds


def test_inflected_failure_words_still_read_as_incidents():
    from app.memory.extraction import extract_atomic_memories

    for sentence in (
        "fraud-engine timeouts spike whenever payments-service raises its concurrency.",
        "The checkout worker crashed twice during the Friday deploy window.",
        "Card authorizations timed out for twenty minutes during the migration.",
    ):
        kinds = {kind for kind, *_ in extract_atomic_memories("thread", sentence, "slack", {})}
        assert kinds == {"incident"}, sentence
