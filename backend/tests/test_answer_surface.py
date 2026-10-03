"""The answer surface shows answers, never the machinery behind them.

Ingested commit records are dense with labelled identifiers — SHAs, URLs,
timestamps. They rank well (short lines, full of query terms) and read as
gibberish: strings and numbers that mean nothing without the system that
produced them. Someone asking why something broke should never be handed one.
"""

from app.graph.base import GraphEvidence
from app.retrieval.universal import _answerable_line, universal_evidence_answer


def _evidence(chunk_id: str, title: str, text: str, score: float) -> GraphEvidence:
    return GraphEvidence(
        chunk_id=chunk_id,
        source_title=title,
        source_type="repo_file",
        source_url="",
        text=text,
        score=score,
        metadata={"path": title, "project_id": "p1"},
    )


COMMIT_RECORD = _evidence(
    "c2",
    "repository-metadata",
    "Latest commit message: theme changed\n"
    "Commit SHA: 77fde75fe032e03f332026acecc78b6802e42c8c\n"
    "Committed at: 2026-07-25T23:07:01Z\n"
    "URL: https://github.com/acme/widgets\n",
    # Deliberately outranks the README: metadata chunks score well because they
    # are short and dense with query terms. Filtering, not ranking, is the fix.
    95.0,
)
README = _evidence(
    "c1",
    "README.md",
    "# Widgets\n\nRun locally:\n\nnpm install\nnpm run dev\n\n"
    "The dev server listens on port 3000.\n",
    90.0,
)


def test_a_higher_ranked_commit_record_does_not_crowd_out_the_real_answer():
    answer = universal_evidence_answer("How do I run this locally?", [COMMIT_RECORD, README])

    assert answer["sufficient"]
    assert "npm run dev" in answer["answer"]
    assert "77fde75f" not in answer["answer"]
    assert "2026-07-25T23:07:01Z" not in answer["answer"]


def test_the_human_written_part_of_a_commit_record_still_answers():
    """Filtering machinery must not throw away what a person actually wrote."""
    answer = universal_evidence_answer("what changed recently?", [COMMIT_RECORD, README])

    assert answer["sufficient"]
    assert "theme changed" in answer["answer"]
    assert "77fde75f" not in answer["answer"]


def test_commit_machinery_is_never_quoted_as_an_answer():
    for line in [
        "Commit SHA: 77fde75fe032e03f332026acecc78b6802e42c8c",
        "Committed at: 2026-07-25T23:07:01Z",
        "URL: https://github.com/acme/widgets",
        "Latest commit URL: https://github.com/acme/widgets/commit/7b383d4b",
        "Author email: someone@example.com",
    ]:
        assert not _answerable_line(line), f"machinery leaked into an answer: {line}"


def test_sentences_that_happen_to_carry_a_label_still_answer():
    """The filter targets identifier-valued fields, not every colon.

    A commit *message* is what a human wrote about the change and is exactly what
    someone asking "what changed" wants; only the SHA beside it is machinery.
    """
    for line in [
        "Latest commit message: fixed the navy background on the login page",
        "Root cause: the worker was pointed at the old queue after the redeploy",
        "The deploy failed because the worker still pointed at the retired queue.",
    ]:
        assert _answerable_line(line), f"a real statement was filtered out: {line}"


def test_short_single_word_labels_remain_filtered_by_the_older_css_rule():
    """Documents a pre-existing limit rather than asserting it is right.

    `Label: value` with no inner colons is dropped by the rule that strips CSS
    declarations, so short prose fields are lost with them. Ownership questions
    have their own resolver, so this is a known edge rather than a live gap —
    but a future change to that rule should know it is load-bearing here.
    """
    assert not _answerable_line("Owner: the payments team")
    # A multi-word label sidesteps that rule entirely, which is why the behaviour
    # looks inconsistent from the outside.
    assert _answerable_line("Default branch: main")


# What "are there any payments set up in the repos?" used to come back with: the
# question's framing words ("setup", "repos") matched source code about setup,
# and nothing checked that a quoted line was about payments at all.
RANKER_SOURCE = _evidence(
    "c3",
    "app/graph/graph_ranker.py",
    "INTENT_TERMS = {\n"
    '    "setup": {"build", "dev", "install", "make", "npm", "run", "setup", "start"},\n'
    '    "repository_locator": {"auth", "login"},\n'
    "}\n"
    "    # Searching company repositories is a setup concern for every repository.\n"
    '    "company repositories",\n',
    95.0,
)
PITCH = _evidence(
    "c4",
    "README.md",
    "Every engineering org already knows why its payments service failed last time.\n"
    "\n\n\n\n"
    "Run docker compose up -d to set up a local copy.\n",
    90.0,
)


def test_framing_words_alone_never_answer_a_question_about_something_else():
    answer = universal_evidence_answer(
        "are there any payments setup in any of the repos?", [RANKER_SOURCE]
    )

    assert not answer["sufficient"]
    assert answer["answer"].startswith("I do not have enough company memory")
    assert "Nothing in the searched sources mentions payments." in answer["answer"]


def test_a_passing_mention_of_the_subject_does_not_answer_what_was_asked_about_it():
    # README mentions payments, and separately explains setup — neither line is a
    # payments setup.
    answer = universal_evidence_answer(
        "are there any payments setup in any of the repos?", [RANKER_SOURCE, PITCH]
    )

    assert not answer["sufficient"]


def test_a_real_payments_setup_still_answers():
    setup = _evidence(
        "c5",
        "docs/billing.md",
        "Payments run through Stripe; set STRIPE_SECRET_KEY and run make setup-payments.\n",
        80.0,
    )

    answer = universal_evidence_answer(
        "are there any payments setup in any of the repos?", [RANKER_SOURCE, setup]
    )

    assert answer["sufficient"]
    assert "Stripe" in answer["answer"]
    assert "graph_ranker" not in answer["answer"]


def test_quoted_data_literals_from_source_code_are_not_prose():
    assert not _answerable_line('"setup": {"build", "dev", "install", "start"},')
    assert not _answerable_line('"company repositories",')
    assert _answerable_line('"Payments" are handled by the billing service.')
