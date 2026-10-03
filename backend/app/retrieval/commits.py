"""Answer a question about one specific commit with that commit.

Ingestion stores a commit's message, author and date, not its diff. That is
enough for MemoryWorks to cite a commit in an answer, and not enough to answer
the obvious next question — "show me that commit" — which used to come back as
"I couldn't find this", about a commit it had just named.

So a question that names a commit MemoryWorks knows about is answered directly:
the stored record locates it (repository, full SHA, and proof the asker may see
it), and the change itself is fetched live from GitHub with the workspace's own
connection. When GitHub cannot be reached the stored record still answers,
with a link to the full change, instead of pretending the commit is unknown.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from app.core.database import rows
from app.graph.base import GraphEvidence

# A SHA prefix: 7–40 hex characters with at least one digit and one letter, so
# ordinary words ("deadbeef", "accede") and plain numbers are never read as one.
_SHA_RE = re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{7,40}(?![0-9A-Za-z])")

MAX_FILES_WITH_PATCH = 6
MAX_LINES_PER_FILE = 80
MAX_DIFF_LINES = 400

CommitFetcher = Callable[[str, str], dict[str, Any]]


def commit_references(query: str) -> list[str]:
    return [
        token.lower()
        for token in _SHA_RE.findall(query)
        if re.search(r"\d", token) and re.search(r"[a-fA-F]", token)
    ]


def find_commit(prefix: str, project_ids: list[str]) -> dict[str, Any] | None:
    """The stored commit record matching ``prefix`` within the caller's projects.

    Only an unambiguous match counts: two commits sharing a short prefix is a
    question to ask, not a guess to make.
    """
    if not project_ids:
        return None
    placeholders = ",".join("?" for _ in project_ids)
    matches = rows(
        "SELECT id, project_id, source_id, source_title, source_url, content, metadata_json "
        "FROM knowledge_items WHERE source_type='github_commit' "
        f"AND project_id IN ({placeholders}) AND source_id LIKE ? LIMIT 3",
        (*project_ids, f"commit-source:%:{prefix}%"),
    )
    unique = {str(item["source_id"]): item for item in matches}
    if len(unique) != 1:
        return None
    record = next(iter(unique.values()))
    metadata = json.loads(record.get("metadata_json") or "{}")
    _, slug, sha = str(record["source_id"]).split(":", 2)
    return {
        **record,
        "metadata": metadata,
        "slug": str(metadata.get("repository") or slug),
        "sha": str(metadata.get("commit_sha") or sha),
    }


def _field(content: str, label: str) -> str:
    match = re.search(rf"^{label}:\s*(.*)$", content, re.M)
    return match.group(1).strip() if match else ""


def _stored_summary(record: dict[str, Any]) -> tuple[str, str, str]:
    content = str(record.get("content") or "")
    message = content.split("Message:", 1)[1].strip() if "Message:" in content else ""
    return message, _field(content, "Author"), _field(content, "Committed at")


def commit_answer(
    record: dict[str, Any], fetched: dict[str, Any] | None, fetch_error: str = ""
) -> dict[str, Any]:
    """Markdown for one commit: what it says, who made it, and what it changed."""
    sha = str((fetched or {}).get("sha") or record["sha"])
    url = str((fetched or {}).get("html_url") or record.get("source_url") or "")
    stored_message, stored_author, stored_date = _stored_summary(record)
    payload = (fetched or {}).get("commit") or {}
    author_payload = payload.get("author") or {}
    message = str(payload.get("message") or stored_message).strip()
    author = (
        ((fetched or {}).get("author") or {}).get("login")
        or author_payload.get("name")
        or stored_author
        or "unknown"
    )
    date = str(author_payload.get("date") or stored_date)
    headline, _, body = message.partition("\n")

    lines = [f"**Commit {sha[:12]} — {headline.strip() or 'No message'}**"]
    if body.strip():
        lines += ["", body.strip()]
    lines += ["", f"- Repository: `{record['slug']}`", f"- Author: {author}"]
    if date:
        lines.append(f"- Committed: {date}")

    files = list((fetched or {}).get("files") or [])
    if fetched is not None:
        stats = fetched.get("stats") or {}
        lines.append(
            f"- {len(files)} file{'s' if len(files) != 1 else ''} changed, "
            f"+{stats.get('additions', 0)} −{stats.get('deletions', 0)}"
        )
        if files:
            lines += ["", "### Files changed"]
            lines += [
                f"- `{item.get('filename')}` ({item.get('status', 'modified')}, "
                f"+{item.get('additions', 0)} −{item.get('deletions', 0)})"
                for item in files
            ]
        shown = 0
        diff_lines: list[str] = []
        omitted = 0
        for item in files:
            patch = str(item.get("patch") or "")
            if not patch:
                continue
            if len(diff_lines) >= MAX_DIFF_LINES or shown >= MAX_FILES_WITH_PATCH:
                omitted += 1
                continue
            patch_lines = patch.splitlines()
            kept = patch_lines[:MAX_LINES_PER_FILE]
            diff_lines += [f"--- {item.get('filename')}", *kept]
            if len(patch_lines) > len(kept):
                diff_lines.append(f"… {len(patch_lines) - len(kept)} more lines in this file")
            shown += 1
        if diff_lines:
            lines += ["", "### Diff", "```diff", *diff_lines, "```"]
        if omitted:
            lines.append(f"{omitted} more file diff{'s' if omitted != 1 else ''} on GitHub.")
    else:
        reason = f" ({fetch_error})" if fetch_error else ""
        lines += [
            "",
            f"MemoryWorks has this commit's record but could not fetch its diff from "
            f"GitHub{reason}.",
        ]
    if url:
        lines += ["", f"Full change on GitHub: {url}"]

    return {
        "answer": "\n".join(lines),
        "likely_cause": "Not applicable — this is a commit lookup.",
        "safe_actions": [],
        "approval_required": [],
        "sufficient": True,
        "supporting_chunk_ids": [str(record["id"])],
        "answer_kind": "commit_lookup",
    }


def commit_evidence(record: dict[str, Any], project_name: str) -> GraphEvidence:
    return GraphEvidence(
        chunk_id=str(record["id"]),
        text=str(record.get("content") or ""),
        source_type="github_commit",
        source_title=str(record.get("source_title") or f"Commit {record['sha'][:12]}"),
        source_url=str(record.get("source_url") or ""),
        metadata={
            **record.get("metadata", {}),
            "project_id": record["project_id"],
            "project_name": project_name,
        },
        score=100.0,
    )
