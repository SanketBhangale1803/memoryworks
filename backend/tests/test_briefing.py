"""The pre-action briefing: verdicts, scoping, and what it refuses to imply.

These tests are written against the properties an agent depends on, not the
current wording. A briefing is a control on real work, so the things worth
pinning are: the same intent gives the same verdict, a record never appears
twice, an unnamed service never borrows another team's history, and an empty
memory says "empty" rather than "fine".
"""

from app.memory import briefing


def unit(memory_id, kind, subject, service="payments", content="body"):
    return {
        "id": memory_id,
        "project_id": "prj_1",
        "type": kind,
        "subject": subject,
        "content": content,
        "scope": {"service": service},
        "confidence": 0.9,
        "source_ids": ["src_1"],
        "updated_at": "2026-08-01T00:00:00+00:00",
    }


MEMORY = [
    unit("mem_incident_1", "incident", "payments outage: pool exhaustion"),
    unit("mem_incident_2", "incident", "payments degraded: checkout timeouts"),
    unit("mem_decision", "decision", "cap payments worker concurrency"),
    unit("mem_dependency", "dependency", "payments shares the PostgreSQL cluster"),
    unit("mem_procedure", "procedure", "payments pool exhaustion first response"),
    unit("mem_other", "incident", "billing invoice drift", service="billing"),
]


def build(task, service="", memory=None, precedents=None):
    corpus = MEMORY if memory is None else memory

    def search(text, project_id="", limit=12):
        terms = {token for token in text.casefold().split() if len(token) > 2}
        hits = [
            item
            for item in corpus
            if terms & set(f"{item['subject']} {item['content']}".casefold().split())
        ]
        return {"results": hits[:limit]}

    def list_by_kind(kind, project_id):
        return [item for item in corpus if item["type"] == kind]

    return briefing.build(
        task=task,
        service=service,
        project_id="prj_1",
        search=search,
        list_by_kind=list_by_kind,
        precedents=lambda _task: precedents or [],
    )


def cited_ids(brief):
    groups = ("must_read", "constraints", "prior_incidents", "blast_radius", "procedures")
    return [item["memory_id"] for group in groups for item in brief[group]]


def test_a_consequential_change_requires_a_person_and_says_why():
    brief = build("restart the payments connection pool", service="payments")
    assert brief["verdict"] == "requires_approval"
    assert brief["consequential_action"] == "restarting"
    assert brief["requires_approval"], "an approval verdict must carry its reasons"
    assert any("restarting" in reason for reason in brief["requires_approval"])


def test_raising_a_limit_counts_as_a_change_even_though_it_sounds_mild():
    # The capacity change is the move that caused the outage in memory; a
    # briefing that waves it through is worse than no briefing.
    brief = build("raise worker concurrency on payments", service="payments")
    assert brief["verdict"] == "requires_approval"
    assert brief["consequential_action"] == "raising a limit"


def test_a_read_only_intent_is_not_gated():
    brief = build("read the payments connection dashboard", service="payments")
    assert brief["verdict"] == "proceed_with_context"
    assert brief["requires_approval"] == []


def test_the_verdict_is_stable_for_the_same_intent():
    # No model runs, so two identical calls must agree. An unstable control is
    # not a control.
    first = build("restart the payments connection pool", service="payments")
    second = build("restart the payments connection pool", service="payments")
    assert first["verdict"] == second["verdict"]
    assert cited_ids(first) == cited_ids(second)


def test_no_memory_is_reported_as_unknown_not_as_safe():
    brief = build("deploy the widget service", service="widget", memory=[])
    assert brief["verdict"] == "no_memory"
    assert "nothing" in brief["headline"].casefold()
    assert brief["open_questions"], "an empty briefing has to ask for something"


def test_every_cited_memory_appears_exactly_once():
    brief = build("restart the payments connection pool", service="payments")
    ids = cited_ids(brief)
    assert len(ids) == len(set(ids))
    assert brief["memory_count"] == len(set(ids))


def test_a_named_service_never_borrows_another_services_history():
    brief = build("restart the payments connection pool", service="payments")
    assert "mem_other" not in cited_ids(brief), "billing's incident is not payments' history"


