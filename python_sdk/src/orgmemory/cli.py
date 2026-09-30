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

from .client import MemoryWorks
from .exceptions import MemoryWorksError
from .models import AskResponse


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
        default=os.getenv("ORGMEMORY_API_URL", "http://localhost:8000"),
        help="MemoryWorks API URL (default: ORGMEMORY_API_URL or localhost).",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("ORGMEMORY_API_KEY", ""),
        help="API key (default: ORGMEMORY_API_KEY).",
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

    return parser


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


def _print_human(value: Any) -> None:
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        with MemoryWorks(base_url=args.api_url, api_key=args.api_key) as client:
            result = _request(client, args)
    except (MemoryWorksError, httpx.RequestError, OSError, UnicodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(_as_json(result), indent=2, ensure_ascii=False, default=str))
    else:
        _print_human(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
