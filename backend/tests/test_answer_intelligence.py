"""Answers that do not come from company evidence, and the pass that picks between answers."""

from app.audit import AuditService
from app.graph.base import GraphEvidence
from app.hcag_adapter import HCAGAdapter
from app.ingestion import IngestionService
from app.retrieval import RetrievalService
from app.retrieval.conversation import assistant_reply, is_company_question
from app.retrieval.deliberation import deliberate
from app.retrieval.handoff import build_handoff


def _evidence(chunk_id: str, title: str, text: str, source_type: str = "repo_file", path: str = ""):
    return GraphEvidence(
        chunk_id=chunk_id,
        text=text,
        source_type=source_type,
        source_title=title,
        source_url=f"https://github.com/acme/api/blob/main/{title}",
        score=0.8,
        service_names=["checkout"],
        metadata={"path": path, "source_id": f"src:{chunk_id}"},
    )


def test_greeting_is_answered_instead_of_refused(graph):
    hcag = HCAGAdapter(graph)
    project_id = IngestionService(graph, hcag, AuditService()).create_project("Empty")

    result = RetrievalService(hcag).ask(project_id, "hello")

    assert result["answer_sufficient"] is True
    assert result["answer_scope"] == "assistant"
    assert "enough company memory" not in result["answer"]
    assert result["evidence"] == []


def test_conversational_shortcut_never_swallows_a_real_question():
    for question in (
        "what is this repository about?",
        "what is the checkout service?",
        "who is on call this week?",
        "help me understand why payments failed",
        "what do you do when the database is down",
    ):
        assert assistant_reply(question) is None, question

    for greeting in ("hi", "Hello!", "hey there", "thanks", "who are you?", "what can you do?"):
        assert assistant_reply(greeting) is not None, greeting


def test_company_questions_never_fall_back_to_general_knowledge():
    assert is_company_question("why is our checkout service failing?")
    assert is_company_question("what changed in PR 128?")
    assert is_company_question("where is DATABASE_URL configured")
    assert is_company_question("what did the team decide in slack")

    assert not is_company_question("what is a race condition?")
    assert not is_company_question("explain the CAP theorem")


def test_general_knowledge_answers_when_company_memory_is_empty(graph, monkeypatch):
    hcag = HCAGAdapter(graph)
    project_id = IngestionService(graph, hcag, AuditService()).create_project("Empty")

    monkeypatch.setattr(
        "app.retrieval.conversation.generate_grounded_json",
        lambda prompt, provider=None: (
            {"answer": "A race condition is a timing-dependent bug.", "confident": True},
            type("P", (), {"id": "gpt"})(),
        ),
    )

    result = RetrievalService(hcag).ask(project_id, "what is a race condition?")

    assert result["answer_scope"] == "general_knowledge"
    assert "timing-dependent" in result["answer"]
    # Nothing in the company graph supports it, so nothing may be cited.
    assert result["evidence"] == []
    assert "general knowledge" in result["trust_score"]["reason"].casefold()


def test_missing_company_memory_still_refuses_for_company_questions(graph, monkeypatch):
    hcag = HCAGAdapter(graph)
    project_id = IngestionService(graph, hcag, AuditService()).create_project("Empty")

    def unexpected(*args, **kwargs):
        raise AssertionError("general knowledge must not answer a company question")

    monkeypatch.setattr("app.retrieval.conversation.generate_grounded_json", unexpected)

    result = RetrievalService(hcag).ask(project_id, "why is our checkout service failing?")

    assert result["answer_sufficient"] is False
    assert result["answer_scope"] == "company_memory"


def test_deliberation_judges_between_candidates(monkeypatch):
    evidence = [_evidence("c1", "checkout.py", "checkout calls the retired payments v1 url")]
    produced = []

    def synthesize(query, items, *, compiled_context=None, model_provider=None, lens=""):
        produced.append(lens)
        return {
            "answer": f"answer for {lens[:12]}",
            "likely_cause": "cause",
            "safe_actions": [],
            "approval_required": [],
            "sufficient": True,
            "supporting_chunk_ids": ["c1"],
        }

    monkeypatch.setattr("app.retrieval.deliberation.llm_answer", synthesize)
    monkeypatch.setattr(
        "app.retrieval.deliberation.generate_grounded_json",
        lambda prompt, provider=None: (
            {"choice": 3, "reason": "it answers what was asked"},
            type("P", (), {"id": "gpt"})(),
        ),
    )

    winner = deliberate("why is checkout failing?", evidence, candidate_count=5)

    assert len(produced) == 5
    assert len(set(produced)) == 5, "each candidate must read the question differently"
    assert winner["_deliberation"]["candidate_count"] == 5
    assert winner["_deliberation"]["selected_lens"] == "procedural"
    assert winner["_deliberation"]["reason"] == "it answers what was asked"


