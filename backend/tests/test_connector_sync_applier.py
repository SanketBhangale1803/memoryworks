import pytest

from app.audit import AuditService
from app.connectors.application import MemoryWorksSyncApplier
from app.connectors.base import SyncOperation, SyncRecord
from app.core.database import row
from app.hcag_adapter import HCAGAdapter
from app.ingestion.service import IngestionService


def _record(record_id: str, resource_type: str, **metadata) -> SyncRecord:
    return SyncRecord(
        id=record_id,
        resource_type=resource_type,
        operation=SyncOperation.UPSERT,
        version="2026-10-05T12:00:00Z",
        title=f"{record_id} title",
        content="The search cluster stays on the current major version until analyzers migrate.",
        source_url=f"https://example.test/{record_id}",
        metadata=metadata,
    )


# Every connector but Slack used to be refused here ("Unsupported memory
# source"), because the applier passed the provider name as the source type.
@pytest.mark.parametrize(
    ("provider", "record", "expected"),
    [
        (
            "google_drive",
            _record(
                "gdrive-file:doc1", "document", mime_type="application/vnd.google-apps.document"
            ),
            "document",
        ),
        (
            "google_drive",
            _record("gdrive-file:pdf1", "document", mime_type="application/pdf"),
            "pdf",
        ),
        (
            "google_drive",
            _record(
                "gdrive-file:sheet1",
                "document",
                mime_type="application/vnd.google-apps.spreadsheet",
            ),
            "spreadsheet",
        ),
        ("notion", _record("notion-page:1", "page"), "document"),
        ("teams", _record("teams-message:1", "channel_message"), "text"),
        ("slack", _record("slack-message:1", "message"), "slack"),
        ("remote_mcp", _record("remote:1", "incident"), "incident"),
        ("rest_pull", _record("rest:1", "record"), "document"),
    ],
)
def test_connector_records_are_ingested_with_a_supported_source_type(
    graph, provider, record, expected
):
    ingestion = IngestionService(graph, HCAGAdapter(graph), AuditService())
    project_id = ingestion.create_project("Platform")
    applier = MemoryWorksSyncApplier(ingestion, graph)

    result = applier(
        record, {"project_id": project_id, "provider": provider, "user_id": "user_test"}
    )

    assert result["status"] == "upserted"
    stored = row(
        "SELECT source_type FROM knowledge_items WHERE project_id=? AND source_id=?",
        (project_id, result["source_id"]),
    )
    assert stored["source_type"] == expected


def _signal_workspace_project(graph, repo="acme/api"):
    from app.core.database import connect, new_id, utcnow

    wid = new_id("wsp")
    pid = new_id("prj")
    now = utcnow()
    with connect() as c:
        c.execute(
            "INSERT INTO workspaces VALUES (?,?,?,?,?)",
            (wid, f"ws-{wid[-6:]}", f"slug-{wid[-6:]}", now, now),
        )
        c.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?,?)", (pid, "sig-proj", repo, "ready", now, now)
        )
        c.execute("INSERT INTO workspace_projects VALUES (?,?)", (wid, pid))
    return wid, pid


def _signal_record(**meta):
    from app.connectors.base import SyncOperation, SyncRecord

    base = {
        "source": "github",
        "kind": "ci_failure",
        "subject": "acme/api",
        "lane": {"producer": "workflow", "target": "default", "workflow_id": "7"},
        "severity": "error",
        "state": "failure",
        "observed_at": "2026-10-08T10:00:00.000000+00:00",
        "generation_at": "2026-10-08T09:55:00.000000+00:00",
        "generation_id": 101,
        "attempt": 1,
        "observation_key": "sig-obs-1",
        "evidence_url": "https://github.com/acme/api/actions/runs/101",
        "details": {"producer": "workflow", "workflow_id": 7, "target": "default"},
        "repository": "acme/api",
        "repo_id": 42,
    }
    base.update(meta)
    return SyncRecord(
        id="github-signal:test:workflow:101:1:failure:2026",
        resource_type="signal",
        operation=SyncOperation.UPSERT,
        version="v1",
        title="CI failure acme/api",
        content="",
        source_url="https://github.com/acme/api/actions/runs/101",
        metadata=base,
    )


def _applier(graph, monkeypatch=None):
    from app.audit import AuditService
    from app.connectors.application import MemoryWorksSyncApplier
    from app.hcag_adapter import HCAGAdapter
    from app.ingestion.service import IngestionService

    ingestion = IngestionService(graph, HCAGAdapter(graph), AuditService())
    applier = MemoryWorksSyncApplier(ingestion, graph)
    if monkeypatch is not None:

        def _boom(*args, **kwargs):
            raise AssertionError("ingestion must not run for signals")

        monkeypatch.setattr(applier.ingestion, "ingest_item", _boom)
    return applier


