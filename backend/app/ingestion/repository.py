from __future__ import annotations

import fnmatch
import hashlib
import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.connectors.github import GitHubConnector
from app.core.config import settings
from app.core.database import connect, row, rows
from app.graph.base import GraphStore
from app.graph.graph_builder import RepoGraphBuilder

from .safety import sanitize_for_index
from .service import IngestionService

IGNORED_DIRS = {
    ".git",
    ".next",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "demo_data",
    "__pycache__",
}
USEFUL_NAMES = {
    "readme.md",
    "dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
    "jenkinsfile",
    "requirements.txt",
    "pyproject.toml",
    "package.json",
    "go.mod",
    "cargo.toml",
    "makefile",
    ".env.example",
}
USEFUL_SUFFIXES = {
    ".md",
    ".txt",
    ".log",
    ".yml",
    ".yaml",
    ".json",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".properties",
    ".xml",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".sh",
    ".sql",
    ".graphql",
    ".proto",
    ".vue",
    ".svelte",
    ".ejs",
    ".css",
    ".ipynb",
}
EXCLUDED_FILES = {
    ".env",
    ".env.local",
    ".env.production",
    "cookies.txt",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
}
EXCLUDED_PATHS = {
    "backend/scripts/load_demo.py",
}
SENSITIVE_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}
MAX_FILE_BYTES = 1_500_000
MAX_FILES = 5000
INDEX_SCHEMA_VERSION = 3


