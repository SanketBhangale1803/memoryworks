"""Recognise "import this" when it is typed into the chat.

The chat is the one place people talk to MemoryWorks, so "import the docs from
my Google Drive" has to import them rather than explain how importing works.

Recognition is deterministic on purpose. Importing writes to company memory,
so it should only happen on an unmistakable request: an import verb said as
an instruction, plus a source that can be named. A question about importing
("how do I import from Drive?") is a question, and goes to the answer path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Politeness and framing in front of the instruction itself.
_PREFIX = re.compile(
    r"^(?:(?:hey|hi|ok|okay|so|now|alright)\b[,!.]?\s*)*"
    r"(?:please\s+)?"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?"
    r"(?:(?:i\s+want|i'?d\s+like|i\s+need)\s+(?:you\s+)?to\s+|let'?s\s+|help\s+me\s+)?"
    r"(?:go\s+ahead\s+and\s+)?(?:please\s+)?",
    re.IGNORECASE,
)
_VERB = re.compile(
    r"^(?:import|ingest|index|add|sync|pull(?:\s+in)?|bring(?:\s+in)?|load|learn(?:\s+from)?"
    r"|remember)\b",
    re.IGNORECASE,
)
# Asking about importing, not asking for it.
_QUESTION = re.compile(
    r"^(?:how|what|why|where|when|which|who|is|are|does|do|did|should|can\s+(?:i|we)"
    r"|could\s+(?:i|we)|is\s+it)\b",
    re.IGNORECASE,
)

_GITHUB_URL = re.compile(r"https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)", re.IGNORECASE)
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)
_SLUG = re.compile(r"(?<![\w/.-])([A-Za-z0-9][\w.-]*/[\w.-]+)(?![\w/])")
_ALL_REPOS = re.compile(
    r"\ball\s+(?:of\s+)?(?:my|our|the)?\s*(?:github\s+)?(?:repos|repositories)\b", re.IGNORECASE
)
_REPO_WORD = re.compile(r"\b(?:repo|repos|repository|repositories|github)\b", re.IGNORECASE)
# "Drive" alone is too common a word ("add our drive policy"); it has to be
# named as a place things come from.
_DRIVE = re.compile(
    r"\b(?:google\s+drive|gdrive|google\s+docs|my\s+drive"
    r"|(?:from|in|on|out\s+of)\s+(?:my\s+|our\s+|the\s+)?drive)\b",
    re.IGNORECASE,
)
_QUOTED = re.compile(r"[\"“”'‘’]([^\"“”'‘’]{2,120})[\"“”'‘’]")
_NAMED = re.compile(
    r"\b(?:named|called|titled|with\s+(?:the\s+)?name)\s+(.{2,120}?)(?:\s+(?:from|in|on|into)\b|$)",
    re.IGNORECASE,
)
_OTHER_SOURCES = {
    "slack": re.compile(r"\bslack\b", re.IGNORECASE),
    "notion": re.compile(r"\bnotion\b", re.IGNORECASE),
    "teams": re.compile(r"\b(?:microsoft\s+)?teams\b", re.IGNORECASE),
}

# What a person means by the kind of Drive file they name.
DRIVE_KINDS: dict[str, tuple[str, ...]] = {
    "documents": (
        "application/vnd.google-apps.document",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
        "text/plain",
        "text/markdown",
    ),
    "spreadsheets": (
        "application/vnd.google-apps.spreadsheet",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/csv",
    ),
    "presentations": (
        "application/vnd.google-apps.presentation",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ),
}
_KIND_WORDS = {
    "documents": re.compile(r"\b(?:docs|documents|doc|pdfs?)\b", re.IGNORECASE),
    "spreadsheets": re.compile(r"\b(?:sheets|spreadsheets?)\b", re.IGNORECASE),
    "presentations": re.compile(r"\b(?:slides|decks?|presentations?)\b", re.IGNORECASE),
}


@dataclass(frozen=True)
class ImportIntent:
    """What the person asked to import.

    ``source`` is ``github`` (one repository), ``github_all``, ``website``,
    ``google_drive``, or another named provider that chat cannot import from
    yet. ``target`` is the repository slug, URL, or Drive name filter.
    ``kinds`` narrows a Drive import to documents, spreadsheets, or slides.
    """

    source: str
    target: str = ""
    kinds: tuple[str, ...] = ()


def parse_import(text: str) -> ImportIntent | None:
    message = " ".join(str(text or "").split())
    if not message or len(message) > 500 or _QUESTION.match(message):
        return None
    instruction = _PREFIX.sub("", message, count=1)
    if not _VERB.match(instruction):
        return None
    # "add" and "remember" are also how people state a fact ("remember that we
    # deploy on Fridays"); only a nameable source makes them an import.
    if match := _GITHUB_URL.search(instruction):
        return ImportIntent("github", f"{match.group(1)}/{match.group(2).removesuffix('.git')}")
    if _ALL_REPOS.search(instruction):
        return ImportIntent("github_all")
    if match := _URL.search(instruction):
        url = match.group(0).rstrip(".,;:!?)")
        return ImportIntent("website", url if "://" in url else f"https://{url}")
    if _REPO_WORD.search(instruction) and (match := _SLUG.search(instruction)):
        return ImportIntent("github", match.group(1).removesuffix(".git"))
    if _DRIVE.search(instruction):
        return ImportIntent("google_drive", _drive_name(instruction), _drive_kinds(instruction))
    for provider, pattern in _OTHER_SOURCES.items():
        if pattern.search(instruction):
            return ImportIntent(provider)
    return None


def _drive_name(instruction: str) -> str:
    if match := _QUOTED.search(instruction):
        return match.group(1).strip()
    if match := _NAMED.search(instruction):
        return match.group(1).strip(" .,'\"")
    return ""


def _drive_kinds(instruction: str) -> tuple[str, ...]:
    # Asking for "the docs from Drive" usually means everything, not only Google
    # Docs — unless another kind is named next to it.
    named = tuple(kind for kind, pattern in _KIND_WORDS.items() if pattern.search(instruction))
    return named if named and named != ("documents",) else ()
