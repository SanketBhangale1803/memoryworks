import json

from app.audit import AuditService
from app.hcag_adapter import HCAGAdapter
from app.ingestion import IngestionService
from app.memory.company import CompanyMemoryService


def test_new_memory_updates_prior_subject(graph):
    project_id = IngestionService(graph, HCAGAdapter(graph), AuditService()).create_project(
        "Updates"
    )
    service = CompanyMemoryService(graph)
    old = service.create(
        project_id,
        "fact",
        "frontend",
        "The frontend uses Create React App.",
        ["a"],
        0.8,
        {"project": project_id},
    )
    new = service.create(
        project_id,
        "fact",
        "frontend",
        "The frontend uses Vite.",
        ["b"],
        0.9,
        {"project": project_id},
    )
    assert service.get(old["id"])["is_latest"] == 0
    assert service.get(new["id"])["is_latest"] == 1
    assert service.relationships(project_id, "UPDATES")


def test_a_different_kind_never_retires_a_memory_with_the_same_subject(graph):
    project_id = IngestionService(graph, HCAGAdapter(graph), AuditService()).create_project("Kinds")
    service = CompanyMemoryService(graph)
    owner = service.create(
        project_id,
        "ownership",
        "ledger-service",
        "ledger-service is owned by the Ledger team.",
        ["a"],
        0.9,
        {"project": project_id},
    )
    first = service.create(
        project_id,
        "incident",
        "ledger-service",
        "ledger-service failed for 47 minutes after a backfill.",
        ["b"],
        0.86,
        {"project": project_id},
    )
    second = service.create(
        project_id,
        "incident",
        "ledger-service",
        "ledger-service failed again when a replica lagged.",
        ["c"],
        0.86,
        {"project": project_id},
    )

    assert service.get(owner["id"])["is_latest"] == 1
    assert service.get(first["id"])["is_latest"] == 1
    assert service.get(second["id"])["is_latest"] == 1


def _current_sources(project_id):
    from app.core.database import rows

    return {
        source
        for unit in rows(
            "SELECT source_ids_json FROM memory_units WHERE project_id=? AND is_latest=1",
            (project_id,),
        )
        for source in json.loads(unit["source_ids_json"])
    }


def test_a_record_that_says_it_supersedes_another_retires_it_in_either_order(graph):
    for order in ("old-first", "new-first"):
        old = (
            f"doc:adr-9:{order}",
            "ADR-009: Payment sessions in Redis",
            "We decided payments-service should store checkout sessions in Redis.",
        )
        new = (
            f"doc:adr-21:{order}",
            "ADR-021: Move payment sessions to PostgreSQL",
            "Supersedes ADR-009. We decided payments-service must use PostgreSQL for sessions.",
        )
        ingestion = IngestionService(graph, HCAGAdapter(graph), AuditService())
        project_id = ingestion.create_project(f"Supersession {order}")
        for source_id, title, content in (old, new) if order == "old-first" else (new, old):
            ingestion.ingest_item(project_id, "document", title, content, source_id=source_id)

        current = _current_sources(project_id)
        assert new[0] in current, order
        assert old[0] not in current, order
        assert CompanyMemoryService(graph).relationships(project_id, "UPDATES"), order