def test_without_a_service_nothing_is_presented_as_a_constraint():
    # Kind-scoped pulls are workspace-wide when unscoped, and another team's
    # postmortem shown under "this has gone wrong before" is indistinguishable
    # from a real warning. The briefing falls back to relevance and asks.
    brief = build("rename a variable in the docs site")
    assert brief["service"] is None
    assert brief["constraints"] == []
    assert brief["prior_incidents"] == []
    assert brief["blast_radius"] == []
    assert any("service" in question.casefold() for question in brief["open_questions"])


def test_a_service_named_only_in_prose_is_still_resolved():
    brief = build("restart the pool on service payments")
    assert brief["service"] == "payments"
    assert brief["prior_incidents"] or brief["must_read"]


def test_every_citation_carries_an_id_and_a_reason():
    brief = build("restart the payments connection pool", service="payments")
    for group in ("must_read", "constraints", "prior_incidents", "blast_radius", "procedures"):
        for item in brief[group]:
            assert item["memory_id"], "a citation without an id cannot be opened"
            assert item["why_it_matters"], "the reason a record is shown is not the reader's guess"


def test_precedent_is_carried_through_when_the_library_has_any():
    brief = build(
        "restart the payments connection pool",
        service="payments",
        precedents=[
            {
                "id": "skl_1",
                "name": "payments-pool-recovery",
                "trigger": "payments pool exhaustion",
                "steps": ["check connection count"],
                "successes": 3,
                "confidence": 0.8,
            }
        ],
    )
    assert brief["precedents"][0]["skill_id"] == "skl_1"
    assert brief["precedents"][0]["successes"] == 3


def test_a_failing_retrieval_degrades_the_briefing_instead_of_raising():
    # An agent standing in front of a production change gets a partial briefing
    # or a hard failure. Partial is the correct trade.
    def exploding(*args, **kwargs):
        raise RuntimeError("index unavailable")

    brief = briefing.build(
        task="restart the payments connection pool",
        service="payments",
        project_id="prj_1",
        search=exploding,
        list_by_kind=exploding,
        precedents=exploding,
    )
    assert brief["verdict"] == "no_memory"
    assert brief["requires_approval"], "a consequential intent stays gated even with no evidence"


def test_relevance_hits_stay_within_the_named_service_and_its_dependencies():
    corpus = [
        unit("mem_payments_retry", "incident", "payments retry double charge", "payments-service"),
        unit("mem_email_retry", "incident", "notification retry storm", "notification-worker"),
        unit(
            "mem_ledger_rule",
            "decision",
            "ledger writes retry through the ledger-service API",
            "ledger-service",
        ),
        unit(
            "mem_dep",
            "dependency",
            "billing-service depends on ledger-service",
            "billing-service",
            content="billing-service depends on ledger-service for invoice balances.",
        ),
    ]

    def shown(result):
        return {
            item["memory_id"]
            for group in ("must_read", "constraints", "prior_incidents", "blast_radius")
            for item in result[group]
        }

    worker = build("tune the retry loop", service="notification-worker", memory=corpus)
    assert "mem_email_retry" in shown(worker)
    assert "mem_payments_retry" not in shown(worker)

    billing = build("retry ledger writes", service="billing-service", memory=corpus)
    assert "mem_ledger_rule" in shown(billing)
    assert "mem_payments_retry" not in shown(billing)


def test_a_recorded_review_rule_requires_approval_whatever_kind_it_is():
    corpus = [
        unit(
            "mem_owner",
            "ownership",
            "fraud-engine",
            "fraud-engine",
            content="fraud-engine is owned by the Risk team, and changes to it need a Risk reviewer.",
        ),
    ]
    result = build("adjust the scoring threshold", service="fraud-engine", memory=corpus)
    assert result["verdict"] == "requires_approval"
    assert any("review" in reason for reason in result["requires_approval"])

    quiet = [
        unit("mem_owner", "ownership", "fraud-engine", "fraud-engine", content="Owned by Risk.")
    ]
    result = build("adjust the scoring threshold", service="fraud-engine", memory=quiet)
    assert result["requires_approval"] == []
