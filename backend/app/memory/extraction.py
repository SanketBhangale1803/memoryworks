from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

MemoryCandidate = tuple[str, str, str, float]

PROSE_SOURCE_TYPES = {
    "doc",
    "document",
    "github_issue",
    "github_commit",
    # Postmortems and incident reports are the memory a pre-change briefing
    # most needs; without this they were ingested but never promoted.
    "incident",
    "pull_request",
    "report",
    "slack",
    "slack_export",
    "text",
    "upload",
}
CODE_SUFFIXES = {
    ".css",
    ".go",
    ".html",
    ".java",
    ".js",
    ".jsx",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".svelte",
    ".ts",
    ".tsx",
    ".vue",
}
DOC_SUFFIXES = {".md", ".markdown", ".rst", ".txt"}
CONFIG_NAMES = {
    ".env.example",
    "cargo.toml",
    "docker-compose.yaml",
    "docker-compose.yml",
    "go.mod",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
}

SERVICE_NAME_RE = re.compile(
    r"\b([a-z][a-z0-9]*(?:(?:[-_][a-z0-9]+)*[-_])?(?:"
    r"service|gateway|worker|engine|ledger|client|parser|api))\b",
    re.I,
)
ENDPOINT_RE = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+" r"(/[A-Za-z0-9_{}:./?=&-]*)",
    re.I,
)
PY_ROUTE_RE = re.compile(
    r"@(?:app|router)\.(get|post|put|patch|delete|options|head)" r"\(\s*[\"']([^\"']+)[\"']",
    re.I,
)
JS_ROUTE_RE = re.compile(
    r"\b(?:app|router)\.(get|post|put|patch|delete|options|head)" r"\(\s*[\"']([^\"']+)[\"']",
    re.I,
)


def extract_atomic_memories(
    title: str,
    content: str,
    source_type: str,
    metadata: dict[str, Any],
) -> list[MemoryCandidate]:
    """Extract only source-backed statements that are safe to promote.

    Repository code is interpreted structurally. It is never split into
    arbitrary "sentences", which previously promoted CSS, JSX, validation
    errors, and incomplete expressions into company policy.
    """

    path = str(metadata.get("path") or title)
    name = Path(path).name.casefold()
    suffix = Path(path).suffix.casefold()
    source_type = source_type.casefold()
    candidates: list[MemoryCandidate] = []

    if source_type == "repository_metadata":
        candidates.extend(_repository_metadata(content))
    elif source_type == "repo_file":
        if suffix in DOC_SUFFIXES:
            candidates.extend(_markdown_structure(content))
            candidates.extend(_prose_candidates(content))
        elif name in CONFIG_NAMES or suffix in {".json", ".toml", ".yaml", ".yml"}:
            candidates.extend(_configuration_candidates(name, content))
        elif suffix in CODE_SUFFIXES:
            candidates.extend(_code_candidates(path, content))
    elif source_type in PROSE_SOURCE_TYPES:
        candidates.extend(_markdown_structure(content))
        candidates.extend(_prose_candidates(content))

    return _deduplicate(candidates)[:40]


def _repository_metadata(content: str) -> list[MemoryCandidate]:
    values: dict[str, str] = {}
    for line in content.splitlines():
        key, separator, value = line.partition(":")
        if separator and value.strip():
            values[key.strip().casefold()] = value.strip()
    repository = values.get("repository") or values.get("name") or "repository"
    output: list[MemoryCandidate] = []
    if description := values.get("description"):
        output.append(("fact", repository, f"{repository}: {description}", 0.92))
    if language := values.get("primary language"):
        output.append(
            ("fact", f"{repository} technology", f"{repository} primarily uses {language}.", 0.96)
        )
    if branch := values.get("default branch"):
        output.append(
            (
                "config",
                f"{repository} default branch",
                f"{repository}'s default branch is {branch}.",
                0.98,
            )
        )
    if owner := values.get("owner"):
        output.append(("ownership", repository, f"{repository} is owned by {owner}.", 0.96))
    if sha := values.get("latest commit sha"):
        message = values.get("latest commit message", "")
        statement = f"The latest indexed commit for {repository} is {sha}"
        if message:
            statement += f" ({message})"
        output.append(("fact", f"{repository} latest commit", statement + ".", 0.98))
    return output


