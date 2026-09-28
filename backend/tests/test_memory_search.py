"""Memory ranking: whole words, informative terms, and honest emptiness."""

import pytest

from app.retrieval import memory_search, semantic


@pytest.fixture(autouse=True)
def lexical_only(monkeypatch):
    """A developer .env may enable the local cross-encoder; these tests pin lexical ranking."""
    monkeypatch.setattr(semantic, "get_reranker", lambda: None)


def unit(memory_id, subject, content, service="", kind="fact"):
    return {
        "id": memory_id,
        "type": kind,
        "subject": subject,
        "content": content,
        "scope": {"service": service},
        "updated_at": "2026-08-01T00:00:00+00:00",
    }


MEMORY = [
    unit("retry", "payments retry wrapper", "Retries re-sent card authorizations after a timeout."),
    unit("owner", "payments-service", "payments-service is owned by the Payments Core team."),
    unit("sessions", "checkout sessions", "Checkout sessions are stored in PostgreSQL."),
    unit("emails", "notification-worker", "The worker sent duplicate marketing emails."),
]


def ids(query, units=MEMORY, limit=10):
    return [item["id"] for item, _ in memory_search.rank(units, query, limit)]


def test_function_words_never_match():
    terms = memory_search.query_terms("Update the colors on the marketing site")
    assert terms == [memory_search.stem(word) for word in ("colors", "marketing", "site")]
    assert ids("the on to is") == []


def test_matches_whole_words_not_fragments():
    # "pay" used to match inside "payments", so this query found everything.
    assert ids("pay button") == []


def test_inflections_meet():
    assert ids("retry authorization")[0] == "retry"
    assert ids("retried the charge") == ["retry"]


def test_rare_terms_outweigh_common_ones():
    pool = [
        *MEMORY,
        unit("a", "payments ledger", "payments ledger entries"),
        unit("b", "payments fees", "payments fee rounding"),
    ]
    assert ids("payments sessions", pool)[0] == "sessions"


def test_weak_hits_below_the_floor_are_dropped():
    pool = [
        unit("strong", "checkout sessions", "Checkout sessions are stored in PostgreSQL."),
        unit("weak", "billing", "Invoices mention PostgreSQL once."),
    ]
    assert ids("checkout sessions postgresql", pool) == ["strong"]


class FakeReranker:
    def __init__(self, logits):
        self._logits = logits

    def logits(self, query, documents):
        return [self._logits.get(document.split(".")[0], -10.0) for document in documents]


def test_reranker_reorders_and_drops_what_it_judges_unrelated(monkeypatch):
    fake = FakeReranker({"payments-service": 4.0, "payments retry wrapper": 1.0})
    monkeypatch.setattr(semantic, "get_reranker", lambda: fake)
    assert ids("payments retry owner") == ["owner", "retry"]


def test_a_failing_reranker_keeps_the_lexical_order(monkeypatch):
    class Broken:
        def logits(self, query, documents):
            raise RuntimeError("model missing")

    monkeypatch.setattr(semantic, "get_reranker", lambda: Broken())
    assert ids("retry authorization")[0] == "retry"