def test_deliberation_falls_back_to_first_candidate_without_a_judge(monkeypatch):
    evidence = [_evidence("c1", "checkout.py", "checkout calls payments v1")]

    monkeypatch.setattr(
        "app.retrieval.deliberation.llm_answer",
        lambda query, items, **kwargs: {
            "answer": "grounded answer",
            "likely_cause": "cause",
            "safe_actions": [],
            "approval_required": [],
            "sufficient": True,
        },
    )
    monkeypatch.setattr(
        "app.retrieval.deliberation.generate_grounded_json",
        lambda prompt, provider=None: None,
    )

    winner = deliberate("why is checkout failing?", evidence, candidate_count=3)

    assert winner["answer"] == "grounded answer"
    assert winner["_deliberation"]["selected_lens"] == "direct"


def test_a_slow_candidate_is_dropped_instead_of_holding_up_the_answer(monkeypatch):
    # A reasoning model at high effort took ~140 s per candidate, and the asker
    # got a proxy 502 instead of any answer. Whatever finishes in time is judged.
    import threading

    release = threading.Event()

    def synthesize(query, items, *, compiled_context=None, model_provider=None, lens=""):
        if "causal chain" in lens:
            release.wait(5)
        return {
            "answer": f"answer for {lens[:12]}",
            "likely_cause": "cause",
            "safe_actions": [],
            "approval_required": [],
            "sufficient": True,
        }

    monkeypatch.setattr("app.retrieval.deliberation.llm_answer", synthesize)
    monkeypatch.setattr(
        "app.retrieval.deliberation.generate_grounded_json",
        lambda prompt, provider=None: None,
    )

    winner = deliberate(
        "why is checkout failing?",
        [_evidence("c1", "checkout.py", "checkout calls payments v1")],
        candidate_count=3,
        deadline_seconds=0.5,
    )
    release.set()

    assert winner["_deliberation"]["candidate_count"] == 2
    assert "causal" not in {item["lens"] for item in winner["_deliberation"]["candidates"]}


def test_deliberation_returns_nothing_when_no_model_answers(monkeypatch):
    monkeypatch.setattr("app.retrieval.deliberation.llm_answer", lambda *args, **kwargs: None)

    assert deliberate("why is checkout failing?", [_evidence("c1", "a.py", "text")]) is None


def test_handoff_is_built_for_code_work_and_carries_only_relevant_context():
    evidence = [
        _evidence(
            "c1",
            "checkout.py",
            "checkout posts to PAYMENTS_V1_URL on every order",
            path="checkout.py",
        ),
        _evidence("c2", "#platform thread", "unrelated meeting notes", source_type="slack"),
    ]
    grounded = {
        "sufficient": True,
        "answer": "Production still points at the retired v1 endpoint.",
        "likely_cause": "Production config still references PAYMENTS_V1_URL.",
        "safe_actions": ["Point the production config at the v2 endpoint."],
        "approval_required": ["Deploy the config change."],
    }

    handoff = build_handoff("fix the checkout 502 bug in checkout.py", grounded, evidence)

    assert handoff
    assert handoff["files"] == ["checkout.py"]
    assert [item["title"] for item in handoff["context"]] == ["checkout.py"]
    assert "PAYMENTS_V1_URL" in handoff["prompt"]
    assert "unrelated meeting notes" not in handoff["prompt"]
    assert handoff["approval_required"] == ["Deploy the config change."]


def test_handoff_accepts_non_code_sources_that_name_a_file():
    """An incident report naming a file locates the work just as well as the file does."""
    evidence = [
        _evidence(
            "c1",
            "Checkout 502 incident",
            "Root cause: checkout.py still points at the retired PAYMENTS_V1_URL endpoint.",
            source_type="incident",
        )
    ]
    grounded = {
        "sufficient": True,
        "answer": "Production still points at the retired v1 endpoint.",
        "likely_cause": "Stale production config.",
        "safe_actions": [],
        "approval_required": [],
    }

    handoff = build_handoff("fix the checkout 502 bug", grounded, evidence)

    assert handoff
    assert handoff["files"] == ["checkout.py"]
    assert "PAYMENTS_V1_URL" in handoff["prompt"]


def test_no_handoff_for_questions_that_are_not_code_work():
    evidence = [_evidence("c1", "adr.md", "we chose postgres for transactional integrity")]
    grounded = {
        "sufficient": True,
        "answer": "Postgres was chosen for transactional integrity.",
        "likely_cause": "Not applicable — decision question.",
        "safe_actions": [],
        "approval_required": [],
    }

    assert build_handoff("why did we choose postgres?", grounded, evidence) is None


