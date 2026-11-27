"""Command-line interface for MemoryWorks."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx

from . import github_check, hooks
from .client import MemoryWorks, env_setting
from .exceptions import MemoryWorksError
from .models import AskResponse, BriefingOutcomeReceipt, BriefingResponse

OUTCOME_LABELS = ("succeeded", "failed", "partial", "abandoned", "unknown")


def _json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print the complete response as JSON.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orgmemory",
        description="Source-backed organizational memory for agents.",
    )
    parser.add_argument(
        "--api-url",
        default=env_setting("API_URL", "http://localhost:8000"),
        help="MemoryWorks API URL (default: MEMORYWORKS_API_URL, then ORGMEMORY_API_URL, "
        "or localhost).",
    )
    parser.add_argument(
        "--api-key",
        default=env_setting("API_KEY"),
        help="API key (default: MEMORYWORKS_API_KEY, then ORGMEMORY_API_KEY).",
    )
    _json_flag(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    health = subparsers.add_parser("health", help="Check the MemoryWorks API.")
    _json_flag(health)

    projects = subparsers.add_parser("projects", help="List visible projects.")
    _json_flag(projects)

    create_project = subparsers.add_parser("project-create", help="Create a project.")
    create_project.add_argument("name", help="Project name.")
    create_project.add_argument(
        "--team-id",
        action="append",
        default=[],
        help="Grant a team write access; repeat for multiple teams.",
    )
    _json_flag(create_project)

    ask = subparsers.add_parser("ask", help="Ask organizational memory a question.")
    ask.add_argument("project_id")
    ask.add_argument("query")
    ask.add_argument(
        "--token-budget",
        type=int,
        default=6000,
        choices=range(500, 32001),
        metavar="500..32000",
    )
    ask.add_argument(
        "--model",
        choices=["gpt", "claude", "gemini", "grok", "kimi"],
        help="Model provider used after MemoryWorks compiles grounded context.",
    )
    _json_flag(ask)

    ingest = subparsers.add_parser("ingest", help="Add a text source to memory.")
    ingest.add_argument("project_id")
    source = ingest.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="UTF-8 text file to ingest.")
    source.add_argument("--content", help="Inline text to ingest.")
    ingest.add_argument("--title", help="Source title; defaults to the filename.")
    ingest.add_argument(
        "--source-type",
        default="doc",
        choices=[
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
        ],
    )
    ingest.add_argument("--source-url", default="")
    ingest.add_argument("--source-id")
    ingest.add_argument("--team-id", action="append", default=[])
    _json_flag(ingest)

    memories = subparsers.add_parser("memories", help="List project memory units.")
    memories.add_argument("project_id")
    memories.add_argument("--type", dest="memory_type", default="")
    memories.add_argument(
        "--all-versions",
        action="store_true",
        help="Include superseded memory versions.",
    )
    _json_flag(memories)

    graph = subparsers.add_parser("graph", help="Show a project graph summary.")
    graph.add_argument("project_id")
    _json_flag(graph)

    swarm = subparsers.add_parser("swarm", help="Inspect one context-swarm run.")
    swarm.add_argument("run_id")
    _json_flag(swarm)

    envelope = subparsers.add_parser(
        "context",
        help="Inspect a durable context envelope.",
    )
    envelope.add_argument("envelope_id")
    _json_flag(envelope)

    work = subparsers.add_parser("work", help="Create or inspect memory work.")
    work_commands = work.add_subparsers(dest="work_command", required=True)
    work_create = work_commands.add_parser("create", help="Create memory work.")
    work_create.add_argument("project_id")
    work_create.add_argument("objective")
    _json_flag(work_create)
    work_list = work_commands.add_parser("list", help="List memory work.")
    work_list.add_argument("--project-id", default="")
    work_list.add_argument("--limit", type=int, default=100)
    _json_flag(work_list)
    work_get = work_commands.add_parser("get", help="Get one memory work item.")
    work_get.add_argument("work_id")
    _json_flag(work_get)

    brief = subparsers.add_parser(
        "brief",
        help="Get a cited pre-action briefing (advice, not authorization).",
    )
    brief.add_argument("task", help="What you are about to do (max 2,000 characters).")
    brief.add_argument("--service", default="", help="Service the change touches.")
    brief.add_argument(
        "--project-id", default="", help="Limit the briefing to one project."
    )
    brief.add_argument(
        "--surface", default="cli", help="Where the change is happening."
    )
    _json_flag(brief)

    outcome = subparsers.add_parser(
        "outcome",
        help="Record what happened after a briefing (appends to the outcome ledger).",
    )
    outcome.add_argument(
        "briefing_id", help="briefing_id returned by `orgmemory brief`."
    )
    outcome.add_argument(
        "action", help="What was done, e.g. edit_applied or pr_opened."
    )
    outcome.add_argument(
        "--outcome",
        required=True,
        choices=OUTCOME_LABELS,
        help="Observed result.",
    )
    outcome.add_argument("--target", default="", help="What the action touched.")
    outcome.add_argument("--surface", default="cli")
    outcome.add_argument("--reason", default="", help="Evidence for the outcome.")
    outcome.add_argument(
        "--detail",
        default="",
        help="Extra structured detail as a JSON object.",
    )
    _json_flag(outcome)

    hook = subparsers.add_parser(
        "hook",
        help="Editor pre-tool hook: read the event on stdin, print the hook response.",
    )
    hook.add_argument("editor", choices=["claude-code", "cursor"])
    hook.add_argument("--service", default="")
    hook.add_argument("--project-id", default="")

    check = subparsers.add_parser(
        "github-check",
        help="Brief a GitHub pull request and post the result as a Check and PR comment.",
    )
    check.add_argument(
        "--event-path",
        type=Path,
        default=os.getenv("GITHUB_EVENT_PATH") or None,
        help="GitHub event JSON (default: GITHUB_EVENT_PATH).",
    )
    check.add_argument("--repository", default=os.getenv("GITHUB_REPOSITORY", ""))
    check.add_argument(
        "--github-api-url",
        default=os.getenv("GITHUB_API_URL", "https://api.github.com"),
    )
    check.add_argument("--service", default="", help="Service this repository changes.")
    check.add_argument("--project-id", default="")
    check.add_argument(
        "--app-url",
        default=env_setting("APP_URL", "https://memoryworks.app"),
        help="Web app URL used for memory links.",
    )
    check.add_argument(
        "--approval-conclusion",
        choices=["action_required", "neutral"],
        default="action_required",
        help="Check conclusion when the briefing says a person has to decide.",
    )
    check.add_argument(
        "--show-memory-text",
        action="store_true",
        help="Include memory subjects in the check and comment (visible to repo readers).",
    )
    check.add_argument("--no-comment", action="store_true", help="Post the check only.")
    check.add_argument(
        "--rebrief",
        action="store_true",
        help="Serve a new briefing even if this SHA already has one for the same request.",
    )
    check.add_argument(
        "--dry-run",
        action="store_true",
        help="Serve the briefing and print the summary without calling GitHub.",
    )
    _json_flag(check)

    return parser


def _detail(value: str) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("--detail must be a JSON object")
    return parsed


def _request(client: MemoryWorks, args: argparse.Namespace) -> Any:
    if args.command == "health":
        return client.health()
    if args.command == "projects":
        return client.projects()
    if args.command == "project-create":
        return client.create_project(args.name, team_ids=args.team_id)
    if args.command == "ask":
        return client.ask(
            args.project_id,
            args.query,
            token_budget=args.token_budget,
            model=args.model,
        )
    if args.command == "ingest":
        if args.file:
            content = args.file.read_text(encoding="utf-8")
            title = args.title or args.file.name
        else:
            content = args.content
            title = args.title or "CLI note"
        return client.ingest_source(
            args.project_id,
            content,
            title=title,
            source_type=args.source_type,
            source_url=args.source_url,
            source_id=args.source_id,
            team_ids=args.team_id,
        )
    if args.command == "memories":
        return client.list_memories(
            args.project_id,
            memory_type=args.memory_type,
            latest=None if args.all_versions else True,
        )
    if args.command == "graph":
        return client.graph_summary(args.project_id)
    if args.command == "swarm":
        return client.swarm_run(args.run_id)
    if args.command == "context":
        return client.context_envelope(args.envelope_id)
    if args.command == "brief":
        return client.get_briefing(
            args.task,
            service=args.service,
            project_id=args.project_id,
            surface=args.surface,
        )
    if args.command == "outcome":
        return client.record_briefing_outcome(
            args.briefing_id,
            args.action,
            outcome=args.outcome,
            target=args.target,
            surface=args.surface,
            reason=args.reason,
            detail=_detail(args.detail),
        )
    if args.command == "work":
        if args.work_command == "create":
            return client.create_work(args.project_id, args.objective)
        if args.work_command == "list":
            return client.list_work(project_id=args.project_id, limit=args.limit)
        return client.get_work(args.work_id)
    raise ValueError(f"Unknown command: {args.command}")


def _as_json(value: Any) -> Any:
    if isinstance(value, AskResponse):
        return value.raw
    if hasattr(value, "raw"):
        return value.raw
    return value


def _print_briefing(value: BriefingResponse) -> None:
    print(f"verdict: {value.verdict}")
    print(f"briefing_id: {value.briefing_id or 'not recorded'}")
    print(value.headline)
    if value.requires_approval:
        print("\nA person has to agree first:")
        for reason in value.requires_approval:
            print(f"  - {reason}")
    groups = (
        ("must read", value.must_read),
        ("constraint", value.constraints),
        ("prior incident", value.prior_incidents),
        ("blast radius", value.blast_radius),
        ("procedure", value.procedures),
    )
    cited = [(label, item) for label, items in groups for item in items]
    if cited:
        print("\nCited memory:")
        for label, item in cited:
            print(
                f"  - [{item.memory_id}] {label}: {item.subject} — {item.why_it_matters}"
            )
    if value.open_questions:
        print("\nOpen questions:")
        for question in value.open_questions:
            print(f"  - {question}")
    print("\nA briefing is advice, not authorization.")
    if value.briefing_id:
        print(
            f"Afterwards: orgmemory outcome {value.briefing_id} <action> "
            "--outcome <succeeded|failed|partial|abandoned|unknown>"
        )


def _print_human(value: Any) -> None:
    if isinstance(value, BriefingResponse):
        _print_briefing(value)
        return
    if isinstance(value, BriefingOutcomeReceipt):
        print(
            f"recorded {value.outcome} for {value.briefing_id} "
            f"(action {value.action_id}, outcome {value.outcome_id})"
        )
        return
    if isinstance(value, AskResponse):
        print(value.answer)
        print(
            f"\nconfidence {value.confidence:.0%} · "
            f"{len(value.evidence)} sources · "
            f"context {value.context_envelope_id or 'not-created'} · "
            f"model {value.model.get('label') or 'deterministic'}"
        )
        for item in value.evidence[:5]:
            title = item.source_title or item.source_id or item.chunk_id
            print(f"  - {title}")
        return
    payload = _as_json(value)
    if isinstance(payload, list):
        if not payload:
            print("No results.")
            return
        for item in payload:
            if isinstance(item, dict):
                identifier = item.get("id") or item.get("project_id") or "—"
                label = (
                    item.get("name")
                    or item.get("title")
                    or item.get("objective")
                    or item.get("status")
                    or ""
                )
                print(f"{identifier}\t{label}".rstrip())
            else:
                print(item)
        return
    if isinstance(payload, dict):
        for key, value in payload.items():
            rendered = (
                json.dumps(value, ensure_ascii=False)
                if isinstance(value, dict | list)
                else str(value)
            )
            print(f"{key}: {rendered}")
        return
    print(payload)


def _hook(args: argparse.Namespace) -> int:
    """Editor hooks must answer quickly and never crash the editor's tool call."""
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        event = {}
    if not isinstance(event, dict):
        event = {}
    if args.editor == "claude-code":
        response = hooks.claude_code(
            event, service=args.service, project_id=args.project_id
        )
    else:
        response = hooks.cursor(event, service=args.service, project_id=args.project_id)
    if response is not None:
        print(json.dumps(response, ensure_ascii=False))
    return 0


