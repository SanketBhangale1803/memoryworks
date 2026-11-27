"""Automatic briefings: CLI brief/outcome, the GitHub check, and editor hooks."""

from __future__ import annotations

import io
import json
from typing import Any

import httpx
import pytest

from orgmemory import cli, github_check, hooks
from orgmemory.client import MemoryWorks


def _briefing(**overrides: Any) -> dict[str, Any]:
    value = {
        "task": "t",
        "verdict": "proceed_with_context",
        "headline": "Read the constraints below.",
        "briefing_id": "ctx_1",
        "service": "payments",
        "project_id": "prj_1",
        "consequential_action": None,
        "must_read": [
            {
                "memory_id": "mem_1",
                "type": "decision",
                "subject": "Payments retries are capped",
                "content": "…",
                "why_it_matters": "Directly matches what you are about to do",
            }
        ],
        "constraints": [],
        "prior_incidents": [],
        "blast_radius": [],
        "procedures": [],
        "precedents": [],
        "requires_approval": [],
        "safe_actions": [],
        "open_questions": [],
        "memory_count": 1,
    }
    value.update(overrides)
    return value


class FakeMemoryWorks:
    """A MockTransport-backed API that records every briefing request."""

    def __init__(
        self, briefing: dict[str, Any] | None = None, status: int = 200
    ) -> None:
        self.briefing = briefing or _briefing()
        self.status = status
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.requests.append((request.url.path, body))
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": "index unavailable"})
        if request.url.path == "/api/briefings":
            return httpx.Response(200, json=self.briefing)
        if request.url.path == "/api/briefings/outcome":
            return httpx.Response(
                200,
                json={
                    "briefing_id": body["briefing_id"],
                    "action": {"id": "act_1", "action_type": body["action"]},
                    "outcome": {
                        "id": "out_1",
                        "outcome": body["outcome"],
                        "reward": 1.0,
                    },
                    "recorded": True,
                },
            )
        return httpx.Response(404, json={"detail": "not found"})

    def client(self) -> MemoryWorks:
        return MemoryWorks(
            base_url="http://mw.test",
            api_key="k",
            transport=httpx.MockTransport(self.handler),
        )

    @property
    def briefings_served(self) -> int:
        return sum(1 for path, _ in self.requests if path == "/api/briefings")


@pytest.fixture
def fake_api(monkeypatch) -> FakeMemoryWorks:
    fake = FakeMemoryWorks()
    monkeypatch.setattr(cli, "MemoryWorks", lambda **_: fake.client())
    return fake


# ------------------------------------------------------------------------ CLI


def test_brief_wraps_get_briefing_and_prints_the_verdict(fake_api, capsys) -> None:
    code = cli.main(
        ["brief", "raise the payments worker limit", "--service", "payments"]
    )

    assert code == 0
    path, body = fake_api.requests[0]
    assert path == "/api/briefings"
    assert body == {
        "task": "raise the payments worker limit",
        "service": "payments",
        "project_id": "",
        "surface": "cli",
    }
    out = capsys.readouterr().out
    assert "verdict: proceed_with_context" in out
    assert "briefing_id: ctx_1" in out
    assert "[mem_1]" in out
    assert "advice, not authorization" in out


