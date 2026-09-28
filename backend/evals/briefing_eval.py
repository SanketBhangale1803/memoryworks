"""Score pre-change briefings against cases where the right answer is known.

Each case is an intent an agent might send ("raise worker concurrency to 96")
plus the sources a correct briefing has to surface, the sources it must not
surface, and whether a person should have to approve the change. The corpus is
ingested through the real IngestionService and every briefing is served by the
real /api/briefings route, so the score covers extraction, scoping, retrieval
and the verdict together — the same path an MCP or WebMCP agent takes.

Run from backend/:

    .venv/bin/python evals/briefing_eval.py
    .venv/bin/python evals/briefing_eval.py --json results.json --min-pass 0.8

The run is hermetic: a throwaway SQLite file, an in-memory graph, and no model
provider keys, so two runs over the same cases give the same score.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import settings  # noqa: E402
from app.graph.memory_graph import InMemoryGraphStore, set_graph_store  # noqa: E402

CITED_GROUPS = ("must_read", "constraints", "prior_incidents", "blast_radius", "procedures")


def _hermetic(tmp: Path) -> None:
    """The same local defaults the test suite pins, so a developer .env cannot leak in."""
    settings.sqlite_path = tmp / "eval.db"
    settings.generated_runbooks_dir = tmp / "generated"
    settings.frontend_url = "http://localhost:3000"
    settings.public_base_url = ""
    settings.environment = "development"
    settings.public_demo_mode = False
    settings.auth_dev_mode = True
    for key in (
        "openai_api_key",
        "anthropic_api_key",
        "google_api_key",
        "openrouter_api_key",
        "xai_api_key",
        "kimi_api_key",
    ):
        setattr(settings, key, "")
    settings.org_memory_default_model_provider = "none"
    settings.org_memory_answer_candidates = 1
    settings.org_memory_answer_judge_enabled = False
    set_graph_store(InMemoryGraphStore())


def run(cases_path: Path) -> dict:
    from fastapi.testclient import TestClient

    from app.api.routes import graph
    from app.audit import AuditService
    from app.auth.api_keys import create_api_key
    from app.auth.app_auth import create_dev_session, create_workspace
    from app.core.database import connect, row, rows
    from app.hcag_adapter import HCAGAdapter
    from app.ingestion import IngestionService
    from app.main import app

    spec = json.loads(cases_path.read_text())

    owner = create_dev_session("eval-owner@example.com", "Eval Owner")
    workspace = create_workspace("Briefing Eval", owner["token"])
    ingestion = IngestionService(graph, HCAGAdapter(graph), AuditService())
    project_id = ingestion.create_project(spec.get("company", "Eval company"))
    with connect() as conn:
        conn.execute("INSERT INTO workspace_projects VALUES (?,?)", (workspace["id"], project_id))
    key = create_api_key("Briefing eval", workspace["id"], owner["user"]["id"])
    headers = {"Authorization": f"Bearer {key['api_key']}"}
    client = TestClient(app)

    for doc in spec["documents"]:
        ingestion.ingest_item(
            project_id,
            doc["source_type"],
            doc["title"],
            doc["content"],
            source_id=doc["source_id"],
        )

    # A document that produced no memory units is invisible to every briefing,
    # however good retrieval is. Counting this separately keeps an extraction
    # miss from being misread as a ranking miss.
    coverage = {}
    for doc in spec["documents"]:
        coverage[doc["source_id"]] = sum(
            1
            for unit in rows(
                "SELECT source_ids_json FROM memory_units WHERE project_id=? AND is_latest=1",
                (project_id,),
            )
            if doc["source_id"] in json.loads(unit["source_ids_json"] or "[]")
        )

    results = []
    for case in spec["cases"]:
        response = client.post(
            "/api/briefings",
            headers=headers,
            json={
                "task": case["task"],
                "service": case.get("service", ""),
                "project_id": project_id,
                "surface": "eval",
            },
        )
        response.raise_for_status()
        brief = response.json()

        # Order matters: must_read is what an agent reads first, so the rank of
        # the first correct source is measured across the groups in display order.
        cited: list[tuple[str, str]] = []
        for group in CITED_GROUPS:
            for item in brief.get(group) or []:
                cited.append((group, str(item.get("memory_id"))))
        surfaced_sources: list[str] = []
        for _group, memory_id in cited:
            unit = row("SELECT source_ids_json FROM memory_units WHERE id=?", (memory_id,))
            for source_id in json.loads((unit or {}).get("source_ids_json") or "[]"):
                if source_id not in surfaced_sources:
                    surfaced_sources.append(source_id)

        must = case.get("must_surface", [])
        must_not = case.get("must_not_surface", [])
        found = [source for source in must if source in surfaced_sources]
        leaked = [source for source in must_not if source in surfaced_sources]
        missed = [source for source in must if source not in surfaced_sources]
        approval_ok = bool(brief.get("requires_approval")) == bool(case.get("expect_approval"))
        verdict_ok = (
            brief.get("verdict") == case["expect_verdict"] if "expect_verdict" in case else True
        )
        first_rank = next(
            (index + 1 for index, source in enumerate(surfaced_sources) if source in must),
            None,
        )
        ledger = row(
            "SELECT evidence_ids_json FROM context_events WHERE id=?",
            (brief.get("briefing_id") or "",),
        )
        results.append(
            {
                "id": case["id"],
                "gap": case.get("gap", ""),
                "task": case["task"],
                "verdict": brief.get("verdict"),
                "requires_approval": bool(brief.get("requires_approval")),
                "memory_count": brief.get("memory_count", 0),
                "surfaced_sources": surfaced_sources,
                "found": found,
                "missed": missed,
                "missed_never_extracted": [s for s in missed if coverage.get(s, 0) == 0],
                "leaked": leaked,
                "first_correct_rank": first_rank,
                # Share of what was shown that the case asked for. A briefing that
                # surfaces the right source among five unrelated ones "passes" on
                # recall but costs the agent the same attention as a wrong one.
                "precision": round(len(found) / len(surfaced_sources), 3)
                if must and surfaced_sources
                else None,
                "approval_ok": approval_ok,
                "verdict_ok": verdict_ok,
                "ledger_memory_ids": len(
                    json.loads((ledger or {}).get("evidence_ids_json") or "[]")
                ),
                "passed": not missed and not leaked and approval_ok and verdict_ok,
            }
        )

    return {"coverage": coverage, "results": results, "summary": _summarize(results)}


def _summarize(results: list[dict]) -> dict:
    required = sum(len(r["found"]) + len(r["missed"]) for r in results)
    found = sum(len(r["found"]) for r in results)
    with_must = [r for r in results if r["found"] or r["missed"]]
    reciprocal = [
        1 / r["first_correct_rank"] if r["first_correct_rank"] else 0.0 for r in with_must
    ]
    return {
        "cases": len(results),
        "passed": sum(r["passed"] for r in results),
        "pass_rate": round(sum(r["passed"] for r in results) / max(len(results), 1), 3),
        "recall": round(found / max(required, 1), 3),
        "mrr": round(sum(reciprocal) / max(len(reciprocal), 1), 3),
        "precision": round(
            sum(r["precision"] for r in with_must if r["precision"] is not None)
            / max(sum(r["precision"] is not None for r in with_must), 1),
            3,
        ),
        "cases_with_leak": sum(bool(r["leaked"]) for r in results),
        "approval_accuracy": round(
            sum(r["approval_ok"] for r in results) / max(len(results), 1), 3
        ),
        "avg_memories_shown": round(
            sum(r["memory_count"] for r in results) / max(len(results), 1), 1
        ),
        "briefings_attributable": sum(r["ledger_memory_ids"] > 0 for r in results),
    }


def _print(report: dict) -> None:
    print("\nExtraction coverage (memory units per source)")
    for source, count in report["coverage"].items():
        flag = "  <- invisible to briefings" if count == 0 else ""
        print(f"  {count:>2}  {source}{flag}")

    print("\nCases")
    for r in report["results"]:
        status = "PASS" if r["passed"] else "FAIL"
        precision = "" if r["precision"] is None else f"  precision={r['precision']:.0%}"
        print(f"  {status}  {r['id']:<28} [{r['gap']}]  verdict={r['verdict']}{precision}")
        if r["missed"]:
            never = set(r["missed_never_extracted"])
            for source in r["missed"]:
                why = "never extracted" if source in never else "extracted, not retrieved"
                print(f"          missed  {source} ({why})")
        for source in r["leaked"]:
            print(f"          leaked  {source}")
        if not r["approval_ok"]:
            print(f"          approval: got {r['requires_approval']}, expected the opposite")
        if not r["verdict_ok"]:
            print(f"          verdict: got {r['verdict']}")

    s = report["summary"]
    print("\nSummary")
    print(f"  passed               {s['passed']}/{s['cases']} ({s['pass_rate']:.0%})")
    print(f"  recall               {s['recall']:.0%} of required sources surfaced")
    print(f"  MRR                  {s['mrr']:.2f} (1.0 = right source always shown first)")
    print(
        f"  precision            {s['precision']:.0%} of surfaced sources were the ones asked for"
    )
    print(f"  cases with a leak    {s['cases_with_leak']}")
    print(f"  approval accuracy    {s['approval_accuracy']:.0%}")
    print(f"  avg memories shown   {s['avg_memories_shown']}")
    print(
        f"  attributable         {s['briefings_attributable']}/{s['cases']} ledger rows record which memories were shown"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--cases", type=Path, default=Path(__file__).with_name("briefing_cases.json")
    )
    parser.add_argument("--json", type=Path, help="Also write the full report here.")
    parser.add_argument("--min-pass", type=float, default=0.0, help="Exit 1 below this pass rate.")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="orgmemory-eval-") as tmp:
        _hermetic(Path(tmp))
        report = run(args.cases)

    _print(report)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2))
    return 1 if report["summary"]["pass_rate"] < args.min_pass else 0


if __name__ == "__main__":
    raise SystemExit(main())