def _markdown_structure(content: str) -> list[MemoryCandidate]:
    output: list[MemoryCandidate] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        endpoint = ENDPOINT_RE.search(line.replace("`", ""))
        if endpoint:
            method, path = endpoint.groups()
            detail = _after_dash(line)
            statement = f"{method.upper()} {path} is an API endpoint"
            if detail:
                statement += f" for {detail.rstrip('.')}."
            else:
                statement += "."
            output.append(("fact", f"{method.upper()} {path}", statement, 0.96))
        if line.startswith("|") and line.endswith("|"):
            cells = [cell.strip().strip("`") for cell in line.strip("|").split("|")]
            if (
                len(cells) >= 3
                and cells[0]
                and not set(cells[0]) <= {"-", ":"}
                and cells[0].casefold() not in {"service", "module", "name"}
                and SERVICE_NAME_RE.fullmatch(cells[0])
            ):
                statement = (
                    f"{cells[0]} is implemented in {cells[1]} and is responsible for "
                    f"{cells[2].rstrip('.')}."
                )
                output.append(("fact", cells[0].casefold(), statement, 0.96))
    return output


def _configuration_candidates(name: str, content: str) -> list[MemoryCandidate]:
    output: list[MemoryCandidate] = []
    if name == "package.json":
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return []
        for section in ("dependencies", "devDependencies"):
            for dependency, version in sorted((payload.get(section) or {}).items()):
                output.append(
                    (
                        "dependency",
                        dependency.casefold(),
                        f"The project depends on {dependency} ({version}).",
                        0.98,
                    )
                )
        scripts = payload.get("scripts") or {}
        for script_name in ("build", "dev", "start", "test"):
            if command := scripts.get(script_name):
                output.append(
                    (
                        "procedure",
                        f"npm {script_name}",
                        f"The npm {script_name} script runs `{command}`.",
                        0.96,
                    )
                )
        return output
    if name == "requirements.txt":
        for raw in content.splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", "-", "git+")):
                continue
            dependency = re.split(r"[<>=!~;\[]", line, maxsplit=1)[0].strip()
            if dependency:
                output.append(
                    (
                        "dependency",
                        dependency.casefold(),
                        f"The Python service depends on {line}.",
                        0.98,
                    )
                )
        return output
    if name == ".env.example":
        for raw in content.splitlines():
            match = re.match(r"^\s*([A-Z][A-Z0-9_]{2,})\s*=", raw)
            if match:
                variable = match.group(1)
                output.append(
                    (
                        "config",
                        variable.casefold(),
                        f"{variable} is a supported configuration variable.",
                        0.96,
                    )
                )
        return output
    return _compose_dependencies(content) if name.startswith(("compose", "docker-compose")) else []


def _compose_dependencies(content: str) -> list[MemoryCandidate]:
    output: list[MemoryCandidate] = []
    current = ""
    in_services = False
    in_depends = False
    for raw in content.splitlines():
        if re.match(r"^services:\s*$", raw):
            in_services = True
            continue
        service = re.match(r"^\s{2}([A-Za-z][\w-]+):\s*$", raw) if in_services else None
        if service:
            current = service.group(1)
            output.append(
                ("fact", current.casefold(), f"{current} is a Docker Compose service.", 0.98)
            )
            in_depends = False
            continue
        if current and re.match(r"^\s{4}depends_on:\s*$", raw):
            in_depends = True
            continue
        dependency = re.match(r"^\s{6}(?:-\s*)?([A-Za-z][\w-]+)(?::.*)?$", raw)
        if in_depends and dependency:
            target = dependency.group(1)
            output.append(
                (
                    "dependency",
                    current.casefold(),
                    f"{current} depends on {target}.",
                    0.98,
                )
            )
        elif raw.strip() and len(raw) - len(raw.lstrip()) <= 4:
            in_depends = False
    return output


def _code_candidates(path: str, content: str) -> list[MemoryCandidate]:
    output: list[MemoryCandidate] = []
    for method, route in [*PY_ROUTE_RE.findall(content), *JS_ROUTE_RE.findall(content)]:
        output.append(
            (
                "fact",
                f"{method.upper()} {route}",
                f"{method.upper()} {route} is implemented in {path}.",
                0.98,
            )
        )
    declared_services = re.findall(
        r"""(?im)^\s*(?:SERVICE_NAME|APP_NAME|WORKER_NAME)\s*(?::[^=]+)?=\s*
        ["']([^"']+)["']""",
        content,
        re.X,
    )
    for service in sorted(set(declared_services)):
        if not SERVICE_NAME_RE.fullmatch(service):
            continue
        output.append(
            (
                "fact",
                service.casefold(),
                f"{service} is declared by {path}.",
                0.94,
            )
        )
    if path.casefold().endswith(".py"):
        output.extend(_python_docstrings(path, content))
    return output