def test_brief_json_returns_the_raw_briefing(fake_api, capsys) -> None:
    assert cli.main(["brief", "rotate the billing key", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["briefing_id"] == "ctx_1"


def test_outcome_wraps_record_briefing_outcome(fake_api, capsys) -> None:
    code = cli.main(
        [
            "outcome",
            "ctx_1",
            "edit_applied",
            "--outcome",
            "succeeded",
            "--reason",
            "local tests passed",
            "--detail",
            '{"verification_scope": "local"}',
        ]
    )

    assert code == 0
    path, body = fake_api.requests[0]
    assert path == "/api/briefings/outcome"
    assert body["outcome"] == "succeeded"
    assert body["surface"] == "cli"
    assert body["detail"] == {"verification_scope": "local"}
    assert "recorded succeeded for ctx_1" in capsys.readouterr().out


def test_outcome_rejects_labels_outside_the_closed_vocabulary(fake_api) -> None:
    with pytest.raises(SystemExit):
        cli.main(["outcome", "ctx_1", "edit_applied", "--outcome", "great"])
    assert fake_api.requests == []


def test_outcome_rejects_non_object_detail(fake_api, capsys) -> None:
    code = cli.main(
        ["outcome", "ctx_1", "x_y", "--outcome", "failed", "--detail", "[1]"]
    )
    assert code == 1
    assert fake_api.requests == []


# --------------------------------------------------------------- GitHub check


def _event(
    sha: str = "a" * 40, title: str = "Raise payments worker pool", body: str = ""
) -> dict:
    return {
        "action": "opened",
        "number": 7,
        "repository": {"full_name": "acme/shop"},
        "pull_request": {
            "number": 7,
            "title": title,
            "body": body,
            "html_url": "https://github.com/acme/shop/pull/7",
            "head": {"sha": sha},
            "base": {"ref": "main"},
            "changed_files": 2,
            "additions": 10,
            "deletions": 3,
        },
    }


class FakeGitHub:
    def __init__(self, files: list[dict] | None = None) -> None:
        self.files = (
            files
            if files is not None
            else [
                {"filename": "payments/worker.py", "additions": 8, "deletions": 2},
                {"filename": "payments/config.yaml", "additions": 2, "deletions": 1},
            ]
        )
        self.check_runs: dict[int, dict] = {}
        self.comments: dict[int, dict] = {}
        self.calls: list[tuple[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        assert request.headers["Authorization"] == "Bearer ghs_test"
        if path.endswith("/pulls/7/files"):
            return httpx.Response(200, json=self.files)
        if "/commits/" in path and path.endswith("/check-runs"):
            sha = path.split("/commits/")[1].split("/")[0]
            runs = [r for r in self.check_runs.values() if r["head_sha"] == sha]
            return httpx.Response(
                200, json={"total_count": len(runs), "check_runs": runs}
            )
        if path.endswith("/check-runs") and request.method == "POST":
            run = {**json.loads(request.content), "id": len(self.check_runs) + 1}
            self.check_runs[run["id"]] = run
            return httpx.Response(201, json=run)
        if "/check-runs/" in path and request.method == "PATCH":
            run_id = int(path.rsplit("/", 1)[1])
            self.check_runs[run_id].update(json.loads(request.content))
            return httpx.Response(200, json=self.check_runs[run_id])
        if path.endswith("/issues/7/comments") and request.method == "GET":
            return httpx.Response(200, json=list(self.comments.values()))
        if path.endswith("/issues/7/comments") and request.method == "POST":
            comment = {**json.loads(request.content), "id": 100 + len(self.comments)}
            self.comments[comment["id"]] = comment
            return httpx.Response(201, json=comment)
        if "/issues/comments/" in path and request.method == "PATCH":
            comment_id = int(path.rsplit("/", 1)[1])
            self.comments[comment_id].update(json.loads(request.content))
            return httpx.Response(200, json=self.comments[comment_id])
        return httpx.Response(404, json={"message": "Not Found"})

    def api(self) -> github_check.GitHubAPI:
        return github_check.GitHubAPI(
            "ghs_test", transport=httpx.MockTransport(self.handler)
        )


def _run(
    fake: FakeMemoryWorks, gh: FakeGitHub, event: dict | None = None, **kwargs: Any
):
    return github_check.run(
        event=event or _event(), github=gh.api(), memoryworks=fake.client(), **kwargs
    )


def test_check_serves_one_briefing_through_the_existing_api_and_posts_it() -> None:
    fake, gh = FakeMemoryWorks(), FakeGitHub()

    result = _run(fake, gh, service="payments")

    path, body = fake.requests[0]
    assert path == "/api/briefings"
    assert body["surface"] == "github_check"
    assert body["service"] == "payments"
    assert "payments/worker.py" in body["task"]
    assert result.conclusion == "success"
    (run,) = gh.check_runs.values()
    assert run["name"] == "MemoryWorks briefing"
    assert run["head_sha"] == "a" * 40
    assert "verdict: `proceed_with_context`" in run["output"]["summary"]
    assert "briefing_id: `ctx_1`" in run["output"]["summary"]
    assert "/api/memory/units/mem_1" in run["output"]["summary"]
    (comment,) = gh.comments.values()
    assert comment["body"].startswith(github_check.COMMENT_MARKER)
    assert "ctx_1" in comment["body"]


def test_rerunning_the_same_sha_reuses_the_check_without_a_second_ledger_row() -> None:
    fake, gh = FakeMemoryWorks(), FakeGitHub()

    first = _run(fake, gh)
    second = _run(fake, gh)

    assert fake.briefings_served == 1
    assert second.reused is True
    assert second.briefing_id == first.briefing_id == "ctx_1"
    assert len(gh.check_runs) == 1
    assert len(gh.comments) == 1


def test_an_edited_pull_request_updates_the_same_check_and_comment() -> None:
    fake, gh = FakeMemoryWorks(), FakeGitHub()

    _run(fake, gh)
    _run(fake, gh, event=_event(title="Raise payments worker pool to 64"))

    assert fake.briefings_served == 2
    assert len(gh.check_runs) == 1
    assert ("PATCH", "/repos/acme/shop/check-runs/1") in gh.calls
    assert len(gh.comments) == 1


def test_a_new_commit_gets_its_own_check_but_keeps_one_comment() -> None:
    fake, gh = FakeMemoryWorks(), FakeGitHub()

    _run(fake, gh)
    _run(fake, gh, event=_event(sha="b" * 40))

    assert len(gh.check_runs) == 2
    assert len(gh.comments) == 1


def test_an_unavailable_check_is_retried_on_rerun() -> None:
    down, gh = FakeMemoryWorks(status=503), FakeGitHub()
    first = _run(down, gh)
    assert first.state == "unavailable"
    assert first.conclusion == "failure"

    up = FakeMemoryWorks()
    second = _run(up, gh)

    assert second.reused is False
    assert up.briefings_served == 1
    assert len(gh.check_runs) == 1
    assert gh.check_runs[1]["conclusion"] == "success"


def test_no_memory_without_retrieval_health_fails_as_briefing_unavailable() -> None:
    fake = FakeMemoryWorks(_briefing(verdict="no_memory", must_read=[], memory_count=0))
    gh = FakeGitHub()

    result = _run(fake, gh)

    assert result.state == "unavailable"
    assert result.conclusion == "failure"
    summary = gh.check_runs[1]["output"]["summary"]
    assert "Briefing unavailable" in summary
    assert (
        "holds nothing" in summary
    )  # only inside the explicit "not reported as" sentence
    assert 'not reported as "company memory holds nothing"' in summary


def test_no_memory_with_healthy_retrieval_is_neutral_not_permission() -> None:
    assessment = github_check.assess(
        _briefing(verdict="no_memory", failure_state="none"),
    )
    assert assessment.state == "no_memory"
    assert assessment.conclusion == "neutral"
    assert "not permission" in assessment.title


def test_a_reported_retrieval_failure_is_unavailable_whatever_the_verdict() -> None:
    assessment = github_check.assess(
        _briefing(verdict="proceed", failure_state="retrieval_failed")
    )
    assert assessment.state == "unavailable"


def test_requires_approval_needs_a_person_and_never_succeeds() -> None:
    briefing = _briefing(
        verdict="requires_approval",
        requires_approval=[
            "This request involves raising a limit. A person has to agree."
        ],
    )
    assert github_check.assess(briefing).conclusion == "action_required"
    assert (
        github_check.assess(briefing, approval_conclusion="neutral").conclusion
        == "neutral"
    )

    fake, gh = FakeMemoryWorks(briefing), FakeGitHub()
    _run(fake, gh)
    summary = gh.check_runs[1]["output"]["summary"]
    assert "Why a person has to decide" in summary
    assert "raising a limit" in summary
    assert "does not approve" in gh.check_runs[1]["output"]["title"]


def test_unnamed_service_is_called_out_not_invented() -> None:
    fake = FakeMemoryWorks(_briefing(service=None))
    gh = FakeGitHub()

    _run(fake, gh)

    assert fake.requests[0][1]["service"] == ""
    summary = gh.check_runs[1]["output"]["summary"]
    assert "No service was named" in summary
    assert "service: `not named`" in summary


def test_memory_text_stays_out_of_the_pr_unless_asked_for() -> None:
    gh = FakeGitHub()
    _run(FakeMemoryWorks(), gh)
    assert "Payments retries are capped" not in gh.check_runs[1]["output"]["summary"]

    gh = FakeGitHub()
    _run(FakeMemoryWorks(), gh, show_memory_text=True)
    assert "Payments retries are capped" in gh.check_runs[1]["output"]["summary"]


def test_a_huge_pull_request_is_summarized_under_the_task_limit() -> None:
    files = [
        {
            "filename": f"svc{i % 7}/module_{i}/file_{i}.py",
            "additions": 5,
            "deletions": 1,
        }
        for i in range(3000)
    ]
    pull = github_check.PullRequest.from_event(
        _event(body="<!-- template -->" + "x" * 50_000)
    )

    task = github_check.summarize_task(pull, files)

    assert len(task) <= github_check.TASK_LIMIT
    assert "Changes 3000 file(s), +15000/-3000" in task
    assert "more." in task
    assert "template" not in task


def test_task_wording_does_not_trigger_verbs_or_service_inference_by_itself() -> None:
    pull = github_check.PullRequest.from_event(_event(title="Tidy README"))
    task = github_check.summarize_task(pull, [{"filename": "README.md"}]).casefold()
    assert "merge" not in task
    assert "push" not in task
    assert "repository " not in task
    assert "repo " not in task


def test_cli_github_check_dry_run_prints_without_calling_github(
    fake_api, tmp_path, capsys, monkeypatch
) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(_event()), encoding="utf-8")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    code = cli.main(["github-check", "--event-path", str(event_path), "--dry-run"])

    assert code == 0
    assert fake_api.requests[0][1]["surface"] == "github_check"
    assert "briefing_id: `ctx_1`" in capsys.readouterr().out


def test_cli_github_check_without_a_token_fails_clearly(
    fake_api, tmp_path, monkeypatch
) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(_event()), encoding="utf-8")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    assert cli.main(["github-check", "--event-path", str(event_path)]) == 1
    assert fake_api.requests == []


# ---------------------------------------------------------------------- hooks


@pytest.fixture(autouse=True)
def isolated_hook_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MEMORYWORKS_HOOK_STATE_DIR", str(tmp_path))


def _bash(command: str, session: str = "s1") -> dict:
    return {
        "session_id": session,
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": "/work/shop",
    }


def test_hook_ignores_read_only_commands_and_reads() -> None:
    fake = FakeMemoryWorks()
    assert (
        hooks.claude_code(_bash("ls -la && git status"), client=fake.client()) is None
    )
    read = {"session_id": "s1", "tool_name": "Read", "tool_input": {"file_path": "/x"}}
    assert hooks.claude_code(read, client=fake.client()) is None
    assert fake.requests == []


def test_hook_briefs_a_consequential_command_and_asks_for_the_outcome() -> None:
    fake = FakeMemoryWorks()

    response = hooks.claude_code(_bash("git push origin main"), client=fake.client())

    assert fake.requests[0][1]["surface"] == "claude_code"
    assert "git push origin main" in fake.requests[0][1]["task"]
    output = response["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in output  # advice only: normal permissions apply
    assert "briefing_id=ctx_1" in output["additionalContext"]
    assert 'record_orgmemory_outcome(briefing_id="ctx_1"' in output["additionalContext"]


def test_hook_escalates_requires_approval_to_the_person_and_never_allows() -> None:
    fake = FakeMemoryWorks(
        _briefing(verdict="requires_approval", requires_approval=["r"])
    )

    first = hooks.claude_code(
        _bash("kubectl scale deploy/payments --replicas=0"), client=fake.client()
    )
    again = hooks.claude_code(
        _bash("kubectl scale deploy/payments --replicas=0"), client=fake.client()
    )

    assert first["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "ctx_1" in first["hookSpecificOutput"]["permissionDecisionReason"]
    assert again["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert fake.briefings_served == 1  # one ledger row per step per session


def test_hook_briefs_each_edited_file_once_per_session() -> None:
    fake = FakeMemoryWorks()
    edit = {
        "session_id": "s2",
        "tool_name": "Edit",
        "tool_input": {"file_path": "/work/shop/payments/worker.py"},
        "cwd": "/work/shop",
    }

    assert hooks.claude_code(edit, client=fake.client()) is not None
    assert hooks.claude_code(edit, client=fake.client()) is None
    assert fake.requests[0][1]["task"] == "Edit payments/worker.py in shop"
    assert fake.briefings_served == 1


def test_hook_reports_an_unavailable_briefing_instead_of_silence() -> None:
    fake = FakeMemoryWorks(status=503)

    response = hooks.claude_code(_bash("terraform apply"), client=fake.client())

    context = response["hookSpecificOutput"]["additionalContext"]
    assert "briefing unavailable" in context
    assert "not read this as permission" in context
    assert "permissionDecision" not in response["hookSpecificOutput"]


def test_hook_without_an_api_key_does_not_call_the_network(monkeypatch) -> None:
    monkeypatch.delenv("MEMORYWORKS_API_KEY", raising=False)
    monkeypatch.delenv("ORGMEMORY_API_KEY", raising=False)

    response = hooks.claude_code(_bash("npm publish"))

    assert (
        "MEMORYWORKS_API_KEY is not set"
        in response["hookSpecificOutput"]["additionalContext"]
    )


def test_cursor_hook_only_ever_escalates() -> None:
    for briefing in (_briefing(), _briefing(verdict="requires_approval")):
        fake = FakeMemoryWorks(briefing)
        response = hooks.cursor(
            {"command": "helm upgrade payments ./chart", "cwd": "/work/shop"},
            client=fake.client(),
        )
        assert response["permission"] == "ask"
        assert "record_orgmemory_outcome" in response["agent_message"]
        assert fake.requests[0][1]["surface"] == "cursor"
    down = hooks.cursor(
        {"command": "helm upgrade x"}, client=FakeMemoryWorks(status=500).client()
    )
    assert down["permission"] == "ask"


def test_cli_hook_reads_stdin_and_prints_hook_json(monkeypatch, capsys) -> None:
    monkeypatch.delenv("MEMORYWORKS_API_KEY", raising=False)
    monkeypatch.delenv("ORGMEMORY_API_KEY", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_bash("git push"))))

    assert cli.main(["hook", "claude-code"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


def test_cli_hook_prints_nothing_for_steps_it_does_not_brief(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_bash("pytest -q"))))
    assert cli.main(["hook", "claude-code"]) == 0
    assert capsys.readouterr().out == ""