def _github_check(args: argparse.Namespace) -> int:
    if not args.event_path:
        print(
            "error: no GitHub event; set GITHUB_EVENT_PATH or --event-path",
            file=sys.stderr,
        )
        return 1
    event = json.loads(args.event_path.read_text(encoding="utf-8"))
    github = (
        None
        if args.dry_run
        else github_check.GitHubAPI(
            os.getenv("GITHUB_TOKEN", ""), api_url=args.github_api_url
        )
    )
    try:
        with MemoryWorks(base_url=args.api_url, api_key=args.api_key) as client:
            result = github_check.run(
                event=event,
                github=github,
                memoryworks=client,
                repository=args.repository,
                service=args.service,
                project_id=args.project_id,
                app_url=args.app_url,
                approval_conclusion=args.approval_conclusion,
                show_memory_text=args.show_memory_text,
                comment=not args.no_comment,
                rebrief=args.rebrief,
            )
    finally:
        if github is not None:
            github.close()
    if getattr(args, "json", False):
        print(json.dumps(result.as_dict(), indent=2, ensure_ascii=False))
    elif result.reused:
        print(
            f"{github_check.CHECK_NAME}: reused check {result.check_run_id} "
            f"({result.state}, briefing {result.briefing_id}) for this SHA and request"
        )
    else:
        print(result.summary)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "hook":
        return _hook(args)
    if args.command == "github-check":
        try:
            return _github_check(args)
        except (github_check.GitHubCheckError, ValueError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    try:
        with MemoryWorks(base_url=args.api_url, api_key=args.api_key) as client:
            result = _request(client, args)
    except (
        MemoryWorksError,
        httpx.RequestError,
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(_as_json(result), indent=2, ensure_ascii=False, default=str))
    else:
        _print_human(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
