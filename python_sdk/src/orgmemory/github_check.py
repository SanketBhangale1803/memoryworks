"""Serve a MemoryWorks briefing for a GitHub pull request and post it as a Check.

This runs on the customer's CI runner (see ``deploy/github-action``). It does not
add a second briefing API: it summarizes the pull request into the existing
``POST /api/briefings`` request with ``surface="github_check"``, then reports
the verdict back to GitHub as one Check run and one sticky PR comment.

Rules this module keeps:

* A briefing is advice. ``requires_approval`` is reported as a check that needs a
  person; nothing here approves or merges anything.
* ``no_memory`` is only reported as "no applicable memory" when the server says
  retrieval was healthy. Until the API reports retrieval health, an empty
  briefing could be a dead index, so the check fails as "briefing unavailable".
* Re-running for the same head SHA and the same request reuses the existing
  check instead of opening another ledger row; a changed request updates the
  same check. The PR comment is edited in place, never duplicated.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from .client import MemoryWorks
from .exceptions import MemoryWorksError

CHECK_NAME = "MemoryWorks briefing"
COMMENT_MARKER = "<!-- memoryworks-briefing -->"
EXTERNAL_ID_PREFIX = "memoryworks"
SURFACE = "github_check"
TASK_LIMIT = 2_000  # BriefingRequest.task max_length
BODY_LIMIT = 600
SUMMARY_LIMIT = 60_000  # GitHub caps check output.summary at 65,535 characters
MAX_FILE_PAGES = 30  # GitHub returns at most 3,000 files for a pull request

# failure_state values that positively state retrieval worked. Anything else —
# including the field being absent, as it is today — is not proof of health.
HEALTHY_FAILURE_STATES = frozenset({"none", "healthy"})

CITED_GROUPS = (
    ("must_read", "Read first"),
    ("constraints", "Constraint"),
    ("prior_incidents", "Prior incident"),
    ("blast_radius", "Blast radius"),
    ("procedures", "Procedure"),
)

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_SPACE_RE = re.compile(r"\s+")


class GitHubCheckError(RuntimeError):
    """The check could not be posted to GitHub."""


@dataclass(frozen=True)
class PullRequest:
    repository: str
    number: int
    head_sha: str
    title: str
    body: str = ""
    base_ref: str = ""
    html_url: str = ""
    changed_files: int = 0
    additions: int = 0
    deletions: int = 0

    @classmethod
    def from_event(cls, event: Mapping[str, Any], repository: str = "") -> PullRequest:
        pull = event.get("pull_request")
        if not isinstance(pull, Mapping):
            raise ValueError(
                "The GitHub event has no pull_request; run this on pull_request events"
            )
        repo = repository or str((event.get("repository") or {}).get("full_name") or "")
        if not repo:
            raise ValueError("Repository is unknown; set GITHUB_REPOSITORY")
        return cls(
            repository=repo,
            number=int(pull.get("number") or event.get("number") or 0),
            head_sha=str((pull.get("head") or {}).get("sha") or ""),
            title=str(pull.get("title") or ""),
            body=str(pull.get("body") or ""),
            base_ref=str((pull.get("base") or {}).get("ref") or ""),
            html_url=str(pull.get("html_url") or ""),
            changed_files=int(pull.get("changed_files") or 0),
            additions=int(pull.get("additions") or 0),
            deletions=int(pull.get("deletions") or 0),
        )


@dataclass(frozen=True)
class Assessment:
    """How a briefing (or its absence) is reported on the check."""

    state: str  # unavailable | requires_approval | no_memory | proceed_with_context | proceed
    conclusion: str  # GitHub check conclusion
    title: str


@dataclass
class CheckResult:
    state: str
    conclusion: str
    briefing_id: str
    check_run_id: int | None
    comment_id: int | None
    reused: bool
    task: str
    summary: str = ""
    error: str = ""
    briefing: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "conclusion": self.conclusion,
            "briefing_id": self.briefing_id,
            "check_run_id": self.check_run_id,
            "comment_id": self.comment_id,
            "reused": self.reused,
            "task": self.task,
            "error": self.error,
        }


# --------------------------------------------------------------------------- task


def _clean(text: str) -> str:
    return _SPACE_RE.sub(" ", _HTML_COMMENT_RE.sub(" ", text)).strip()


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def summarize_task(
    pull: PullRequest, files: list[Mapping[str, Any]] | None = None
) -> str:
    """Describe the pull request as a briefing intent of at most 2,000 characters.

    The wording is deliberate: it avoids "merge", "push", and "repository <name>"
    so the engine's verb and service detection react to what the author wrote,
    not to boilerplate every pull request would share. A large diff is reduced to
    counts, top-level directories, and as many paths as fit.
    """
    files = list(files or [])
    head = f"Pull request #{pull.number} in {pull.repository}"
    if pull.base_ref:
        head += f" targeting {pull.base_ref}"
    title = _clip(_clean(pull.title), 300)
    parts = [f"{head}: {title}" if title else head]

    body = _clip(_clean(pull.body), BODY_LIMIT)
    if body:
        parts.append(f"Description: {body}")

    count = len(files) or pull.changed_files
    additions = (
        sum(int(f.get("additions") or 0) for f in files) if files else pull.additions
    )
    deletions = (
        sum(int(f.get("deletions") or 0) for f in files) if files else pull.deletions
    )
    if count:
        line = f"Changes {count} file(s), +{additions}/-{deletions}"
        top = sorted(
            {str(f.get("filename") or "").split("/", 1)[0] for f in files} - {""}
        )
        if top:
            line += " under " + ", ".join(top[:12])
        parts.append(line + ".")

    task = " ".join(parts)
    paths = [str(f.get("filename") or "") for f in files if f.get("filename")]
    if paths:
        prefix = " Files: "
        room = TASK_LIMIT - len(task) - len(prefix)
        shown: list[str] = []
        used = 0
        for path in paths:
            cost = len(path) + (2 if shown else 0)
            if used + cost > room - 24:  # keep space for the "and N more" tail
                break
            shown.append(path)
            used += cost
        if shown:
            tail = (
                f" and {len(paths) - len(shown)} more"
                if len(shown) < len(paths)
                else ""
            )
            task += prefix + ", ".join(shown) + tail + "."
    return _clip(task, TASK_LIMIT)


def fingerprint(task: str, service: str, project_id: str) -> str:
    payload = json.dumps(
        {"task": task, "service": service, "project_id": project_id}, sort_keys=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# --------------------------------------------------------------------- assessment


def assess(
    briefing: Mapping[str, Any] | None,
    *,
    error: str = "",
    approval_conclusion: str = "action_required",
) -> Assessment:
    if error or not briefing:
        return Assessment("unavailable", "failure", "Briefing unavailable")
    verdict = str(briefing.get("verdict") or "")
    failure_state = str(briefing.get("failure_state") or "").casefold()
    if failure_state and failure_state not in HEALTHY_FAILURE_STATES | {"partial"}:
        # The server says retrieval failed; whatever verdict came back is not
        # a statement about company memory.
        return Assessment(
            "unavailable", "failure", "Briefing unavailable: retrieval failed"
        )
    if verdict == "requires_approval":
        return Assessment(
            "requires_approval",
            approval_conclusion,
            "Requires a person's decision — MemoryWorks does not approve it",
        )
    if verdict == "no_memory":
        if failure_state in HEALTHY_FAILURE_STATES:
            return Assessment(
                "no_memory",
                "neutral",
                "No applicable company memory — this is not permission",
            )
        # Today the API cannot tell an empty memory from a failed index. Saying
        # "company memory holds nothing" when the index is down would be a
        # confident wrong answer, so the check fails instead.
        return Assessment(
            "unavailable",
            "failure",
            "Briefing unavailable: empty result could not be told apart from a retrieval failure",
        )
    if verdict == "proceed_with_context":
        return Assessment(
            "proceed_with_context", "success", "Proceed with the cited context"
        )
    if verdict == "proceed":
        return Assessment(
            "proceed", "success", "Proceed — relevant memory found, no constraint hit"
        )
    return Assessment(
        "unavailable", "failure", f"Briefing unavailable: unknown verdict {verdict!r}"
    )


# ------------------------------------------------------------------------ render


def _memory_link(app_url: str, memory_id: str) -> str:
    # Opens the permission-trimmed memory for whoever is signed in; a reader
    # without access gets 404, not the record.
    return f"{app_url.rstrip('/')}/api/memory/units/{memory_id}"


def _citations(briefing: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    cited: list[tuple[str, Mapping[str, Any]]] = []
    for key, label in CITED_GROUPS:
        for item in briefing.get(key) or []:
            if isinstance(item, Mapping) and item.get("memory_id"):
                cited.append((label, item))
    return cited


def _no_service_note(briefing: Mapping[str, Any]) -> str:
    if briefing.get("service"):
        return ""
    return (
        "No service was named, so service-scoped constraints and incident history were "
        "not pulled. Set the action's `service` input to get them."
    )


def render_markdown(
    *,
    assessment: Assessment,
    briefing: Mapping[str, Any] | None,
    pull: PullRequest,
    app_url: str,
    error: str = "",
    show_memory_text: bool = False,
    short: bool = False,
) -> str:
    lines: list[str] = []
    if short:
        lines.append(COMMENT_MARKER)
    lines.append(f"### {CHECK_NAME}: `{assessment.state}`")
    lines.append(f"**{assessment.title}**")
    lines.append("")
    if assessment.state == "unavailable":
        reason = error or (
            "The briefing came back empty and the API did not confirm that retrieval was "
            'healthy, so this is not reported as "company memory holds nothing".'
        )
        lines.append(f"MemoryWorks could not serve a usable briefing: {reason}")
        lines.append("")
        lines.append(
            "Treat constraints for this change as unknown until the briefing is served."
        )
    if briefing:
        lines.append(f"> {briefing.get('headline') or ''}")
        lines.append("")
        lines.append(
            f"- verdict: `{briefing.get('verdict')}`\n"
            f"- briefing_id: `{briefing.get('briefing_id') or 'not recorded'}`\n"
            f"- service: `{briefing.get('service') or 'not named'}`\n"
            f"- head: `{pull.head_sha[:12]}`"
        )
        if briefing.get("failure_state"):
            lines.append(f"- retrieval: `{briefing.get('failure_state')}`")
        note = _no_service_note(briefing)
        if note:
            lines.append("")
            lines.append(f"⚠️ {note}")
        reasons = [str(r) for r in briefing.get("requires_approval") or []]
        if reasons:
            lines.append("")
            lines.append("**Why a person has to decide**")
            lines.extend(f"- {reason}" for reason in reasons)
        cited = _citations(briefing)
        if cited:
            lines.append("")
            lines.append("**Cited memory**")
            limit = 10 if short else len(cited)
            for label, item in cited[:limit]:
                memory_id = str(item.get("memory_id"))
                entry = (
                    f"- {label}: [`{memory_id}`]({_memory_link(app_url, memory_id)})"
                    f" ({item.get('type') or 'memory'})"
                )
                if show_memory_text:
                    entry += f" — {_clip(str(item.get('subject') or ''), 160)}"
                    if not short:
                        entry += f"; {item.get('why_it_matters') or ''}"
                lines.append(entry)
            if len(cited) > limit:
                lines.append(f"- …and {len(cited) - limit} more in the check details")
        questions = [str(q) for q in briefing.get("open_questions") or []]
        if questions and not short:
            lines.append("")
            lines.append("**Open questions**")
            lines.extend(f"- {question}" for question in questions)
    lines.append("")
    lines.append(
        "_A briefing is advice, not authorization. MemoryWorks never approves or merges a "
        "change; a person decides._"
    )
    if briefing and briefing.get("briefing_id") and not short:
        lines.append("")
        lines.append(
            "Report what happened afterwards with "
            f"`orgmemory outcome {briefing.get('briefing_id')} <action> --outcome <label>`."
        )
    return _clip("\n".join(lines), SUMMARY_LIMIT)


# ------------------------------------------------------------------------ GitHub


class GitHubAPI:
    """The handful of GitHub REST calls the check needs."""

    def __init__(
        self,
        token: str,
        *,
        api_url: str = "https://api.github.com",
        transport: httpx.BaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        if not token:
            raise GitHubCheckError("GITHUB_TOKEN is not set")
        self._client = httpx.Client(
            base_url=api_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "orgmemory-github-check/0.1.0",
            },
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._client.request(method, path, **kwargs)
        if response.is_error:
            # GitHub error bodies carry a message, never the token.
            try:
                message = response.json().get("message")
            except ValueError:
                message = response.reason_phrase
            raise GitHubCheckError(
                f"GitHub {method} {path} → {response.status_code}: {message}"
            )
        return response.json() if response.content else {}

    def pull_files(self, repository: str, number: int) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        for page in range(1, MAX_FILE_PAGES + 1):
            batch = self._call(
                "GET",
                f"/repos/{repository}/pulls/{number}/files",
                params={"per_page": 100, "page": page},
            )
            files.extend(batch)
            if len(batch) < 100:
                break
        return files

    def find_check_run(self, repository: str, sha: str) -> dict[str, Any] | None:
        found = self._call(
            "GET",
            f"/repos/{repository}/commits/{sha}/check-runs",
            params={"check_name": CHECK_NAME, "per_page": 100},
        )
        for run in found.get("check_runs") or []:
            if str(run.get("external_id") or "").startswith(EXTERNAL_ID_PREFIX + ":"):
                return run
        return None

    def create_check_run(
        self, repository: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return self._call("POST", f"/repos/{repository}/check-runs", json=payload)

    def update_check_run(
        self, repository: str, check_run_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return self._call(
            "PATCH", f"/repos/{repository}/check-runs/{check_run_id}", json=payload
        )

    def upsert_comment(self, repository: str, number: int, body: str) -> int:
        existing = None
        for page in range(1, 11):
            batch = self._call(
                "GET",
                f"/repos/{repository}/issues/{number}/comments",
                params={"per_page": 100, "page": page},
            )
            existing = next(
                (c for c in batch if COMMENT_MARKER in str(c.get("body") or "")), None
            )
            if existing or len(batch) < 100:
                break
        if existing:
            updated = self._call(
                "PATCH",
                f"/repos/{repository}/issues/comments/{existing['id']}",
                json={"body": body},
            )
            return int(updated.get("id") or existing["id"])
        created = self._call(
            "POST", f"/repos/{repository}/issues/{number}/comments", json={"body": body}
        )
        return int(created["id"])


def _external_id(fp: str, state: str, briefing_id: str) -> str:
    return f"{EXTERNAL_ID_PREFIX}:{fp}:{state}:{briefing_id or '-'}"


def _parse_external_id(value: str) -> tuple[str, str, str]:
    parts = value.split(":")
    if len(parts) != 4 or parts[0] != EXTERNAL_ID_PREFIX:
        return "", "", ""
    return parts[1], parts[2], "" if parts[3] == "-" else parts[3]


# --------------------------------------------------------------------------- run


def run(
    *,
    event: Mapping[str, Any],
    github: GitHubAPI | None,
    memoryworks: MemoryWorks,
    repository: str = "",
    service: str = "",
    project_id: str = "",
    app_url: str = "https://memoryworks.app",
    approval_conclusion: str = "action_required",
    show_memory_text: bool = False,
    comment: bool = True,
    rebrief: bool = False,
) -> CheckResult:
    """Serve one briefing for the pull request in ``event`` and report it.

    With ``github=None`` nothing is posted (dry run); the rendered summary is
    returned for printing.
    """
    pull = PullRequest.from_event(event, repository)
    if not pull.head_sha:
        raise ValueError("The pull_request event has no head SHA")

    files: list[dict[str, Any]] = []
    if github is not None:
        try:
            files = github.pull_files(pull.repository, pull.number)
        except GitHubCheckError:
            files = []  # fall back to the counts in the event payload
    task = summarize_task(pull, files)
    fp = fingerprint(task, service, project_id)

    existing = github.find_check_run(pull.repository, pull.head_sha) if github else None
    if existing and not rebrief:
        prior_fp, prior_state, prior_briefing = _parse_external_id(
            str(existing.get("external_id") or "")
        )
        if prior_fp == fp and prior_state and prior_state != "unavailable":
            # Same commit, same request: the briefing on this check already
            # answers it. Serving another would open a second ledger row for
            # an identical intent.
            return CheckResult(
                state=prior_state,
                conclusion=str(existing.get("conclusion") or ""),
                briefing_id=prior_briefing,
                check_run_id=int(existing["id"]),
                comment_id=None,
                reused=True,
                task=task,
            )

    briefing: dict[str, Any] | None = None
    error = ""
    try:
        served = memoryworks.get_briefing(
            task, service=service, project_id=project_id, surface=SURFACE
        )
        briefing = dict(served.raw)
    except (MemoryWorksError, httpx.HTTPError) as exc:
        error = str(exc) or exc.__class__.__name__

    assessment = assess(briefing, error=error, approval_conclusion=approval_conclusion)
    briefing_id = str((briefing or {}).get("briefing_id") or "")
    summary = render_markdown(
        assessment=assessment,
        briefing=briefing,
        pull=pull,
        app_url=app_url,
        error=error,
        show_memory_text=show_memory_text,
    )
    result = CheckResult(
        state=assessment.state,
        conclusion=assessment.conclusion,
        briefing_id=briefing_id,
        check_run_id=None,
        comment_id=None,
        reused=False,
        task=task,
        summary=summary,
        error=error,
        briefing=briefing or {},
    )
    if github is None:
        return result

    payload = {
        "name": CHECK_NAME,
        "head_sha": pull.head_sha,
        "status": "completed",
        "conclusion": assessment.conclusion,
        "external_id": _external_id(fp, assessment.state, briefing_id),
        # action_required needs somewhere to go; the pull request is where a
        # person records the decision.
        "details_url": pull.html_url or app_url,
        "output": {
            "title": _clip(assessment.title, 200),
            "summary": summary,
            "text": _clip(f"Briefing request (task):\n\n{task}", SUMMARY_LIMIT),
        },
    }
    if existing:
        check = github.update_check_run(pull.repository, int(existing["id"]), payload)
    else:
        check = github.create_check_run(pull.repository, payload)
    result.check_run_id = (
        int(check.get("id") or (existing or {}).get("id") or 0) or None
    )

    if comment and pull.number:
        body = render_markdown(
            assessment=assessment,
            briefing=briefing,
            pull=pull,
            app_url=app_url,
            error=error,
            show_memory_text=show_memory_text,
            short=True,
        )
        result.comment_id = github.upsert_comment(pull.repository, pull.number, body)
    return result