def test_signal_records_bypass_memory_and_ledger(graph, monkeypatch):
    from app.core.database import rows

    wid, pid = _signal_workspace_project(graph)
    applier = _applier(graph, monkeypatch)
    ctx = {
        "workspace_id": wid,
        "project_id": pid,
        "provider": "github",
        "resource_id": "acme/api",
        "user_id": "u1",
    }
    result = applier(_signal_record(), ctx)
    assert result["status"] == "signal_recorded"
    assert rows("SELECT id FROM knowledge_items WHERE project_id=?", (pid,)) == []
    assert rows("SELECT id FROM memory_units WHERE project_id=?", (pid,)) == []
    assert rows("SELECT id FROM source_revisions WHERE project_id=?", (pid,)) == []
    assert rows("SELECT id FROM action_events WHERE project_id=?", (pid,)) == []
    assert rows("SELECT id FROM outcome_events WHERE project_id=?", (pid,)) == []
    assert len(rows("SELECT id FROM live_issues WHERE workspace_id=?", (wid,))) == 1
    assert len(rows("SELECT id FROM live_signals WHERE workspace_id=?", (wid,))) == 1


def test_signal_record_replay_is_project_scoped(graph, monkeypatch):
    from app.core.database import connect, new_id, rows, utcnow

    wid, pid1 = _signal_workspace_project(graph)
    pid2 = new_id("prj")
    now = utcnow()
    with connect() as c:
        c.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?,?)",
            (pid2, "sig-proj-2", "acme/api", "ready", now, now),
        )
        c.execute("INSERT INTO workspace_projects VALUES (?,?)", (wid, pid2))
    applier = _applier(graph, monkeypatch)
    rec = _signal_record(observation_key="shared-obs")
    r1 = applier(
        rec,
        {"workspace_id": wid, "project_id": pid1, "provider": "github", "resource_id": "acme/api"},
    )
    r2 = applier(
        rec,
        {"workspace_id": wid, "project_id": pid2, "provider": "github", "resource_id": "acme/api"},
    )
    assert r1["status"] == "signal_recorded"
    assert r2["status"] == "signal_recorded"
    assert r1["issue_id"] != r2["issue_id"]
    issues1 = rows("SELECT * FROM live_issues WHERE workspace_id=? AND project_id=?", (wid, pid1))
    issues2 = rows("SELECT * FROM live_issues WHERE workspace_id=? AND project_id=?", (wid, pid2))
    assert len(issues1) == 1 and issues1[0]["occurrences"] == 1
    assert len(issues2) == 1 and issues2[0]["occurrences"] == 1


def test_signal_replay_refreshes_freshness(graph, monkeypatch):
    from app.core.database import row

    wid, pid = _signal_workspace_project(graph)
    applier = _applier(graph, monkeypatch)
    ctx = {"workspace_id": wid, "project_id": pid, "provider": "github", "resource_id": "acme/api"}
    first = applier(_signal_record(observation_key="fresh-obs"), ctx)
    before = row("SELECT * FROM live_signals WHERE id=?", (first["signal_id"],))
    second = applier(_signal_record(observation_key="fresh-obs"), ctx)
    assert second["signal_id"] == first["signal_id"]
    after = row("SELECT * FROM live_signals WHERE id=?", (first["signal_id"],))
    assert after["expires_at"] >= before["expires_at"]
    issue = row("SELECT * FROM live_issues WHERE id=?", (first["issue_id"],))
    assert issue["occurrences"] == 1


def test_signal_record_rejects_forged_project_and_grants(graph, monkeypatch):
    import pytest

    wid, pid = _signal_workspace_project(graph)
    applier = _applier(graph, monkeypatch)
    ctx = {"workspace_id": wid, "project_id": pid, "provider": "github", "resource_id": "acme/api"}
    with pytest.raises(ValueError):
        applier(_signal_record(project_id="prj_forged"), ctx)
    with pytest.raises(ValueError):
        applier(_signal_record(repository="acme/other"), ctx)
    with pytest.raises(ValueError):
        applier(_signal_record(repo_id="not-numeric"), ctx)
    # Forged source_ids override is ignored; verified ids win.
    from app.core.database import row

    res = applier(_signal_record(observation_key="forge-ids", source_ids=["evil-source"]), ctx)
    assert res["status"] == "signal_recorded"
    stored = row("SELECT * FROM live_signals WHERE id=?", (res["signal_id"],))
    import json

    assert json.loads(stored["source_ids_json"]) == [
        "github-repository:42",
        "repository-metadata:acme/api",
    ]


def test_unassigned_signal_is_not_persisted(graph, monkeypatch):
    from app.core.database import rows

    wid, _ = _signal_workspace_project(graph)
    applier = _applier(graph, monkeypatch)
    result = applier(
        _signal_record(observation_key="unassigned-1"),
        {"workspace_id": wid, "project_id": "", "provider": "github", "resource_id": "acme/api"},
    )
    assert result["status"] == "unassigned"
    assert rows("SELECT id FROM live_issues WHERE workspace_id=?", (wid,)) == []
    assert rows("SELECT id FROM live_signals WHERE workspace_id=?", (wid,)) == []
