"""Answer "can MemoryWorks do X?" from what the product actually does.

"Do you have the ability to ingest documents from GD?" is about MemoryWorks
itself. Company memory does not record the product's own features, so the
answer path found nothing and fell back to general knowledge, whose prompt
says the question is *not* about the user's company — and the reply was a
guess. This module answers those questions deterministically from the sources
the product supports today, the way a greeting is answered: no search, no
model call.

Only questions that clearly ask about MemoryWorks's ability to bring a named
source in are handled here. Anything else returns None and goes on to the
normal answer path.
"""

from __future__ import annotations

import re
from typing import Any

from .conversation import _normalize, _reply

# Asking MemoryWorks whether it can do something ("can you…", "do you have the
# ability to…") is about the product by construction.
_ASKS_US = re.compile(
    r"\b(?:"
    r"(?:can|could|will)\s+(?:you|memoryworks)"
    r"|(?:do|does)\s+(?:you|memoryworks)\s+"
    r"(?:have\s+(?:the\s+|an\s+|any\s+)?(?:ability|option|capability|way)|support|handle|allow|work\s+with)"
    r"|(?:are\s+you|is\s+memoryworks)\s+able\s+to"
    r")\b"
)
# "How do I add a file upload?" is usually about the asker's own code; it is
# about the product only when the product is named.
_ASKS_HOW = re.compile(
    r"\b(?:how\s+(?:do|can|should)\s+(?:i|we)|(?:can|could)\s+(?:i|we)"
    r"|is\s+(?:there\s+(?:a\s+)?way|it\s+possible)\s+to)\b"
)
_NAMES_PRODUCT = re.compile(
    r"\b(?:memoryworks|orgmemory|in\s+(?:the\s+)?(?:app|ui)|into\s+(?:company\s+)?memory"
    r"|this\s+(?:app|tool|product))\b"
)
_ACTION = re.compile(
    r"\b(?:ingest|import|connect|add|sync|pull|read|load|index|upload|bring|select|pick|choose"
    r"|remember|learn|integrate)\w*\b"
)


def _howto(steps: str, chat: str = "") -> str:
    return steps + (f"\n\nOr ask here: {chat}" if chat else "")


# The sources in the product today, with how to bring each one in. Keep this in
# step with the Add knowledge page (frontend/app/ingest/page.tsx) and the live
# entries in the connector catalog.
_LIVE: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\b(?:google\s+drive|gdrive|g\s+drive|gd|drive|google\s+docs|google\s+sheets)\b"
        ),
        _howto(
            "Yes. Open **Add knowledge → Import from Google Drive**, tick the files you want "
            "(your 100 most recently edited are listed, and you can filter by name), choose "
            "the memory space, and import. Docs, Sheets, and Slides are exported as text; "
            "PDFs and Office files are parsed; every memory links back to its file. Google "
            "Drive has to be connected first (read-only) on **Sources**.",
            "“import the first 3 files from my Google Drive”.",
        ),
    ),
    (
        re.compile(r"\b(?:github|repo|repos|repository|repositories|codebase)\b"),
        _howto(
            "Yes. Open **Add knowledge → Connect a repository** and pick one: MemoryWorks "
            "reads its code, docs, issues, pull requests, and ownership. Signing in with "
            "GitHub connects your repositories, so they are ready to pick.",
            "“import owner/repo” or “import all my repos”.",
        ),
    ),
    (
        re.compile(r"\bslack\b"),
        "Yes. Open **Add knowledge → Remember a channel**, connect Slack once, and choose a "
        "channel. Decisions and conventions are remembered with a link back to each message.",
    ),
    (
        re.compile(r"\bnotion\b"),
        "Yes. Connect Notion on **Sources**; its pages and database rows are synced into "
        "memory, each linked back to the page it came from.",
    ),
    (
        re.compile(r"\b(?:microsoft\s+)?teams\b"),
        "Yes. Connect Microsoft Teams on **Sources**; channel messages are synced into "
        "memory with their permissions intact.",
    ),
    (
        re.compile(r"\b(?:websites?|web\s*pages?|urls?|links?|blogs?)\b"),
        _howto(
            "Yes. Open **Add knowledge → Ingest a website** and paste a public URL; the page "
            "becomes searchable memory linked to its address.",
            "“import https://…”.",
        ),
    ),
    (
        re.compile(
            r"\b(?:pdfs?|files?|documents?|docs|uploads?|word|excel|powerpoint|spreadsheets?"
            r"|slides|presentations?|emails?\s+exports?|mail\s+exports?|text)\b"
        ),
        "Yes. Open **Add knowledge → Upload documents**: PDF, Word, Excel, PowerPoint, HTML, "
        "mail exports, code, and text files are read, and each memory links back to its "
        "file. To bring documents in from Google Drive instead, use **Import from Google Drive**.",
    ),
]

# Named in the catalog but not available to connect yet.
_NOT_YET: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bgmail\b"), "Gmail"),
    (re.compile(r"\boutlook\b"), "Outlook"),
    (re.compile(r"\b(?:jira|confluence|atlassian)\b"), "Atlassian (Jira and Confluence)"),
    (re.compile(r"\blinear\b"), "Linear"),
    (re.compile(r"\bclickup\b"), "ClickUp"),
    (re.compile(r"\b(?:onedrive|sharepoint|microsoft\s+365|office\s+365)\b"), "Microsoft 365"),
]

_SUPPORTED = (
    "GitHub repositories, Google Drive, Slack channels, Notion, Microsoft Teams, uploaded "
    "documents, and web pages"
)


def capability_reply(query: str) -> dict[str, Any] | None:
    """Answer a question about what MemoryWorks can bring in, or return None."""
    text = _normalize(query)
    if not text or len(text.split()) > 30:
        return None
    about_product = _ASKS_US.search(text) or (
        _ASKS_HOW.search(text) and _NAMES_PRODUCT.search(text)
    )
    if not (about_product and _ACTION.search(text)):
        return None
    for pattern, name in _NOT_YET:
        if pattern.search(text):
            return _reply(
                f"Not yet. {name} isn't available to connect today. MemoryWorks can bring in "
                f"{_SUPPORTED} now; you can export from {name.split(' (')[0]} and upload the "
                "files on **Add knowledge → Upload documents** in the meantime."
            )
    for pattern, answer in _LIVE:
        if pattern.search(text):
            # "How do I…?" wants the steps, not a yes.
            return _reply(answer if _ASKS_US.search(text) else answer.removeprefix("Yes. "))
    return None