def test_handoff_covers_interface_work_not_just_backend_code():
    """Frontend work is code work.

    The original gate recognised no styling vocabulary and no web file
    extensions, so "change the background colour" — and every request like it —
    silently produced no handoff at all.
    """
    evidence = [
        _evidence(
            "c1",
            "app/globals.css",
            ":root { --paper: #fbf7f7; }",
            source_type="repo_file",
            path="app/globals.css",
        )
    ]
    grounded = {
        "sufficient": True,
        "answer": "The background is set by --paper in globals.css.",
        "likely_cause": "",
        "safe_actions": ["Update the --paper token."],
        "approval_required": [],
    }

    handoff = build_handoff("change the main bg color to navy blue", grounded, evidence)

    assert handoff is not None
    assert handoff["files"] == ["app/globals.css"]


def test_naming_an_editor_is_itself_a_handoff_request():
    """ "Ask cursor to change X" is delegation, even when X names no code noun."""
    evidence = [_evidence("c1", "theme.scss", "$bg: white;", path="theme.scss")]
    grounded = {
        "sufficient": True,
        "answer": "The theme colour lives in theme.scss.",
        "likely_cause": "",
        "safe_actions": [],
        "approval_required": [],
    }

    assert build_handoff("ask cursor to change the main bg to navy", grounded, evidence)


def test_non_code_requests_still_produce_no_handoff():
    """The broadened vocabulary must not turn every verb into an editor task."""
    evidence = [_evidence("c1", "rota.md", "Maya is on call this week.")]
    grounded = {
        "sufficient": True,
        "answer": "Maya is on call.",
        "likely_cause": "",
        "safe_actions": [],
        "approval_required": [],
    }

    for question in ("update the on-call rota", "add Maya to the incident channel"):
        assert build_handoff(question, grounded, evidence) is None


def test_handoff_never_mixes_files_from_two_repositories():
    """Retrieval spans the workspace; a task is applied to one checkout.

    Evidence from a neighbouring project produced a handoff naming files that do
    not exist in the repository being edited, so the agent edited the wrong repo.
    """

    def scoped(chunk_id, title, project_id, repository, rank_text):
        item = _evidence(chunk_id, title, rank_text, path=title)
        item.metadata.update({"project_id": project_id, "repository": repository})
        return item

    evidence = [
        scoped("c1", "src/styles.css", "prj_a", "https://github.com/acme/a.git", "--bg: black"),
        scoped("c2", "views/index.ejs", "prj_b", "https://github.com/acme/b.git", "<body>"),
        scoped("c3", "src/theme.css", "prj_a", "https://github.com/acme/a.git", "--fg: white"),
    ]
    grounded = {
        "sufficient": True,
        "answer": "The background token lives in styles.css.",
        "likely_cause": "",
        "safe_actions": [],
        "approval_required": [],
    }

    handoff = build_handoff("make the bg color navy blue", grounded, evidence)

    assert handoff["project_id"] == "prj_a"
    assert handoff["repository"] == "https://github.com/acme/a.git"
    assert handoff["files"] == ["src/styles.css", "src/theme.css"]
    assert "views/index.ejs" not in handoff["files"]
    assert all("index.ejs" not in item["title"] for item in handoff["context"])


def test_an_ambiguous_change_asks_which_repository_instead_of_guessing(graph):
    """Nineteen connected repos make "change the background" a coin flip."""
    from app.retrieval.clarify import clarification

    def scoped(chunk_id, title, project_id, name):
        item = _evidence(chunk_id, title, "--bg: black", path=title)
        item.metadata.update({"project_id": project_id, "project_name": name})
        return item

    evidence = [
        scoped("c1", "src/styles.css", "prj_a", "storefront"),
        scoped("c2", "app/theme.css", "prj_b", "admin-portal"),
    ]

    result = clarification("make the bg navy", evidence, actionable=True)

    assert result is not None
    assert result["reason"] == "ambiguous_target"
    assert {option["label"] for option in result["options"]} == {"storefront", "admin-portal"}


def test_a_clear_target_is_never_interrupted(graph):
    """One dominant repository means there is nothing to ask about."""
    from app.retrieval.clarify import clarification

    def scoped(chunk_id, title, project_id, name):
        item = _evidence(chunk_id, title, "--bg: black", path=title)
        item.metadata.update({"project_id": project_id, "project_name": name})
        return item

    evidence = [
        scoped("c1", "src/styles.css", "prj_a", "storefront"),
        scoped("c2", "src/theme.css", "prj_a", "storefront"),
        scoped("c3", "app/x.css", "prj_b", "admin-portal"),
    ]

    assert clarification("make the bg navy", evidence, actionable=True) is None