class RepositoryIngestor:
    def __init__(
        self, ingestion: IngestionService, graph: GraphStore, github: GitHubConnector | None = None
    ):
        self.ingestion = ingestion
        self.graph = graph
        self.github = github or GitHubConnector()
        self.graph_builder = RepoGraphBuilder(graph)

    def ingest(self, source: str, project_name: str) -> dict[str, Any]:
        existing = row("SELECT id FROM projects WHERE repository=?", (source,))
        project_id = self.ingestion.create_project(project_name, source)
        repository_id = f"repo:{project_id}"
        root, temporary = self._checkout(source, project_id)
        files_scanned = issues_scanned = pull_requests_scanned = chunks_created = 0
        knowledge_items_created = 0
        memory_units_created = 0
        graph_nodes_created = graph_edges_created = 0
        warnings: list[str] = []
        previous_items: dict[str, dict[str, str]] = {}
        current_files: dict[str, str] = {}
        seen_sources: set[str] = set()
        changed_sources: set[str] = set()
        unchanged_sources: set[str] = set()
        fully_scanned_types = {"repo_file"}

        if existing:
            for item in rows(
                "SELECT source_id,source_type,content,metadata_json FROM knowledge_items "
                "WHERE project_id=? AND source_type IN "
                "('repo_file','github_issue','pull_request','repository_metadata','github_commit')",
                (project_id,),
            ):
                try:
                    previous_metadata = json.loads(item["metadata_json"] or "{}")
                except json.JSONDecodeError:
                    previous_metadata = {}
                previous_items[item["source_id"]] = {
                    "source_type": item["source_type"],
                    "content_hash": hashlib.sha256(
                        item["content"].encode("utf-8", errors="replace")
                    ).hexdigest(),
                    "index_schema_version": str(previous_metadata.get("index_schema_version") or 0),
                }

        def ingest_if_changed(
            source_type: str,
            title: str,
            content: str,
            source_url: str,
            source_id: str,
            metadata: dict[str, Any],
        ) -> tuple[dict[str, Any] | None, str]:
            nonlocal knowledge_items_created, chunks_created, memory_units_created
            sanitized, redaction_count = sanitize_for_index(content, source_url or title)
            digest = hashlib.sha256(sanitized.encode("utf-8", errors="replace")).hexdigest()
            seen_sources.add(source_id)
            previous = previous_items.get(source_id)
            if (
                previous
                and previous["content_hash"] == digest
                and int(previous.get("index_schema_version") or 0) >= INDEX_SCHEMA_VERSION
            ):
                unchanged_sources.add(source_id)
                return None, sanitized
            if previous:
                self.graph.delete_source_knowledge(project_id, source_id, source_type)
                with connect() as conn:
                    conn.execute(
                        "DELETE FROM knowledge_items WHERE project_id=? AND source_id=?",
                        (project_id, source_id),
                    )
            metadata = {
                **metadata,
                "content_hash": digest,
                "secret_redactions": redaction_count,
                "index_schema_version": INDEX_SCHEMA_VERSION,
                "force_memory_reconcile": bool(
                    previous
                    and int(previous.get("index_schema_version") or 0) < INDEX_SCHEMA_VERSION
                ),
            }
            result = self.ingestion.ingest_item(
                project_id,
                source_type,
                title,
                sanitized,
                source_url,
                source_id,
                metadata,
            )
            knowledge_items_created += 1
            chunks_created += result["chunks_created"]
            memory_units_created += result.get("memory_units_created", 0)
            changed_sources.add(source_id)
            return result, sanitized

        try:
            commit_sha = self._git_value(root, "rev-parse", "HEAD")
            codeowners = self._codeowners(root)
            for path in self._files(root):
                relative = path.relative_to(root).as_posix()
                try:
                    raw_content = self._read_indexable_text(path)
                except (OSError, ValueError) as exc:
                    warnings.append(f"Skipped {relative}: {exc}")
                    continue
                source_id = f"file:{project_id}:{relative}"
                content, _ = sanitize_for_index(raw_content, relative)
                content_hash = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
                current_files[source_id] = content_hash
                source_url = self._file_url(source, relative, commit_sha)
                owner, owner_source = self._file_owner(root, relative, codeowners)
                result, sanitized = ingest_if_changed(
                    "repo_file",
                    relative,
                    content,
                    source_url,
                    source_id,
                    {
                        "path": relative,
                        "size": path.stat().st_size,
                        "repository_id": repository_id,
                        "repository": self.github.slug(source) or source,
                        "content_hash": content_hash,
                        "commit_sha": commit_sha,
                        "source_version": commit_sha,
                        "source_updated_at": self._git_value(
                            root, "log", "-1", "--format=%cI", "--", relative
                        )
                        or path.stat().st_mtime,
                        "owner": owner,
                        "owner_source": owner_source,
                    },
                )
                if result:
                    graph_delta = self.graph_builder.build_file(
                        project_id,
                        repository_id,
                        root,
                        path,
                        relative,
                        sanitized,
                        source_id,
                        source_url,
                    )
                    graph_nodes_created += graph_delta["nodes_created"]
                    graph_edges_created += graph_delta["edges_created"]
                files_scanned += 1

            slug = self.github.slug(source)
            if slug and self.github.token():
                try:
                    self._ingest_repository_metadata(
                        slug, project_id, repository_id, ingest_if_changed
                    )
                    fully_scanned_types.update({"repository_metadata", "github_commit"})
                except Exception as exc:
                    warnings.append(f"Repository metadata could not be indexed: {exc}")
                try:
                    issues_scanned, _ = self._ingest_issues(
                        slug, project_id, repository_id, ingest_if_changed
                    )
                    fully_scanned_types.add("github_issue")
                except Exception as exc:
                    warnings.append(f"Issue threads could not be indexed: {exc}")
                try:
                    pull_requests_scanned, _ = self._ingest_pull_requests(
                        slug, project_id, repository_id, ingest_if_changed
                    )
                    fully_scanned_types.add("pull_request")
                except Exception as exc:
                    warnings.append(f"Pull request threads could not be indexed: {exc}")
            elif slug:
                warnings.append(
                    "Repository files were ingested. Connect GitHub to include issues and pull requests."
                )

            deleted_sources = {
                source_id
                for source_id, previous in previous_items.items()
                if source_id not in seen_sources and previous["source_type"] in fully_scanned_types
            }
            for source_id in deleted_sources:
                source_type = previous_items[source_id]["source_type"]
                self.ingestion.memory.retire_source_memories(project_id, source_id)
                self.graph.delete_source_knowledge(project_id, source_id, source_type)
                with connect() as conn:
                    conn.execute(
                        "DELETE FROM knowledge_items WHERE project_id=? AND source_id=?",
                        (project_id, source_id),
                    )
        finally:
            if temporary:
                shutil.rmtree(root, ignore_errors=True)
        previous_files = {
            source_id: value["content_hash"]
            for source_id, value in previous_items.items()
            if value["source_type"] == "repo_file"
        }
        changed_files = sorted(
            source_id.removeprefix(f"file:{project_id}:")
            for source_id, previous_hash in previous_files.items()
            if current_files.get(source_id) != previous_hash
        )
        changed_files.extend(
            sorted(
                source_id.removeprefix(f"file:{project_id}:")
                for source_id in current_files
                if source_id not in previous_files
            )
        )
        changed_files = sorted(set(changed_files))
        return {
            "project_id": project_id,
            "files_scanned": files_scanned,
            "issues_scanned": issues_scanned,
            "pull_requests_scanned": pull_requests_scanned,
            "knowledge_items_created": knowledge_items_created,
            "knowledge_chunks_created": chunks_created,
            "memory_units_created": memory_units_created,
            "graph_nodes_created": graph_nodes_created,
            "graph_edges_created": graph_edges_created,
            "warnings": warnings,
            "incremental": {
                "mode": "content_hash_delta",
                "sources_changed": len(changed_sources),
                "sources_unchanged": len(unchanged_sources),
                "sources_deleted": len(deleted_sources),
                "full_rebuild": False,
            },
            "change": {
                "type": "repository_reingestion",
                "ref": source,
                "changed_files": changed_files,
                "evidence": [
                    {
                        "kind": "repository_reingestion",
                        "file": path,
                        "detail": "File content hash changed between ingestions.",
                    }
                    for path in changed_files
                ],
            },
            "status": "success",
        }

    def _ingest_repository_metadata(
        self,
        slug: str,
        project_id: str,
        repository_id: str,
        ingest_if_changed: Callable[..., tuple[dict[str, Any] | None, str]],
    ) -> tuple[int, int]:
        """Index repository identity plus a safe, incremental commit-history corpus."""
        repository = self.github.repository(slug)
        commits = self.github.recent_commits(slug, limit=50)
        latest = commits[0] if commits else {}
        commit_payload = latest.get("commit") or {}
        author_payload = commit_payload.get("author") or {}
        linked_author = latest.get("author") or {}
        owner = (repository.get("owner") or {}).get("login", "")
        author = linked_author.get("login") or author_payload.get("name", "")
        commit_url = latest.get("html_url", "")
        commit_sha = latest.get("sha", "")
        updated_at = repository.get("updated_at", "")
        lines = [
            f"Repository: {repository.get('full_name') or slug}",
            f"Name: {repository.get('name') or slug.rsplit('/', 1)[-1]}",
            f"URL: {repository.get('html_url') or f'https://github.com/{slug}'}",
            f"Owner: {owner}",
            f"Description: {repository.get('description') or ''}",
            f"Default branch: {repository.get('default_branch') or ''}",
            f"Primary language: {repository.get('language') or ''}",
            f"Topics: {', '.join(repository.get('topics') or [])}",
            f"Latest commit SHA: {commit_sha}",
            f"Latest commit author: {author}",
            f"Latest commit date: {author_payload.get('date') or ''}",
            f"Latest commit message: {str(commit_payload.get('message') or '').splitlines()[0]}",
            f"Latest commit URL: {commit_url}",
        ]
        content = "\n".join(line for line in lines if not line.endswith(": "))
        source_url = repository.get("html_url") or f"https://github.com/{slug}"
        result, _ = ingest_if_changed(
            "repository_metadata",
            "repository-metadata",
            content,
            source_url,
            f"repository-metadata:{slug}",
            {
                "repository": slug,
                "owner": owner,
                "latest_commit_author": author,
                "latest_commit_date": author_payload.get("date") or "",
                "latest_commit_url": commit_url,
                "commit_sha": commit_sha,
                "source_updated_at": updated_at,
            },
        )
        self.graph.upsert_repository(
            {
                "id": repository_id,
                "project_id": project_id,
                "url": source_url,
                "name": repository.get("name") or slug.rsplit("/", 1)[-1],
                "full_name": repository.get("full_name") or slug,
                "owner": owner,
                "default_branch": repository.get("default_branch") or "",
            }
        )
        if commit_sha:
            commit_id = f"commit:{slug}:{commit_sha}"
            self.graph.upsert_node(
                "Commit",
                {
                    "id": commit_id,
                    "project_id": project_id,
                    "sha": commit_sha,
                    "author": author,
                    "committed_at": author_payload.get("date") or "",
                    "url": commit_url,
                },
            )
            self.graph.link("REPO_HAS_COMMIT", "Repository", repository_id, "Commit", commit_id)
        total = 1
        for commit in commits:
            payload = commit.get("commit") or {}
            author_payload = payload.get("author") or {}
            linked_author = commit.get("author") or {}
            sha = str(commit.get("sha") or "")
            if not sha:
                continue
            author_name = linked_author.get("login") or author_payload.get("name") or "unknown"
            message = str(payload.get("message") or "").strip()
            url = str(commit.get("html_url") or f"https://github.com/{slug}/commit/{sha}")
            commit_content = "\n".join(
                (
                    f"Repository: {slug}",
                    f"Commit SHA: {sha}",
                    f"Author: {author_name}",
                    f"Committed at: {author_payload.get('date') or ''}",
                    f"Message: {message}",
                )
            )
            ingest_if_changed(
                "github_commit",
                f"Commit {sha[:12]}: {message.splitlines()[0] if message else 'No message'}",
                commit_content,
                url,
                f"commit-source:{slug}:{sha}",
                {
                    "repository": slug,
                    "commit_sha": sha,
                    "source_version": sha,
                    "source_updated_at": author_payload.get("date") or "",
                    "owner": author_name,
                    "owner_source": "commit_author",
                },
            )
            commit_id = f"commit:{slug}:{sha}"
            self.graph.upsert_node(
                "Commit",
                {
                    "id": commit_id,
                    "project_id": project_id,
                    "sha": sha,
                    "author": author_name,
                    "committed_at": author_payload.get("date") or "",
                    "url": url,
                },
            )
            self.graph.link("REPO_HAS_COMMIT", "Repository", repository_id, "Commit", commit_id)
            total += 1
        return total, 0

    def _checkout(self, source: str, project_id: str) -> tuple[Path, bool]:
        # Production accepts only remote repository URLs. A local path would
        # let any readable directory — including host mounts — become ingested
        # source content. Development keeps local checkouts for the ingest cache.
        if settings.environment.casefold() == "production" and not source.startswith(
            ("http://", "https://", "git@")
        ):
            raise ValueError(
                "Local repository paths are rejected in production; "
                "ingest from a remote repository URL"
            )
        expanded = Path(source).expanduser()
        if expanded.exists() and expanded.is_dir():
            return expanded.resolve(), False
        normalized = source.replace("\\", "/")
        if "/startup/" in normalized:
            relative = normalized.split("/startup/", 1)[1]
            mounted = settings.local_repo_mount / relative
            if mounted.exists() and mounted.is_dir():
                return mounted.resolve(), False
        mounted = settings.local_repo_mount / Path(source).name
        if not source.startswith(("http://", "https://", "git@")) and mounted.exists():
            return mounted.resolve(), False
        target = settings.repo_cache_dir / project_id
        target.parent.mkdir(parents=True, exist_ok=True)
        self.github.clone(source, target)
        return target, True

    def _files(self, root: Path):
        count = 0
        for path in sorted(root.rglob("*")):
            if count >= MAX_FILES:
                break
            if not path.is_file() or any(
                part in IGNORED_DIRS for part in path.relative_to(root).parts
            ):
                continue
            relative = path.relative_to(root).as_posix()
            lower = path.name.lower()
            if (
                relative.casefold() in EXCLUDED_PATHS
                or lower in EXCLUDED_FILES
                or path.suffix.lower() in SENSITIVE_SUFFIXES
            ):
                continue
            in_docs_or_ci = relative.startswith(("docs/", ".github/workflows/"))
            config_like = any(
                fnmatch.fnmatch(lower, pattern)
                for pattern in ("*.config.*", "config.*", "*.service")
            )
            if not (
                lower in USEFUL_NAMES
                or path.suffix.lower() in USEFUL_SUFFIXES
                or in_docs_or_ci
                or config_like
            ):
                continue
            if path.stat().st_size > MAX_FILE_BYTES or b"\0" in path.read_bytes()[:2048]:
                continue
            count += 1
            yield path

    @staticmethod
    def _read_indexable_text(path: Path) -> str:
        raw = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.casefold() != ".ipynb":
            return raw

        try:
            notebook = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid Jupyter notebook JSON") from exc

        metadata = notebook.get("metadata") or {}
        kernel = metadata.get("kernelspec") or {}
        language_info = metadata.get("language_info") or {}
        language = str(kernel.get("language") or language_info.get("name") or "Python")
        sections = [f"# Jupyter notebook: {path.name}", f"Language: {language}"]
        for index, cell in enumerate(notebook.get("cells") or [], start=1):
            cell_type = str(cell.get("cell_type") or "")
            if cell_type not in {"markdown", "code"}:
                continue
            source = cell.get("source") or ""
            if isinstance(source, list):
                source = "".join(str(line) for line in source)
            else:
                source = str(source)
            source = source.strip()
            if not source:
                continue
            if cell_type == "markdown":
                sections.extend((f"\n## Markdown cell {index}", source))
            else:
                sections.extend(
                    (f"\n## Code cell {index}", f"```{language.casefold()}", source, "```")
                )
        return "\n".join(sections).strip()

    def _ingest_issues(
        self,
        slug: str,
        project_id: str,
        repository_id: str,
        ingest_if_changed: Callable[..., tuple[dict[str, Any] | None, str]],
    ) -> tuple[int, int]:
        total = chunks = 0
        for issue in self.github.list_issues(slug):
            source_id = f"issue:{slug}:{issue['number']}"
            title = f"Issue #{issue['number']}: {issue['title']}"
            labels = ", ".join(label["name"] for label in issue.get("labels", []))
            comments = self.github.issue_comments(slug, int(issue["number"]))
            thread = "\n\n".join(
                f"Comment by {(comment.get('user') or {}).get('login') or 'unknown'} "
                f"at {comment.get('created_at') or ''}\n{comment.get('body') or ''}\n"
                f"Link: {comment.get('html_url') or ''}"
                for comment in comments
            )
            content = (
                f"{title}\nState: {issue.get('state')}\nLabels: {labels}\n"
                f"Author: {(issue.get('user') or {}).get('login') or 'unknown'}\n\n"
                f"{issue.get('body') or ''}\n\nDiscussion\n{thread}"
            )
            result, _ = ingest_if_changed(
                "github_issue",
                title,
                content,
                issue["html_url"],
                source_id,
                {
                    "number": issue["number"],
                    "repository": slug,
                    "source_updated_at": issue.get("updated_at", ""),
                    "owner": (issue.get("assignee") or {}).get("login")
                    or (issue.get("user") or {}).get("login")
                    or "",
                    "owner_source": "issue_assignee_or_author",
                    "comment_count": len(comments),
                },
            )
            self.graph.link("REPO_HAS_ISSUE", "Repository", repository_id, "Issue", source_id)
            total += 1
            chunks += result["chunks_created"] if result else 0
        return total, chunks

    def _ingest_pull_requests(
        self,
        slug: str,
        project_id: str,
        repository_id: str,
        ingest_if_changed: Callable[..., tuple[dict[str, Any] | None, str]],
    ) -> tuple[int, int]:
        total = chunks = 0
        for pull in self.github.list_pull_requests(slug):
            source_id = f"pull:{slug}:{pull['number']}"
            title = f"Pull request #{pull['number']}: {pull['title']}"
            changed_files = self.github.pull_request_files(slug, int(pull["number"]))
            file_names = [
                item.get("filename", "") for item in changed_files if item.get("filename")
            ]
            head_sha = (pull.get("head") or {}).get("sha", "")
            conversation = self.github.issue_comments(slug, int(pull["number"]))
            review_comments = self.github.pull_request_review_comments(slug, int(pull["number"]))
            reviews = self.github.pull_request_reviews(slug, int(pull["number"]))
            thread_entries = [
                *(
                    (
                        (comment.get("user") or {}).get("login") or "unknown",
                        comment.get("created_at") or "",
                        comment.get("body") or "",
                        comment.get("html_url") or "",
                    )
                    for comment in conversation
                ),
                *(
                    (
                        (comment.get("user") or {}).get("login") or "unknown",
                        comment.get("created_at") or "",
                        comment.get("body") or "",
                        comment.get("html_url") or "",
                    )
                    for comment in review_comments
                ),
                *(
                    (
                        (review.get("user") or {}).get("login") or "unknown",
                        review.get("submitted_at") or "",
                        f"Review state: {review.get('state') or ''}\n{review.get('body') or ''}",
                        review.get("html_url") or "",
                    )
                    for review in reviews
                ),
            ]
            thread = "\n\n".join(
                f"Review/comment by {author} at {created}\n{body}\nLink: {url}"
                for author, created, body, url in thread_entries
            )
            content = (
                f"{title}\nState: {pull.get('state')}\nCommit: {head_sha}\n"
                f"Author: {(pull.get('user') or {}).get('login') or 'unknown'}\n"
                f"Changed files: {', '.join(file_names)}\n\n{pull.get('body') or ''}"
                f"\n\nReview discussion\n{thread}"
            )
            result, _ = ingest_if_changed(
                "pull_request",
                title,
                content,
                pull["html_url"],
                source_id,
                {
                    "number": pull["number"],
                    "repository": slug,
                    "commit_sha": head_sha,
                    "changed_files": file_names,
                    "source_updated_at": pull.get("updated_at", ""),
                    "owner": (pull.get("user") or {}).get("login") or "",
                    "owner_source": "pull_request_author",
                    "comment_count": len(thread_entries),
                },
            )
            self.graph.link(
                "REPO_HAS_PULL_REQUEST", "Repository", repository_id, "PullRequest", source_id
            )
            for filename in file_names:
                self.graph.link(
                    "PR_TOUCHES_FILE",
                    "PullRequest",
                    source_id,
                    "File",
                    f"file:{project_id}:{filename}",
                )
            total += 1
            chunks += result["chunks_created"] if result else 0
        return total, chunks

    @staticmethod
    def _git_value(root: Path, *args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    @staticmethod
    def _codeowners(root: Path) -> list[tuple[str, list[str]]]:
        for candidate in (
            root / ".github" / "CODEOWNERS",
            root / "CODEOWNERS",
            root / "docs" / "CODEOWNERS",
        ):
            if not candidate.is_file():
                continue
            rules: list[tuple[str, list[str]]] = []
            for raw_line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                owners = [value.lstrip("@") for value in parts[1:] if value.startswith("@")]
                if owners:
                    rules.append((parts[0].lstrip("/"), owners))
            return rules
        return []

    def _file_owner(
        self, root: Path, relative: str, codeowners: list[tuple[str, list[str]]]
    ) -> tuple[str, str]:
        matched: list[str] = []
        for pattern, owners in codeowners:
            directory_rule = pattern.endswith("/")
            normalized = pattern.rstrip("/") + ("/*" if directory_rule else "")
            if fnmatch.fnmatch(relative, normalized) or fnmatch.fnmatch(
                relative, f"**/{normalized}"
            ):
                matched = owners
        if matched:
            return ", ".join(matched), "CODEOWNERS"
        author = self._git_value(root, "log", "-1", "--format=%an <%ae>", "--", relative)
        return (author, "last_committer") if author else ("", "unresolved")

    @staticmethod
    def _file_url(source: str, relative: str, commit_sha: str = "") -> str:
        slug = GitHubConnector.slug(source)
        return (
            f"https://github.com/{slug}/blob/{commit_sha or 'HEAD'}/{relative}"
            if slug
            else f"file://{Path(source).expanduser().resolve() / relative}"
        )