def _python_docstrings(path: str, content: str) -> list[MemoryCandidate]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []
    output: list[MemoryCandidate] = []
    module_doc = ast.get_docstring(tree, clean=True)
    if module_doc:
        for candidate in _prose_candidates(module_doc):
            output.append((candidate[0], candidate[1], candidate[2], min(candidate[3], 0.9)))
    for node in tree.body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            doc = ast.get_docstring(node, clean=True)
            if not doc:
                continue
            first = " ".join(doc.split()).split(". ", 1)[0].rstrip(".")
            if 18 <= len(first) <= 300 and not _looks_like_code(first):
                output.append(
                    (
                        "fact",
                        f"{Path(path).name}:{node.name}".casefold(),
                        f"{node.name} in {path} {first[0].lower() + first[1:]}.",
                        0.86,
                    )
                )
    return output


def _prose_candidates(content: str) -> list[MemoryCandidate]:
    output: list[MemoryCandidate] = []
    for raw in re.split(r"(?<=[.!?])\s+|\n+", content):
        text = re.sub(r"^[-*#>\s]+", "", raw).strip().strip("`")
        lower = text.casefold()
        if (
            len(text) < 18
            or len(text) > 600
            or text.endswith(":")
            or text.startswith("|")
            or ENDPOINT_RE.search(text)
            or re.search(r"\b(demo|fixture|placeholder|example only|sample data)\b", lower)
            or _looks_like_code(text)
        ):
            continue
        kind, confidence = "", 0.0
        if re.search(r"\b(decided|decision|we will|should|must use|use .+ instead)\b", lower):
            kind, confidence = "decision", 0.92
        elif re.search(r"\b(must|required|prohibited|only allowed|may not)\b", lower) or re.match(
            r"^(?:company\s+)?policy\s*:", lower
        ):
            kind, confidence = "policy", 0.9
        elif re.search(r"\b(owner|owns|owned by|maintained by|responsible for)\b", lower):
            kind, confidence = "ownership", 0.9
        elif re.search(r"\b(depends on|connects to|uses kafka|uses redis)\b", lower):
            kind, confidence = "dependency", 0.88
        elif re.search(r"\b(run|install|deploy|start|configure)\b", lower) and "`" in raw:
            kind, confidence = "procedure", 0.84
        elif re.search(
            r"\b(fail(?:ed|s|ures?)?|outages?|incidents?|time(?:outs?|d out)|crash(?:ed|es)?)\b",
            lower,
        ):
            kind, confidence = "incident", 0.86
        elif re.search(
            r"\b(prototype|application|app|service|tool)\b.{0,100}"
            r"\b(turns|provides|manages|handles|creates|generates)\b",
            lower,
        ):
            kind, confidence = "fact", 0.88
        elif re.search(r"\b(is|are|uses|built with|powered by|defaults to|listens on)\b", lower):
            kind, confidence = "fact", 0.82
        if kind:
            output.append((kind, _subject(text), text, confidence))
    return output


def _looks_like_code(text: str) -> bool:
    lowered = text.casefold()
    if re.match(
        r"^(?:if|elif|else|for|while|return|raise|def|class|const|let|var|function|"
        r"import|from|export|set[A-Z_]|[.#][\w-]+\s*\{)",
        text,
    ):
        return True
    if any(marker in text for marker in ("=>", "</", "/>", "className=", "self.", "});", "])")):
        return True
    if text.count("{") != text.count("}") or text.count("(") != text.count(")"):
        return True
    if re.match(r"^[\w-]+\s*:\s*[^:]+;?$", text):
        return True
    if lowered.startswith(("http ", "write_json(", "seterror(", "valueerror(")):
        return True
    return bool(re.search(r"[;{}]\s*$", text))


def _subject(text: str) -> str:
    subject = re.split(
        r"\b(?:is|are|uses|should|must|depends|failed|was|will)\b",
        text,
        maxsplit=1,
        flags=re.I,
    )[0].strip(" :-`\"'")
    return (subject or "source statement").casefold()[:120]


def _after_dash(line: str) -> str:
    parts = re.split(r"\s+[—-]\s+", line, maxsplit=1)
    return re.sub(r"\[[^\]]+\]\([^)]*\)", "", parts[1]).strip() if len(parts) == 2 else ""


def _deduplicate(candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
    output: list[MemoryCandidate] = []
    seen: set[tuple[str, str]] = set()
    for kind, subject, content, confidence in candidates:
        key = (kind, " ".join(content.casefold().split()))
        if key in seen:
            continue
        seen.add(key)
        output.append((kind, subject, content, confidence))
    return output