def test_reading_questions_are_never_blocked_by_clarification(graph):
    """Cross-repository *questions* are legitimate; only changes need a target."""
    from app.retrieval.clarify import clarification

    def scoped(chunk_id, title, project_id, name):
        item = _evidence(chunk_id, title, "notes", path=title)
        item.metadata.update({"project_id": project_id, "project_name": name})
        return item

    evidence = [
        scoped("c1", "a.md", "prj_a", "storefront"),
        scoped("c2", "b.md", "prj_b", "admin-portal"),
    ]

    assert clarification("what changed recently?", evidence, actionable=False) is None


def test_a_pinned_repository_is_a_boundary_not_a_hint():
    """Widening the search may improve the answer; it must not move the commit.

    Asking about a repository with no matching code used to fall back to the
    workspace, find a neighbour's stylesheet, and hand the agent a task aimed at
    a checkout nobody selected.
    """

    def scoped(chunk_id, title, project_id, repository, rank_text):
        item = _evidence(chunk_id, title, rank_text, path=title)
        item.metadata.update({"project_id": project_id, "repository": repository})
        return item

    evidence = [
        scoped("c1", "src/styles.css", "prj_b", "https://github.com/acme/b.git", "--bg: black"),
    ]
    grounded = {
        "sufficient": True,
        "answer": "The background token lives in styles.css.",
        "likely_cause": "",
        "safe_actions": [],
        "approval_required": [],
    }

    assert build_handoff("make the bg color navy blue", grounded, evidence) is not None
    assert (
        build_handoff("make the bg color navy blue", grounded, evidence, pinned_project_id="prj_a")
        is None
    ), "a task must never be aimed at a repository the asker did not pin"


def test_a_pin_keeps_only_the_pinned_repositorys_evidence():
    def scoped(chunk_id, title, project_id, repository, rank_text):
        item = _evidence(chunk_id, title, rank_text, path=title)
        item.metadata.update({"project_id": project_id, "repository": repository})
        return item

    evidence = [
        scoped("c1", "src/styles.css", "prj_b", "https://github.com/acme/b.git", "--bg: black"),
        scoped("c2", "src/theme.css", "prj_b", "https://github.com/acme/b.git", "--bg: black"),
        scoped("c3", "app/main.css", "prj_a", "https://github.com/acme/a.git", "--bg: black"),
    ]
    grounded = {
        "sufficient": True,
        "answer": "The background token lives in a stylesheet.",
        "likely_cause": "",
        "safe_actions": [],
        "approval_required": [],
    }

    handoff = build_handoff(
        "make the bg color navy blue", grounded, evidence, pinned_project_id="prj_a"
    )

    assert handoff["project_id"] == "prj_a"
    assert handoff["files"] == ["app/main.css"]


def _styled(chunk_id, title, project_id, name):
    item = _evidence(chunk_id, title, "--bg: black", path=title)
    item.metadata.update({"project_id": project_id, "project_name": name})
    return item


def test_a_topic_without_a_target_is_asked_about_not_executed(graph):
    """ "change the styling" is a subject, not a task.

    Handed to an agent it spent ten minutes exploring the repository before
    reporting that it needed a target all along. That is the same question, later.
    """
    from app.retrieval.clarify import clarification

    evidence = [_styled("c1", "src/styles.css", "prj_a", "storefront")]

    result = clarification("change the styling", evidence, actionable=True)

    assert result is not None
    assert result["reason"] == "missing_target"
    assert "src/styles.css" in result["files"]
    assert all(
        not option.get("project_id") for option in result["options"]
    ), "these are examples of the missing detail, not repositories to choose"


def test_a_request_that_names_its_target_is_left_alone(graph):
    """The guardrail must not become the assistant that asks about everything."""
    from app.retrieval.clarify import clarification

    evidence = [_styled("c1", "src/styles.css", "prj_a", "storefront")]

    for clear in (
        "make the styling navy blue",
        "change the theme to #0a1f44",
        "set the layout padding to 24px",
        "change the styling to match the login page",
        "switch the theme to light mode",
        "restyle the header to look like the marketing site",
    ):
        assert clarification(clear, evidence, actionable=True) is None, clear


def test_missing_target_is_asked_even_when_a_repository_was_pinned(graph):
    """Pinning answers "where", which says nothing about "what"."""
    from app.retrieval.clarify import clarification

    evidence = [_styled("c1", "src/styles.css", "prj_a", "storefront")]
    result = clarification("change the styling", evidence, actionable=True)

    assert result["reason"] == "missing_target"


def test_a_narrow_change_is_never_treated_as_a_topic(graph):
    """Only broad subjects qualify; a named file or symbol is specific enough."""
    from app.retrieval.clarify import clarification

    evidence = [_styled("c1", "src/checkout.py", "prj_a", "storefront")]

    assert clarification("fix the 502 in checkout.py", evidence, actionable=True) is None
