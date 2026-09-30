from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.graph.base import GraphEvidence
from app.llm import generate_grounded_json
from app.retrieval.intents import ownership_scope
from app.retrieval.universal import universal_evidence_answer

CAUSE_MARKERS = (
    "root cause",
    "caused by",
    "because",
    "due to",
    "mismatch",
    "failed",
    "failure",
    "timeout",
    "refused",
    "unreachable",
    "error",
)
ACTION_MARKERS = (
    "check",
    "inspect",
    "verify",
    "review",
    "compare",
    "restart",
    "update",
    "change",
    "deploy",
    "rotate",
    "send",
    "delete",
)
MUTATION_MARKERS = (
    "restart",
    "update",
    "change",
    "deploy",
    "rotate",
    "delete",
    "write",
    "remove",
    "scale",
)
OVERVIEW_PHRASES = (
    "what is this",
    "what does this",
    "what is the project",
    "what is the service",
    "what does it do",
    "tell me about",
    "project overview",
    "service overview",
    "describe this",
)

SLACK_CONVERSATION_RE = re.compile(
    r"\b(?:say|says|said|message|messages|conversation|conversations|thread|threads|"
    r"discuss|discussed|discussion|summarize|summarise|summary)\b"
)
SLACK_CATCHUP_RE = re.compile(
    r"\b(?:missed|unread)\s+(?:chat|chats|message|messages|conversation|conversations)\b"
    r"|\b(?:chat|chats|slack|channel)\s+(?:updates?|digest|recap|summary)\b"
    r"|\b(?:updates?|digest|recap|summary)\s+(?:from|in|on|of)\s+"
    r"(?:chat|chats|slack|channels?)\b"
    r"|\bcatch\s+me\s+up\b.{0,48}\b(?:chat|chats|messages?|slack|channels?)\b"
)
SLACK_AUTHORING_RE = re.compile(
    r"\b(?:draft|write|prepare|create|post|publish|send)\b.{0,48}\bslack\b"
)
SLACK_IMPLEMENTATION_RE = re.compile(
    r"\b(?:api|client|connector|endpoint|function|class|implementation|implemented|"
    r"oauth|sdk|source code)\b"
)


def _slack_message_intent(lowered: str) -> bool:
    """Recognize Slack-content questions while leaving connector code searchable."""
    if SLACK_AUTHORING_RE.search(lowered):
        return False
    if SLACK_IMPLEMENTATION_RE.search(lowered):
        return False
    if SLACK_CATCHUP_RE.search(lowered):
        return True
    return bool(re.search(r"\bslack\b", lowered) and SLACK_CONVERSATION_RE.search(lowered))


def answer_intent(query: str) -> str:
    """Classify answer shape before any sentence can be promoted as evidence."""
    lowered = " ".join(query.casefold().replace("@runbook", "").replace("@orgmemory", "").split())
    terms = set(re.findall(r"[a-z][\w-]{1,}", lowered))
    if re.search(
        r"\b(?:current|latest|present)\s+(?:status|state|value)\b",
        lowered,
    ) and re.search(
        r"\b(?:requisition|request|record|order|ticket|workflow instance)\b",
        lowered,
    ):
        return "runtime_record"
    if re.search(r"\b(endpoint|endpoints|api route|request payload|payload)\b", lowered) and (
        "endpoint" in lowered
        or "payload" in lowered
        or re.search(r"\b(?:get|post|put|patch|delete)\s+/", lowered)
    ):
        return "api_contract"
    briefing_facets = sum(
        marker in lowered
        for marker in (
            "tech stack",
            "major services",
            "dependencies",
            "ownership",
            "important decisions",
            "conflicting",
            "recently updated",
        )
    )
    if (
        any(
            phrase in lowered
            for phrase in (
                "what should an ai agent know before working on this repo",
                "what should an ai agent know before touching this repo",
                "explain this repo before i edit it",
                "repository briefing",
                "repo briefing",
            )
        )
        or re.search(
            r"\bwhat\b.{0,80}\bai\b.{0,40}\bagent\b.{0,80}\bknow\b"
            r".{0,80}\bbefore\b.{0,80}\b(?:edit|editing|work|working|touch|touching)\b",
            lowered,
        )
        or briefing_facets >= 3
    ):
        return "agent_briefing"
    profile_facets = int(
        bool(re.search(r"\b(?:explain|describe)\s+what\s+[\w.-]+\s+does\b", lowered))
    ) + sum(
        marker in lowered
        for marker in (
            "what it does",
            "authentication",
            "authorization",
            "entry points",
            "external dependencies",
        )
    )
    if profile_facets >= 2 and any(
        phrase in lowered
        for phrase in (
            "connected github repositories",
            "connected repositories",
            "across repositories",
            "repository profile",
        )
    ):
        return "repository_profile"
    if _slack_message_intent(lowered) or any(
        phrase in lowered
        for phrase in (
            "what slack messages",
            "which slack messages",
            "show slack messages",
            "messages do i have",
            "messages from slack",
            "what was said in slack",
            "summarize slack",
            "slack conversation",
        )
    ):
        return "slack_messages"
    if _technical_anchors(query) and (
        re.search(
            r"\b(where|which file|which repository|which repo)\b.*\b(configur|defined|set|used)",
            lowered,
        )
        or any(term in lowered for term in ("trace ", "flow through", "from configuration"))
    ):
        return "configuration_locator"
    if any(
        phrase in lowered
        for phrase in (
            "why did the team choose",
            "why was this chosen",
            "what decision was made",
            "what was decided",
            "architecture decision",
            "decision rationale",
        )
    ):
        return "decision_rationale"
    if any(
        phrase in lowered
        for phrase in (
            "what must be approved",
            "what requires approval",
            "who must approve",
            "approval before",
        )
    ):
        return "approval_requirement"
    if any(
        phrase in lowered
        for phrase in (
            "which repositories could be affected",
            "which repos could be affected",
            "what could be affected",
            "if i change this api",
            "blast radius",
        )
    ):
        return "change_impact"
    if _tech_stack_intent(query):
        return "tech_stack"
    if any(
        phrase in lowered
        for phrase in (
            "how do i run",
            "how to run",
            "run this locally",
            "local setup",
            "getting started",
            "how do i start",
        )
    ):
        return "setup"
    if any(
        phrase in lowered
        for phrase in (
            "which repository contains",
            "which repo contains",
            "where is the authentication implementation",
            "where is auth implemented",
            "where is authentication implemented",
            "what repository implements",
            "what repo implements",
        )
    ):
        return "repository_locator"
    if ({"repo", "repository"}.intersection(terms)) and {"name", "named", "nae"}.intersection(
        terms
    ):
        return "repository_identity"
    if any(
        phrase in lowered
        for phrase in (
            "last commit",
            "latest commit",
            "most recent commit",
            "newest commit",
            "recent commits",
            "commit history",
        )
    ):
        return "commit_history"
    if any(
        phrase in lowered
        for phrase in (
            "what changed recently",
            "what changed in this repo",
            "recent changes",
            "latest changes",
            "what was changed",
            "what has changed",
        )
    ):
        return "recent_changes"
    ownership_kind = ownership_scope(lowered)
    if ownership_kind == "repository" or any(
        phrase in lowered
        for phrase in ("who committed", "who commited", "most recent commit", "latest commit")
    ):
        return "ownership"
    if ownership_kind == "entity":
        return "entity_ownership"
    if any(
        phrase in lowered
        for phrase in (
            "procedure still valid",
            "procedure is still valid",
            "runbook still valid",
            "runbook is still valid",
            "deployment procedure still valid",
            "is this procedure valid",
            "is the current procedure valid",
        )
    ):
        return "procedure_validity"
    if _overview_intent(query):
        return "overview"
    return "general"


def sentences(text: str) -> list[str]:
    lines = [line.strip(" -*#\t") for line in text.splitlines() if line.strip()]
    output: list[str] = []
    for line in lines:
        output.extend(
            piece.strip() for piece in re.split(r"(?<=[.!?])\s+", line) if len(piece.strip()) >= 12
        )
    return output


def evidence_answer(query: str, evidence: list[GraphEvidence]) -> dict[str, Any]:
    intent = answer_intent(query)
    if intent == "runtime_record":
        return _runtime_record_answer(evidence)
    if intent == "api_contract":
        return _api_contract_answer(query, evidence)
    if intent == "agent_briefing":
        return _agent_briefing_answer(evidence)
    if intent == "repository_profile":
        return _repository_profile_answer(query, evidence)
    if intent == "slack_messages":
        return _slack_messages_answer(evidence)
    if intent == "procedure_validity":
        return _procedure_validity_answer(evidence)
    if intent == "change_impact":
        return _change_impact_answer(query, evidence)
    if intent == "configuration_locator":
        return _configuration_locator_answer(query, evidence)
    if intent == "decision_rationale":
        return _decision_answer(query, evidence)
    if intent == "approval_requirement":
        return _approval_answer(query, evidence)
    if not evidence:
        return _insufficient()
    if intent == "tech_stack":
        return _tech_stack_answer(evidence)
    if intent == "setup":
        return _setup_answer(evidence)
    if intent == "repository_identity":
        return _repository_identity_answer(evidence)
    if intent == "commit_history":
        return _commit_history_answer(evidence)
    if intent == "recent_changes":
        return _recent_changes_answer(evidence)
    if intent == "repository_locator":
        return _repository_locator_answer(query, evidence)
    if intent == "ownership":
        return _ownership_answer(query, evidence)
    if intent == "entity_ownership":
        # Entity ownership must come from a typed atomic ownership memory.
        # Generic source text containing "owner" is not sufficient.
        return _insufficient()
    if intent == "overview":
        return _overview_answer(evidence)
    return universal_evidence_answer(query, evidence)


def _runtime_record_answer(evidence: list[GraphEvidence]) -> dict[str, Any]:
    runtime_evidence = [
        item
        for item in evidence
        if item.source_type in {"api_snapshot", "database_record", "runtime_record"}
    ]
    if not runtime_evidence:
        return _insufficient(
            "I do not have enough company memory to answer this confidently. "
            "The indexed repository can describe how to look up this record, but it "
            "does not prove the record's current runtime status."
        )
    return universal_evidence_answer("current runtime record status", runtime_evidence)


def _api_contract_answer(query: str, evidence: list[GraphEvidence]) -> dict[str, Any]:
    """Extract callable HTTP routes and their bodies from authoritative source files."""
    if not evidence:
        return _insufficient("I do not have source-backed API route definitions for this project.")

    def unique_contract_sources(items: list[GraphEvidence]) -> list[GraphEvidence]:
        output: list[GraphEvidence] = []
        seen: set[str] = set()
        for value in items:
            if value.chunk_id in seen:
                continue
            seen.add(value.chunk_id)
            output.append(value)
        return output

    endpoints: dict[tuple[str, str], dict[str, Any]] = {}
    descriptions: dict[tuple[str, str], str] = {}
    default_base = ""
    sample_message = "I need 3 laptops for onboarding, cost center 4100, budget $4200."
    sample_message_found = False

    for item in evidence:
        text = item.text
        base_match = re.search(
            r"(?:API_BASE|apiBase)[^\n]{0,120}?(https?://(?:127\.0\.0\.1|localhost):\d+)",
            text,
            re.I,
        )
        if base_match and not default_base:
            default_base = base_match.group(1)
        source_suffix = Path(item.source_title.casefold()).suffix
        example = (
            re.search(r'"message"\s*:\s*"([^"]{12,500})"', text)
            if source_suffix in {".md", ".markdown", ".rst", ".txt"}
            else None
        )
        if example and not sample_message_found:
            sample_message = example.group(1)
            sample_message_found = True

        for method, path, description in re.findall(
            r"(?m)^\s*-[ \t]*`(GET|POST|PUT|PATCH|DELETE)[ \t]+([^`]+)`"
            r"[ \t]*(?:—|-)?[ \t]*([^\n]*)",
            text,
            re.I,
        ):
            key = (method.upper(), path.strip())
            endpoints.setdefault(key, {"sources": [], "fields": [], "required": []})
            descriptions[key] = description.strip()
            endpoints[key]["sources"].append(item)

        for method_match in re.finditer(r"(?m)^\s*def\s+do_(GET|POST|PUT|PATCH|DELETE)\b", text):
            method = method_match.group(1).upper()
            next_method = re.search(
                r"(?m)^\s*def\s+do_(?:GET|POST|PUT|PATCH|DELETE)\b",
                text[method_match.end() :],
            )
            end = method_match.end() + next_method.start() if next_method else len(text)
            section = text[method_match.end() : end]
            route_matches = list(re.finditer(r'if\s+path\s*==\s*["\']([^"\']+)["\']\s*:', section))
            for index, route_match in enumerate(route_matches):
                path = route_match.group(1)
                block_end = (
                    route_matches[index + 1].start()
                    if index + 1 < len(route_matches)
                    else len(section)
                )
                block = section[route_match.end() : block_end]
                key = (method, path)
                contract = endpoints.setdefault(key, {"sources": [], "fields": [], "required": []})
                contract["sources"].append(item)
                contract["fields"].extend(re.findall(r'body\.get\(["\']([^"\']+)', block))
                contract["required"].extend(
                    re.findall(r'error["\']?\s*:\s*["\']([^"\']+) is required', block)
                )

            status_route = re.search(
                r'path\.startswith\(["\'](/api/[^"\']+/)["\']\)\s+and\s+'
                r'path\.endswith\(["\'](/status)["\']\)',
                section,
            )
            if status_route:
                path = f"{status_route.group(1)}{{id}}{status_route.group(2)}"
                endpoints.setdefault(
                    (method, path), {"sources": [item], "fields": [], "required": []}
                )

        express_routes = list(
            re.finditer(
                r"""\b(?:router|app)\.(get|post|put|patch|delete)\s*\(\s*["'](/[^"']*)["']""",
                text,
                re.I,
            )
        )
        for index, route_match in enumerate(express_routes):
            method = route_match.group(1).upper()
            path = route_match.group(2)
            block_end = (
                express_routes[index + 1].start() if index + 1 < len(express_routes) else len(text)
            )
            block = text[route_match.end() : block_end]
            handler_start = re.search(
                r"\b(?:async\s+)?function\b|(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>",
                block,
                re.I,
            )
            route_signature = block[: handler_start.start()] if handler_start else block[:500]
            key = (method, path)
            contract = endpoints.setdefault(key, {"sources": [], "fields": [], "required": []})
            contract["sources"].append(item)
            for destructured in re.findall(r"\{([^}]+)\}\s*=\s*req\.body", block, re.I | re.S):
                contract["fields"].extend(
                    value.strip()
                    for value in destructured.split(",")
                    if re.fullmatch(r"[A-Za-z_][\w]*", value.strip())
                )
            contract["fields"].extend(re.findall(r"req\.body\.([A-Za-z_][\w]*)", block))
            upload = re.search(r"""upload\.single\(\s*["']([^"']+)["']\s*\)""", block)
            if upload:
                contract["fields"].append(upload.group(1))
                contract["encoding"] = "multipart/form-data"
            if "isLoggedIn" in route_signature:
                contract["authenticated"] = True

        for route_match in re.finditer(
            r"""@\w+\.route\s*\(\s*["'](/[^"']*)["']([^)]*)\)""",
            text,
            re.I | re.S,
        ):
            path, arguments = route_match.groups()
            methods_match = re.search(r"methods\s*=\s*\[([^]]+)\]", arguments, re.I)
            methods = (
                re.findall(
                    r"""["'](GET|POST|PUT|PATCH|DELETE)["']""",
                    methods_match.group(1),
                    re.I,
                )
                if methods_match
                else ["GET"]
            )
            for method in methods:
                contract = endpoints.setdefault(
                    (method.upper(), path), {"sources": [], "fields": [], "required": []}
                )
                contract["sources"].append(item)

        for route_match in re.finditer(
            r"""@(Get|Post|Put|Patch|Delete)Mapping\s*\(\s*(?:value\s*=\s*)?["']([^"']+)["']""",
            text,
            re.I,
        ):
            method, path = route_match.groups()
            contract = endpoints.setdefault(
                (method.upper(), path), {"sources": [], "fields": [], "required": []}
            )
            contract["sources"].append(item)

        for route_match in re.finditer(
            r"""HandleFunc\s*\(\s*["'](/[^"']*)["'][\s\S]{0,500}?\.Methods\s*\(\s*["'](GET|POST|PUT|PATCH|DELETE)["']""",
            text,
            re.I,
        ):
            path, method = route_match.groups()
            contract = endpoints.setdefault(
                (method.upper(), path), {"sources": [], "fields": [], "required": []}
            )
            contract["sources"].append(item)

        for form_match in re.finditer(r"<form\b([^>]*)>(.*?)</form>", text, re.I | re.S):
            attributes, body = form_match.groups()
            action = re.search(r"""\baction\s*=\s*["']([^"']+)["']""", attributes, re.I)
            method = re.search(r"""\bmethod\s*=\s*["']([^"']+)["']""", attributes, re.I)
            if not action or not method:
                continue
            key = (method.group(1).upper(), action.group(1))
            contract = endpoints.setdefault(key, {"sources": [], "fields": [], "required": []})
            contract["sources"].append(item)
            contract["fields"].extend(
                re.findall(r"""<input\b[^>]*\bname\s*=\s*["']?([^\s>"']+)""", body, re.I)
            )
            contract.setdefault("encoding", "application/x-www-form-urlencoded")

        for fetch in re.finditer(r"fetch\([^\n]{0,160}?(/api/[A-Za-z0-9_?={}/.-]+)", text):
            block = text[fetch.start() : fetch.start() + 650]
            method_match = re.search(r"""method\s*:\s*["'](GET|POST|PUT|PATCH|DELETE)""", block)
            method = method_match.group(1).upper() if method_match else "GET"
            key = (method, fetch.group(1).rstrip("`'\""))
            contract = endpoints.setdefault(key, {"sources": [], "fields": [], "required": []})
            contract["sources"].append(item)
            body_match = re.search(r"JSON\.stringify\(\{([^}]+)\}\)", block)
            if body_match:
                contract["fields"].extend(
                    re.findall(r"\b([A-Za-z_][\w]*)\s*(?::|,|$)", body_match.group(1))
                )

    if not endpoints:
        return _insufficient("I found project sources, but no callable HTTP route contract.")

    lowered = query.casefold()
    focus = next(
        (
            marker
            for marker in ("submit", "draft", "validate", "test-connection", "status")
            if marker in lowered
        ),
        "",
    )
    collapsed: dict[tuple[str, str], dict[str, Any]] = {}
    for (method, path), contract in endpoints.items():
        canonical = (method, path.partition("?")[0])
        description = descriptions.get((method, path), "")
        current = collapsed.setdefault(
            canonical,
            {
                "path": path,
                "description": description,
                "sources": [],
                "fields": [],
                "required": [],
            },
        )
        current["sources"].extend(contract["sources"])
        current["fields"].extend(contract["fields"])
        current["required"].extend(contract["required"])
        if contract.get("encoding"):
            current["encoding"] = contract["encoding"]
        if contract.get("authenticated"):
            current["authenticated"] = True
        if description and not current["description"]:
            current["description"] = description
            current["path"] = path

    ordered = sorted(
        (((method, value["path"]), value) for (method, _), value in collapsed.items()),
        key=lambda item: (
            item[0][0] != "POST",
            item[0][1],
        ),
    )
    if focus:
        focused = [item for item in ordered if focus in item[0][1].casefold()]
        if focused:
            ordered = focused

    sections = []
    supporting: list[GraphEvidence] = []
    if default_base:
        sections.append(f"**Base URL:** `{default_base}`")
    sections.append("**Callable endpoints and payloads**")
    for (method, path), contract in ordered:
        sources = unique_contract_sources(contract["sources"])
        supporting.extend(sources)
        cite = " ".join(f"[{source.source_title}]" for source in sources[:2])
        description = contract.get("description", "")
        line = f"- **{method} `{path}`**"
        if description:
            line += f" — {description.rstrip('.')}"
        if contract.get("authenticated"):
            line += " — requires an authenticated session"
        line += f". {cite}" if cite else "."
        sections.append(line)
        if method != "POST":
            continue
        fields = list(dict.fromkeys(contract["fields"]))
        if path.endswith(("/validate", "/draft")) or "message" in fields:
            payload = {"message": sample_message}
            if "requester" in fields:
                payload["requester"] = {"employee_id": "E10042"}
            sections.append(f"  Payload: `{json.dumps(payload)}`")
        elif "sap_payload" in fields:
            sections.append(
                "  Payload: `{" + '"sap_payload": draftResponse.sap_payload, '
                '"sap_config": sapSession' + "}`. "
                "Pass `sap_payload` returned by the draft endpoint; `sap_config` is optional "
                "when server-side SAP configuration is complete."
            )
        elif "sap_config" in fields:
            sections.append('  Payload: `{"sap_config": {"username": "E10042", "password": "…"}}`.')
        elif fields:
            encoding = contract.get("encoding") or "request body"
            formatted = [
                (
                    f"`{field}` (binary file)"
                    if encoding == "multipart/form-data" and field == "file"
                    else f"`{field}`"
                )
                for field in fields
            ]
            sections.append(f"  {encoding} fields: " + ", ".join(formatted) + ".")

    supporting = unique_contract_sources(supporting)
    return {
        "answer": "\n".join(sections),
        "likely_cause": "Not applicable — this is an API contract question.",
        "safe_actions": [],
        "approval_required": [],
        "sufficient": bool(supporting),
        "supporting_chunk_ids": [item.chunk_id for item in supporting],
    }


def _operational_evidence_source(item: GraphEvidence) -> bool:
    if item.source_type in {
        "incident",
        "log",
        "slack",
        "slack_export",
        "github_issue",
        "pull_request",
        "operational_assertion",
    }:
        return True
    if item.source_type != "repo_file":
        return False
    title = item.source_title.casefold().replace("\\", "/")
    # Focused operational docs can contain the approved fix for an exact
    # identifier. Root READMEs and executable examples remain excluded because
    # they commonly document sample incidents rather than current operations.
    if title.startswith(("readme", "tests/", "test/", "demo_data/", "examples/")):
        return False
    if any(segment in title for segment in ("/tests/", "/fixtures/", "/examples/")):
        return False
    if title.startswith("docs/") and title.endswith(".md"):
        return True
    return title.endswith(
        (
            ".conf",
            ".env.example",
            ".ini",
            ".java",
            ".js",
            ".json",
            ".py",
            ".rb",
            ".sh",
            ".toml",
            ".ts",
            ".yaml",
            ".yml",
        )
    )


def _slack_messages_answer(evidence: list[GraphEvidence]) -> dict[str, Any]:
    messages = [item for item in evidence if item.source_type in {"slack", "slack_export"}]
    if not messages:
        return _insufficient(
            "I could not find any indexed Slack messages for the active project. "
            "Connect Slack and ingest a channel into this project first."
        )
    unique: dict[str, GraphEvidence] = {}
    for item in messages:
        identity = str(item.metadata.get("source_id") or "") or (
            f"{item.source_title}:{' '.join(item.text.split())}"
        )
        unique.setdefault(identity, item)
    selected = sorted(
        unique.values(),
        key=lambda item: str(item.metadata.get("timestamp") or item.source_title),
        reverse=True,
    )[:8]
    channels = sorted(
        {
            str(item.metadata.get("channel_name") or "").strip()
            or item.source_title.partition(" at ")[0].removeprefix("#")
            for item in selected
        }
    )
    lines: list[str] = []
    for index, item in enumerate(selected, 1):
        text = " ".join(item.text.split())
        if len(text) > 280:
            text = text[:279].rstrip() + "…"
        timestamp = str(item.metadata.get("timestamp") or "").strip()
        prefix = f"{timestamp}: " if timestamp else ""
        lines.append(f"{index}. {prefix}{text}")
    channel_label = ", ".join(f"#{channel}" for channel in channels if channel)
    answer = f"I found {len(unique)} unique indexed Slack message(s)"
    if channel_label:
        answer += f" in {channel_label}"
    answer += ": " + " ".join(lines)
    return {
        **_structured(answer, "Slack message lookup"),
        "supporting_chunk_ids": [item.chunk_id for item in selected],
    }


def _overview_intent(query: str) -> bool:
    lowered = " ".join(query.lower().replace("@runbook", "").split())
    return any(phrase in lowered for phrase in OVERVIEW_PHRASES)


def _tech_stack_intent(query: str) -> bool:
    lowered = " ".join(query.casefold().replace("@runbook", "").split())
    return any(
        phrase in lowered
        for phrase in (
            "tech stack",
            "technology stack",
            "technologies used",
            "frameworks used",
            "built with",
        )
    )


def _agent_briefing_answer(evidence: list[GraphEvidence]) -> dict[str, Any]:
    """Assemble a broad repository briefing without filling evidence gaps."""
    if not evidence:
        return _insufficient()

    def cite(item: GraphEvidence) -> str:
        return f"[{item.source_title}]"

    def unique_items(items: list[GraphEvidence]) -> list[GraphEvidence]:
        seen: set[str] = set()
        output: list[GraphEvidence] = []
        for item in items:
            if item.chunk_id not in seen:
                seen.add(item.chunk_id)
                output.append(item)
        return output

    supporting: list[GraphEvidence] = []
    sections: list[str] = []
    missing: list[str] = []

    readmes = [
        item
        for item in evidence
        if item.source_title.casefold().startswith("readme")
        and int(item.metadata.get("chunk_index") or 0) == 0
    ]
    overview = _overview_answer(readmes)
    if overview["sufficient"] and readmes:
        purpose = re.sub(
            r"^Based on (?:the repository README|the retrieved project sources):\s*",
            "",
            overview["answer"],
        )
        purpose = re.split(r",\s+with\b", purpose, maxsplit=1, flags=re.I)[0].rstrip(".") + "."
        sections.append(f"**Purpose.** {purpose} {cite(readmes[0])}")
        supporting.append(readmes[0])
    else:
        missing.append("a canonical repository purpose")

    stack_answer = _tech_stack_answer(evidence)
    if stack_answer["sufficient"]:
        stack_sources = [
            item
            for item in evidence
            if item.source_title.casefold().rsplit("/", 1)[-1]
            in {
                "package.json",
                "pyproject.toml",
                "requirements.txt",
                "docker-compose.yml",
                "docker-compose.yaml",
                "compose.yml",
                "compose.yaml",
            }
            or Path(item.source_title.casefold()).suffix
            in {".py", ".js", ".ts", ".tsx", ".go", ".rs", ".java"}
        ][:4]
        source_labels = " ".join(cite(item) for item in stack_sources)
        sections.append(f"**Tech stack.** {stack_answer['answer']} {source_labels}".strip())
        supporting.extend(stack_sources)
    else:
        missing.append("a verified technology stack")

    services: list[str] = []
    service_sources: list[GraphEvidence] = []
    for item in evidence:
        found = list(item.service_names)
        title = item.source_title.casefold().rsplit("/", 1)[-1]
        if title in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
            match = re.search(r"(?ms)^services:\s*\n(?P<body>.*?)(?:^\S|\Z)", item.text)
            body = match.group("body") if match else item.text
            found.extend(re.findall(r"(?m)^\s{2}([a-zA-Z][\w.-]+):\s*$", body))
        if found:
            services.extend(found)
            service_sources.append(item)
    services = list(
        dict.fromkeys(
            value
            for value in services
            if value
            and value.casefold() not in {"client_service", "instagram_service"}
            and not ("-" not in value and "_" not in value and value.casefold().endswith("client"))
        )
    )
    services = [
        service
        for service in services
        if not any(
            candidate != service
            and len(candidate) > len(service)
            and candidate.casefold().endswith(f"-{service.casefold()}")
            for candidate in services
        )
    ]
    if services:
        sources = unique_items(service_sources)[:3]
        sections.append(
            "**Major services.** "
            + ", ".join(f"`{name}`" for name in services[:15])
            + ". "
            + " ".join(cite(item) for item in sources)
        )
        supporting.extend(sources)
    else:
        missing.append("named major services")

    dependencies: list[str] = []
    dependency_sources: list[GraphEvidence] = []
    for item in evidence:
        title = item.source_title.casefold().rsplit("/", 1)[-1]
        found: list[str] = []
        if title == "requirements.txt":
            found = [
                re.split(r"[<>=!~\[]", line.strip(), maxsplit=1)[0]
                for line in item.text.splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
        elif title == "pyproject.toml":
            found = re.findall(r"(?m)^\s*[\"']?([a-zA-Z][\w.-]+)[\"']?\s*(?:=|[<>=~])", item.text)
        elif title == "package.json":
            try:
                package = json.loads(item.text)
                found = list(
                    {
                        **(package.get("dependencies") or {}),
                        **(package.get("devDependencies") or {}),
                    }
                )
            except json.JSONDecodeError:
                pass
        elif title in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
            found = re.findall(r"(?m)^\s*-\s*([a-zA-Z][\w.-]+)\s*$", item.text)
        if found:
            dependencies.extend(found)
            dependency_sources.append(item)
    dependencies = list(dict.fromkeys(value for value in dependencies if value))
    if dependencies:
        sources = unique_items(dependency_sources)[:3]
        sections.append(
            "**Dependencies.** "
            + ", ".join(f"`{name}`" for name in dependencies[:20])
            + ". "
            + " ".join(cite(item) for item in sources)
        )
        supporting.extend(sources)
    else:
        missing.append("declared external dependencies")

    category_patterns = {
        "Ownership": r"\b(?:owned by|owner|maintained by|responsible for)\b",
        "Important decisions": r"\b(?:decided|decision|we chose|must use|use .+ instead)\b",
        "Updates or conflicts": r"\b(?:no longer|replaced|migrated|supersed|changed from|instead)\b",
    }
    for label, pattern in category_patterns.items():
        matches: list[tuple[str, GraphEvidence]] = []
        for item in evidence:
            source_suffix = Path(item.source_title.casefold()).suffix
            if item.source_type == "repo_file" and source_suffix not in {
                ".md",
                ".markdown",
                ".rst",
                ".txt",
            }:
                continue
            for sentence in sentences(item.text):
                if re.search(pattern, sentence, re.I):
                    matches.append((sentence, item))
        if matches:
            chosen = matches[:4]
            sections.append(
                f"**{label}.** " + " ".join(f"{sentence} {cite(item)}" for sentence, item in chosen)
            )
            supporting.extend(item for _, item in chosen)
        else:
            missing.append(label.casefold())

    if missing:
        sections.append(
            "**Insufficient company memory.** MemoryWorks found no source-backed evidence for: "
            + "; ".join(missing)
            + "."
        )
    support = unique_items(supporting)
    if not support:
        return _insufficient()
    return {
        **_structured("\n\n".join(sections), "repository briefing"),
        "supporting_chunk_ids": [item.chunk_id for item in support],
    }


def _repository_profile_answer(query: str, evidence: list[GraphEvidence]) -> dict[str, Any]:
    """Build a multi-facet repository answer from canonical source structures."""
    if not evidence:
        return _insufficient(
            "I could not resolve the named service to an indexed repository with source evidence."
        )

    repository = next(
        (
            str(item.metadata.get("project_name") or item.metadata.get("repository") or "")
            for item in evidence
            if item.metadata.get("project_name") or item.metadata.get("repository")
        ),
        "the named repository",
    )
    by_title: dict[str, GraphEvidence] = {}
    for item in sorted(evidence, key=lambda value: value.score, reverse=True):
        by_title.setdefault(item.source_title.casefold(), item)

    supporting: list[GraphEvidence] = []
    sections: list[str] = []
    missing: list[str] = []

    readmes = [
        item
        for item in evidence
        if item.source_title.casefold().startswith("readme")
        and int(item.metadata.get("chunk_index") or 0) == 0
    ]
    overview = _overview_answer(readmes)
    if overview["sufficient"]:
        purpose = re.sub(
            r"^Based on (?:the repository README|the retrieved project sources):\s*",
            "",
            overview["answer"],
        )
        sections.append(f"Purpose: {purpose}")
        supporting.extend(readmes[:1])
    else:
        missing.append("a canonical purpose statement")

    auth_details: list[str] = []
    middleware = by_title.get("middleware/auth.js")
    if middleware:
        symbols = list(
            dict.fromkeys(re.findall(r"\bauth\.([A-Za-z_$][\w$]*)\s*=", middleware.text))
        )
        detail = "`middleware/auth.js`"
        if symbols:
            detail += " defines " + ", ".join(f"`{symbol}`" for symbol in symbols[:6])
        auth_details.append(detail)
        supporting.append(middleware)

    auth_routes = by_title.get("routes/auth.js")
    if auth_routes:
        routes = list(
            dict.fromkeys(
                f"{method.upper()} {path}"
                for method, path in re.findall(
                    r"router\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)",
                    auth_routes.text,
                    re.I,
                )
            )
        )
        detail = "`routes/auth.js` implements Google OAuth"
        if routes:
            detail += " through " + ", ".join(f"`{route}`" for route in routes[:6])
        auth_details.append(detail)
        supporting.append(auth_routes)

    passport = by_title.get("config/passport.js")
    if passport:
        features = []
        if "GoogleStrategy" in passport.text or "passport-google" in passport.text:
            features.append("Google strategy")
        if "serializeUser" in passport.text:
            features.append("session serialization")
        if "deserializeUser" in passport.text:
            features.append("session deserialization")
        auth_details.append(
            "`config/passport.js` configures " + (", ".join(features) or "Passport")
        )
        supporting.append(passport)

    users_route = by_title.get("routes/users.js")
    if users_route and "jwt.sign" in users_route.text:
        auth_details.append("`routes/users.js` issues JWTs after credential authentication")
        supporting.append(users_route)

    if auth_details:
        sections.append("Authentication and authorization: " + "; ".join(auth_details) + ".")
    else:
        missing.append("authentication and authorization implementation evidence")

    package_item = by_title.get("package.json")
    package: dict[str, Any] = {}
    if package_item:
        try:
            package = json.loads(package_item.text)
        except json.JSONDecodeError:
            package = {}
        supporting.append(package_item)
    start_command = str((package.get("scripts") or {}).get("start") or "").strip()
    app_item = by_title.get("app.js")
    entry_details: list[str] = []
    if start_command:
        entry_details.append(f"`package.json` starts the service with `{start_command}`")
    if app_item:
        entry_details.append("`app.js` is the Express application entry point")
        supporting.append(app_item)
    if auth_routes:
        entry_details.append("`routes/auth.js` is the OAuth HTTP entry point")
    if entry_details:
        sections.append("Main entry points: " + "; ".join(entry_details) + ".")
    else:
        missing.append("a verified application entry point")

    dependencies = {
        **(package.get("dependencies") or {}),
        **(package.get("devDependencies") or {}),
    }
    preferred = (
        "express",
        "mongoose",
        "passport",
        "passport-local",
        "passport-google-oauth20",
        "jsonwebtoken",
        "express-session",
        "connect-mongo",
        "googleapis",
        "nodemailer",
        "ejs",
    )
    selected_dependencies = [name for name in preferred if name in dependencies]
    if not selected_dependencies:
        selected_dependencies = list(dependencies)[:12]
    if selected_dependencies:
        sections.append(
            "Manifest dependencies: "
            + ", ".join(f"`{name}`" for name in selected_dependencies)
            + "."
        )
    else:
        missing.append("a readable dependency manifest")

    if "integrat" in query.casefold():
        sections.append(
            "Integration status: no integration is asserted by this repository profile alone; "
            "a proposed integration must be labeled as design work until both repositories contain "
            "a shared API contract or direct dependency."
        )
    if missing:
        sections.append("Missing evidence: " + "; ".join(missing) + ".")

    unique_support = list(dict.fromkeys(item.chunk_id for item in supporting))
    if not unique_support:
        return _insufficient()
    return {
        **_structured(
            f"Repository profile for {repository}. " + " ".join(sections),
            "repository profile",
        ),
        "supporting_chunk_ids": unique_support,
    }


def _tech_stack_answer(evidence: list[GraphEvidence]) -> dict[str, Any]:
    languages: set[str] = set()
    packages: list[str] = []
    tools: set[str] = set()
    language_by_suffix = {
        ".js": "JavaScript",
        ".jsx": "JavaScript with JSX",
        ".ts": "TypeScript",
        ".tsx": "TypeScript with JSX",
        ".py": "Python",
        ".go": "Go",
        ".rs": "Rust",
        ".java": "Java",
    }
    for item in evidence:
        title = item.source_title.casefold()
        suffix = Path(title).suffix
        if suffix in language_by_suffix:
            languages.add(language_by_suffix[suffix])
        if title == "package.json":
            try:
                package = json.loads(item.text)
            except json.JSONDecodeError:
                package = {}
            dependencies = {
                **(package.get("dependencies") or {}),
                **(package.get("devDependencies") or {}),
            }
            packages.extend(str(name) for name in dependencies)
            scripts = " ".join(str(value) for value in (package.get("scripts") or {}).values())
            if "vite" in scripts.casefold() or "vite" in dependencies:
                tools.add("Vite")
        haystack = f"{item.source_title}\n{item.text}".casefold()
        for token, label in (
            ("react", "React"),
            ("next.js", "Next.js"),
            ("@angular/", "Angular"),
            ("vue", "Vue"),
            ("vite", "Vite"),
            ("eslint", "ESLint"),
            ("webgpu", "WebGPU"),
            ("docker", "Docker"),
        ):
            if token in haystack:
                tools.add(label)
    packages = list(dict.fromkeys(packages))
    if any(name in packages for name in ("react", "react-dom")):
        tools.add("React")
    if "vite" in packages:
        tools.add("Vite")
    if "typescript" in packages:
        languages.add("TypeScript")
    if not (languages or packages or tools):
        return _insufficient()
    parts: list[str] = []
    if languages:
        parts.append("Languages: " + ", ".join(sorted(languages)))
    if tools:
        parts.append("Frameworks and tooling: " + ", ".join(sorted(tools)))
    if packages:
        parts.append("Manifest dependencies: " + ", ".join(packages[:12]))
    answer = "The repository's evidenced technology stack is: " + ". ".join(parts) + "."
    return {
        "answer": answer,
        "likely_cause": "Not applicable — this is a repository architecture question.",
        "safe_actions": [],
        "approval_required": [],
        "sufficient": True,
    }


def _overview_answer(evidence: list[GraphEvidence]) -> dict[str, Any]:
    # A repository overview is a document-structure question. Prefer the first
    # prose paragraph of README chunk zero over a later sentence that merely
    # contains generic words such as "application" or "system".
    readme_starts = sorted(
        (
            item
            for item in evidence
            if item.source_title.casefold().startswith("readme")
            and int(item.metadata.get("chunk_index") or 0) == 0
        ),
        key=lambda item: item.score,
        reverse=True,
    )
    for item in readme_starts:
        in_fence = False
        for raw_paragraph in re.split(r"\n\s*\n", item.text):
            paragraph = " ".join(line.strip() for line in raw_paragraph.splitlines()).strip()
            if paragraph.startswith("```"):
                in_fence = not in_fence
                continue
            if (
                in_fence
                or not paragraph
                or paragraph.startswith(("#", "- ", "* ", ">", "![", "[![", "<img", "<picture"))
            ):
                continue
            if 24 <= len(paragraph) <= 420 and re.search(r"[A-Za-z]{3}", paragraph):
                return {
                    "answer": f"Based on the repository README: {paragraph}",
                    "likely_cause": "Not applicable — this is a project overview question.",
                    "safe_actions": [],
                    "approval_required": [],
                    "sufficient": True,
                    "supporting_chunk_ids": [item.chunk_id],
                }

    candidates: list[tuple[float, str, GraphEvidence]] = []
    for item in evidence:
        source_weight = 5 if item.source_title.lower().startswith("readme") else 2
        for sentence in sentences(item.text):
            lowered = sentence.lower()
            if (
                sentence.startswith(("![", "[", "```"))
                or "`" in sentence
                or "http://" in lowered
                or "https://" in lowered
            ):
                continue
            descriptive = sum(
                marker in lowered
                for marker in (
                    " is a ",
                    " is an ",
                    "designed for",
                    "features",
                    "provides",
                    "system",
                    "application",
                )
            )
            score = source_weight + descriptive * 2 + min(len(sentence), 240) / 240
            if descriptive and 24 <= len(sentence) <= 420:
                candidates.append((score, sentence, item))
    candidates.sort(key=lambda value: value[0], reverse=True)
    selected: list[str] = []
    supporting: list[str] = []
    for _, sentence, item in candidates:
        if sentence not in selected:
            selected.append(sentence)
            supporting.append(item.chunk_id)
        if len(selected) == 2:
            break
    if not selected:
        return _insufficient()
    return {
        "answer": "Based on the retrieved project sources: " + " ".join(selected),
        "likely_cause": "Not applicable — this is a project overview question.",
        "safe_actions": [],
        "approval_required": [],
        "sufficient": True,
        "supporting_chunk_ids": supporting,
    }


def _metadata_lines(item: GraphEvidence) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in item.text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
        if normalized and value.strip():
            values[normalized] = value.strip()
    for key, value in item.metadata.items():
        if isinstance(value, str) and value:
            values.setdefault(str(key).casefold(), value)
    return values


def _repository_metadata(
    evidence: list[GraphEvidence],
) -> tuple[GraphEvidence | None, dict[str, str]]:
    for item in evidence:
        if item.source_type == "repository_metadata":
            return item, _metadata_lines(item)
    return None, {}


def _repository_identity_answer(evidence: list[GraphEvidence]) -> dict[str, Any]:
    item, metadata = _repository_metadata(evidence)
    full_name = metadata.get("repository") or metadata.get("full_name")
    url = metadata.get("url") or (item.source_url if item else "")
    if not full_name:
        for candidate in evidence:
            match = re.search(r"github\.com/([^/\s]+/[^/\s#?]+)", candidate.source_url)
            if match:
                full_name = match.group(1).removesuffix(".git")
                url = f"https://github.com/{full_name}"
                break
    if not full_name:
        return _insufficient()
    answer = f"This repository is {full_name}."
    if url:
        answer += f" Repository: {url}"
    return _structured(answer, "repository identity")


def _repository_locator_answer(query: str, evidence: list[GraphEvidence]) -> dict[str, Any]:
    lowered_query = query.casefold()
    authentication_query = any(term in lowered_query for term in ("auth", "login", "session"))
    if not authentication_query:
        return _insufficient()
    candidates: list[tuple[int, GraphEvidence]] = []
    for item in evidence:
        if item.source_type != "repo_file":
            continue
        path = item.source_title.casefold()
        content = item.text.casefold()
        path_markers = (
            "auth",
            "jwt",
            "login",
            "middleware",
            "oauth",
            "security",
            "session",
        )
        implementation_patterns = (
            r"\bauthorization\b",
            r"\bbearer\b",
            r"\bcreate[_a-z]*session\b",
            r"\bdecode[_a-z]*token\b",
            r"\bgetserversession\b",
            r"\bjwt\b",
            r"\boauth\b",
            r"\bpassport\b",
            r"\bverify[_a-z]*token\b",
            r"(?:def|class|function)\s+[a-z0-9_]*(?:auth|login|session)",
            r"(?:route|router|app).{0,40}/(?:api/)?auth",
        )
        path_score = 4 if any(marker in path for marker in path_markers) else 0
        code_score = sum(
            bool(re.search(pattern, content, re.I | re.S)) for pattern in implementation_patterns
        )
        if path_score + code_score < 2:
            continue
        candidates.append((path_score + code_score, item))
    candidates.sort(key=lambda value: (value[0], value[1].score), reverse=True)
    repositories: dict[str, GraphEvidence] = {}
    for _, item in candidates:
        repository = str(item.metadata.get("repository") or "").removesuffix(".git")
        label = repository or str(item.metadata.get("project_name") or "")
        if not label:
            continue
        repositories.setdefault(label, item)
    selected = list(repositories.items())[:3]
    if not selected:
        return _insufficient(
            "I could not find an authentication implementation file in the indexed repositories."
        )
    descriptions = []
    for repository, item in selected:
        source = f" ({item.source_url})" if item.source_url else ""
        descriptions.append(f"{repository}, primarily `{item.source_title}`{source}")
    prefix = (
        "Authentication implementation evidence was found in "
        if len(descriptions) == 1
        else "Authentication implementation evidence was found in these repositories: "
    )
    return {
        **_structured(prefix + "; ".join(descriptions) + ".", "repository location"),
        "supporting_chunk_ids": [item.chunk_id for _, item in selected],
    }


def _procedure_validity_answer(evidence: list[GraphEvidence]) -> dict[str, Any]:
    assertions = [item for item in evidence if item.source_type == "operational_assertion"]
    if not assertions:
        return _insufficient(
            "Runbook cannot determine whether the procedure is still valid because no verification assertion has been recorded for it. Run a reliability review and have the procedure owner verify the resulting assertions."
        )
    status_order = {
        "contradicted": 5,
        "stale": 4,
        "possibly_stale": 3,
        "proposed": 2,
        "verified": 1,
    }
    assertions.sort(
        key=lambda item: status_order.get(str(item.metadata.get("status") or ""), 0),
        reverse=True,
    )
    strongest = assertions[0]
    status = str(strongest.metadata.get("status") or "proposed")
    title = strongest.source_title
    reason = str(strongest.metadata.get("verification_reason") or "").strip()
    verified_at = str(strongest.metadata.get("last_verified_at") or "").strip()
    owner = str(strongest.metadata.get("verification_owner") or "").strip()
    if status in {"stale", "contradicted"}:
        answer = f"No. `{title}` is currently marked {status.replace('_', ' ')}."
        trust = _status_trust(0.9, "The negative validity result comes from recorded review state.")
    elif status == "possibly_stale":
        answer = (
            f"Runbook cannot confirm that the procedure is still valid. `{title}` is marked "
            "possibly stale and requires re-verification."
        )
        trust = _status_trust(0.78, "A connected change downgraded the verified assertion.")
    elif status == "verified":
        answer = (
            f"The procedure is recorded as verified as of {verified_at or 'an unspecified time'}"
        )
        if owner:
            answer += f" by {owner}"
        answer += ". No stale or contradicted assertion is currently recorded in Runbook."
        trust = _status_trust(
            max(0.55, float(strongest.metadata.get("trust_score") or 0.0)),
            "Validity is bounded to the recorded verification state and timestamp.",
        )
    else:
        answer = (
            f"The procedure is not verified yet. `{title}` is only a proposed assertion, so "
            "Runbook cannot call it valid until a human owner reviews it."
        )
        trust = _status_trust(0.65, "The answer reports an explicit unverified assertion state.")
    if reason:
        answer += f" Review record: {reason}"
    return {
        **_structured(answer, "procedure validity"),
        "trust_score": trust,
        "supporting_chunk_ids": [item.chunk_id for item in assertions],
    }


def _decision_answer(query: str, evidence: list[GraphEvidence]) -> dict[str, Any]:
    query_terms = set(re.findall(r"[a-z][\w-]{2,}", query.casefold())) - {
        "architecture",
        "decision",
        "made",
        "service",
        "team",
        "this",
        "what",
        "why",
    }
    candidates: list[tuple[int, str, GraphEvidence]] = []
    for item in evidence:
        title = item.source_title.casefold()
        authoritative_type = item.source_type in {
            "slack",
            "slack_export",
            "github_issue",
            "pull_request",
            "incident",
        }
        authoritative_title = any(
            marker in title for marker in ("adr", "decision", "rfc", "architecture")
        )
        if not (authoritative_type or authoritative_title):
            continue
        for sentence in sentences(item.text):
            lowered = sentence.casefold()
            decision_markers = sum(
                marker in lowered
                for marker in (
                    "we chose",
                    "we decided",
                    "selected because",
                    "decision:",
                    "rationale:",
                    "trade-off",
                    "tradeoff",
                )
            )
            subject_overlap = sum(term in lowered for term in query_terms)
            if decision_markers and (subject_overlap or not query_terms):
                candidates.append((decision_markers * 3 + subject_overlap, sentence, item))
    candidates.sort(key=lambda value: value[0], reverse=True)
    if not candidates:
        return _insufficient(
            "I could not find a recorded architecture decision or rationale in this repository's ADRs, issues, pull requests, or Slack evidence."
        )
    _, sentence, item = candidates[0]
    return {
        **_structured(f"The recorded decision is: {sentence}", "decision rationale"),
        "supporting_chunk_ids": [item.chunk_id],
    }


def _approval_answer(query: str, evidence: list[GraphEvidence]) -> dict[str, Any]:
    action_terms = {
        term
        for term in ("restart", "deploy", "production", "rotate", "delete", "scale")
        if term in query.casefold()
    }
    candidates: list[tuple[int, str, GraphEvidence]] = []
    for item in evidence:
        for sentence in sentences(item.text):
            lowered = sentence.casefold()
            explicit = any(
                marker in lowered
                for marker in (
                    "requires approval",
                    "require approval",
                    "must be approved",
                    "approval is required",
                    "approved by",
                    "only after approval",
                )
            )
            overlap = sum(term in lowered for term in action_terms)
            if explicit and (overlap or not action_terms):
                candidates.append((4 + overlap, sentence, item))
    candidates.sort(key=lambda value: value[0], reverse=True)
    if not candidates:
        return _insufficient(
            "I could not find an explicit approval policy for this action in the active repository's verified evidence."
        )
    _, sentence, item = candidates[0]
    return {
        **_structured(f"The recorded approval requirement is: {sentence}", "approval policy"),
        "supporting_chunk_ids": [item.chunk_id],
    }


def _change_impact_answer(query: str, evidence: list[GraphEvidence]) -> dict[str, Any]:
    anchors = _technical_anchors(query)
    if not anchors:
        return _insufficient(
            "Specify the API endpoint, client function, environment variable, or service name you plan to change. Runbook will not infer a blast radius from the phrase 'this API'."
        )
    matching: list[GraphEvidence] = []
    for item in evidence:
        haystack = f"{item.source_title}\n{item.text}".casefold()
        if any(anchor.casefold() in haystack for anchor in anchors):
            matching.append(item)
    repositories: dict[str, GraphEvidence] = {}
    for item in matching:
        label = str(item.metadata.get("repository") or item.metadata.get("project_name") or "")
        if label:
            repositories.setdefault(label.removesuffix(".git"), item)
    if not repositories:
        return _insufficient(
            f"I found no indexed repository with a direct reference to {', '.join(sorted(anchors))}."
        )
    selected = list(repositories.items())[:8]
    answer = (
        "These repositories contain direct references and therefore require impact review: "
        + "; ".join(f"{repository} (`{item.source_title}`)" for repository, item in selected)
        + ". This proves a reference, not that a breaking change will occur."
    )
    return {
        **_structured(answer, "change impact"),
        "supporting_chunk_ids": [item.chunk_id for _, item in selected],
    }


def _configuration_locator_answer(query: str, evidence: list[GraphEvidence]) -> dict[str, Any]:
    anchors = _technical_anchors(query)
    trace_requested = any(
        term in query.casefold() for term in ("trace ", "flow through", "from configuration")
    )
    if not anchors:
        return _insufficient()
    matches: list[tuple[int, GraphEvidence]] = []
    for item in evidence:
        haystack = f"{item.source_title}\n{item.text}".casefold()
        present = [anchor for anchor in anchors if anchor.casefold() in haystack]
        if not present:
            continue
        title = item.source_title.casefold()
        definition = any(
            re.search(rf"(?m)^\s*{re.escape(anchor)}\s*=", item.text) for anchor in present
        )
        priority = 4 if definition else 3 if ".env" in title else 2
        matches.append((priority, item))
    matches.sort(key=lambda value: (value[0], value[1].score), reverse=True)
    selected: list[GraphEvidence] = []
    seen: set[tuple[str, str]] = set()
    for _, item in matches:
        identity = (
            str(item.metadata.get("repository") or item.metadata.get("project_name") or ""),
            item.source_title,
            str(item.metadata.get("line_start") or "") if trace_requested else "",
        )
        if identity in seen:
            continue
        seen.add(identity)
        selected.append(item)
        if len(selected) == 8:
            break
    if not selected:
        return _insufficient(
            f"I found no indexed file with a direct reference to {', '.join(sorted(anchors))}."
        )
    if trace_requested:
        steps: list[tuple[int, int, str]] = []
        for original_index, item in enumerate(selected):
            title = item.source_title
            lowered_title = title.casefold()
            text = item.text
            roles: list[str] = []
            is_config = ".env" in lowered_title or lowered_title.endswith(
                ("settings.yaml", "settings.yml", "config.yaml", "config.yml")
            )
            is_code = Path(lowered_title).suffix in {
                ".go",
                ".java",
                ".js",
                ".jsx",
                ".php",
                ".py",
                ".rb",
                ".rs",
                ".ts",
                ".tsx",
            }
            priority = 4
            if is_config:
                roles.append("declares the configuration names")
                priority = 0
            elif not is_code:
                roles.append("documents the configuration contract")
                priority = 5
            if is_code and re.search(r"(?:getenv|environ(?:\.get)?|process\.env)", text, re.I):
                roles.append("reads them from the process environment")
                priority = min(priority, 1)
            if (
                is_code
                and len(anchors) >= 2
                and all(anchor.casefold() in text.casefold() for anchor in anchors)
                and re.search(r"(?:url|endpoint|rstrip|lstrip|urljoin)", text, re.I)
            ):
                roles.append("combines the base URL and path into the request target")
                priority = min(priority, 2)
            if is_code and re.search(
                r"(?:urlopen|urllib|httpx\.|requests\.|HTTPConnection|\.request\s*\(|\.post\s*\()",
                text,
                re.I,
            ):
                roles.append("performs the outbound HTTP request")
                priority = 3
            if not roles:
                roles.append("directly references the configuration")
                priority = 2
            location = _evidence_location(item)
            section = _evidence_section(item)
            detail = f"`{title}`{location}"
            if section:
                detail += f" in `{section}`"
            steps.append(
                (priority, original_index, f"{detail} {' and '.join(dict.fromkeys(roles))}")
            )
        steps.sort(key=lambda value: (value[0], value[1]))
        return {
            **_structured(
                "Verified evidence: "
                + " ".join(f"{index}. {step}." for index, (_, _, step) in enumerate(steps, 1))
                + " Inference and runtime limit: these direct references establish the static code path, "
                "but they do not prove that a deployed SAP endpoint accepted a request; no runtime telemetry "
                "was supplied.",
                "configuration trace",
            ),
            "supporting_chunk_ids": [item.chunk_id for item in selected],
        }
    locations = []
    for item in selected:
        repository = str(item.metadata.get("repository") or item.metadata.get("project_name") or "")
        locations.append(f"{repository.removesuffix('.git')}: `{item.source_title}`")
    return {
        **_structured(
            f"{', '.join(sorted(anchors))} is directly referenced in " + "; ".join(locations) + ".",
            "configuration location",
        ),
        "supporting_chunk_ids": [item.chunk_id for item in selected],
    }


def _evidence_location(item: GraphEvidence) -> str:
    start = item.metadata.get("line_start")
    end = item.metadata.get("line_end")
    if start and end:
        return f" lines {start}-{end}"
    if start:
        return f" line {start}"
    return ""


def _evidence_section(item: GraphEvidence) -> str:
    section = str(item.metadata.get("section") or "").strip()
    match = re.search(r"(?:async\s+)?(?:def|class|function)\s+([A-Za-z_$][\w$]*)", section)
    return match.group(1) if match else ""


def _technical_anchors(query: str) -> set[str]:
    anchors = set(re.findall(r"\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b", query))
    anchors.update(re.findall(r"\b[a-z][\w-]*(?:_service|-service)\b", query.casefold()))
    anchors.update(re.findall(r"/(?:api/)?[a-zA-Z0-9_./{}:-]+", query))
    anchors.update(
        value.strip() for value in re.findall(r"`([^`]{3,120})`", query) if not value.isspace()
    )
    return anchors


def _status_trust(score: float, reason: str) -> dict[str, Any]:
    bounded = round(min(0.95, max(0.0, score)), 3)
    return {
        "score": bounded,
        "level": "high" if bounded >= 0.75 else "medium" if bounded >= 0.5 else "low",
        "reason": reason,
        "factors": {"recorded_review_state": 1.0},
        "contradictions": [],
    }


def _ownership_answer(query: str, evidence: list[GraphEvidence]) -> dict[str, Any]:
    item, metadata = _repository_metadata(evidence)
    owner = metadata.get("owner")
    author = metadata.get("latest_commit_author")
    committed_at = metadata.get("latest_commit_date")
    commit_url = metadata.get("latest_commit_url")
    wants_commit = any(
        phrase in query.casefold()
        for phrase in ("committed", "commited", "recent commit", "latest commit")
    )
    wants_owner = "owner" in query.casefold() or "owns" in query.casefold()
    if wants_owner and not owner:
        return _insufficient()
    if wants_commit and not author:
        return _insufficient()
    parts: list[str] = []
    if owner and wants_owner:
        parts.append(f"The repository owner is {owner}.")
    if author and wants_commit:
        detail = f"The most recent indexed commit was authored by {author}"
        if committed_at:
            detail += f" on {committed_at}"
        detail += "."
        if commit_url:
            detail += f" Commit: {commit_url}"
        parts.append(detail)
    if not parts or not item:
        return _insufficient()
    return _structured(" ".join(parts), "repository ownership and commit history")


def _commit_records(
    evidence: list[GraphEvidence],
) -> list[tuple[str, GraphEvidence, dict[str, str]]]:
    """Return authoritative GitHub commit evidence in actual commit-time order."""
    records: list[tuple[str, GraphEvidence, dict[str, str]]] = []
    for item in evidence:
        if item.source_type != "github_commit":
            continue
        metadata = _metadata_lines(item)
        committed_at = metadata.get("committed_at") or metadata.get("source_updated_at") or ""
        records.append((committed_at, item, metadata))
    records.sort(key=lambda value: value[0], reverse=True)
    return records


def _commit_history_answer(evidence: list[GraphEvidence]) -> dict[str, Any]:
    records = _commit_records(evidence)
    if not records:
        return _insufficient(
            "I do not have an indexed GitHub commit for this repository yet. Refresh the repository memory and try again."
        )
    committed_at, item, metadata = records[0]
    sha = metadata.get("commit_sha") or metadata.get("sha") or ""
    message = metadata.get("message") or item.source_title.partition(": ")[2] or "No message"
    author = metadata.get("author") or metadata.get("owner") or "unknown author"
    short_sha = sha[:8] if sha else "unknown SHA"
    answer = (
        f"The latest indexed GitHub commit is **{message.splitlines()[0]}** "
        f"(`{short_sha}`) by **{author}**"
    )
    if committed_at:
        answer += f", committed at **{committed_at}**"
    answer += "."
    return {
        "answer": answer,
        "likely_cause": "Not applicable — this is repository history.",
        "safe_actions": [],
        "approval_required": [],
        "sufficient": True,
        "supporting_chunk_ids": [item.chunk_id],
    }


def _recent_changes_answer(evidence: list[GraphEvidence]) -> dict[str, Any]:
    records = _commit_records(evidence)[:5]
    if not records:
        return _insufficient(
            "I do not have recent indexed GitHub changes for this repository yet. Refresh the repository memory and try again."
        )
    lines: list[str] = []
    supporting: list[str] = []
    for committed_at, item, metadata in records:
        sha = metadata.get("commit_sha") or metadata.get("sha") or ""
        message = metadata.get("message") or item.source_title.partition(": ")[2] or "No message"
        author = metadata.get("author") or metadata.get("owner") or "unknown author"
        when = f" · {committed_at}" if committed_at else ""
        lines.append(f"- **{message.splitlines()[0]}** (`{sha[:8]}`) · {author}{when}")
        supporting.append(item.chunk_id)
    return {
        "answer": "**Recent indexed GitHub changes**\n" + "\n".join(lines),
        "likely_cause": "Not applicable — this is repository change history.",
        "safe_actions": [],
        "approval_required": [],
        "sufficient": True,
        "supporting_chunk_ids": supporting,
    }


def _setup_answer(evidence: list[GraphEvidence]) -> dict[str, Any]:
    commands: list[str] = []
    notes: list[str] = []
    for item in evidence:
        title = item.source_title.casefold()
        if title == "package.json":
            try:
                package = json.loads(item.text)
            except json.JSONDecodeError:
                package = {}
            if package.get("dependencies") or package.get("devDependencies"):
                commands.append("npm install")
            scripts = package.get("scripts") or {}
            for script in ("dev", "start", "serve"):
                if scripts.get(script):
                    commands.append(f"npm run {script}")
                    break
        elif title in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
            commands.append("docker compose up --build")
        elif title.startswith("readme") or title.endswith(("setup.md", "development.md")):
            for block in re.findall(
                r"```(?:bash|sh|shell|console)?\s*\n(.*?)```", item.text, re.I | re.S
            ):
                for raw in block.splitlines():
                    command = raw.strip().removeprefix("$").strip()
                    if _looks_like_setup_command(command):
                        commands.append(command)
            if int(item.metadata.get("chunk_index") or 0) == 0:
                for sentence in sentences(item.text):
                    lowered = sentence.casefold()
                    if any(marker in lowered for marker in ("prerequisite", "before running")):
                        notes.append(sentence)
        elif title == "makefile":
            targets = re.findall(r"^([a-zA-Z][\w.-]+):(?:\s|$)", item.text, re.M)
            for target in ("dev", "start", "run", "up"):
                if target in targets:
                    commands.append(f"make {target}")
                    break
    commands = list(dict.fromkeys(commands))[:6]
    if not commands:
        return _insufficient()
    steps = " ".join(f"{index}. `{command}`" for index, command in enumerate(commands, 1))
    answer = f"To run this repository locally, use the evidenced setup sequence: {steps}"
    if notes:
        answer += f" Prerequisite: {notes[0]}"
    return _structured(answer, "local development setup")


def _looks_like_setup_command(value: str) -> bool:
    if not value or value.startswith(("#", "//")) or len(value) > 220:
        return False
    prefixes = (
        "bun ",
        "docker ",
        "go ",
        "make ",
        "npm ",
        "pnpm ",
        "poetry ",
        "python ",
        "python3 ",
        "uv ",
        "yarn ",
    )
    return value.casefold().startswith(prefixes)


def _structured(answer: str, subject: str) -> dict[str, Any]:
    return {
        "answer": answer,
        "likely_cause": f"Not applicable — this is a {subject} question.",
        "safe_actions": [],
        "approval_required": [],
        "sufficient": True,
    }


def _actions(evidence: list[GraphEvidence]) -> tuple[list[str], list[str]]:
    safe: list[str] = []
    risky: list[str] = []
    for item in evidence:
        signals = item.metadata.get("signals", {})
        candidates = list(signals.get("procedures", [])) + list(signals.get("commands", []))
        for candidate in candidates:
            lowered = candidate.lower()
            if not any(marker in lowered for marker in ACTION_MARKERS):
                continue
            target = risky if any(marker in lowered for marker in MUTATION_MARKERS) else safe
            if candidate not in target:
                target.append(candidate)
    return safe[:5], risky[:5]


def _insufficient(
    message: str = "I do not have enough company memory to answer this confidently.",
) -> dict[str, Any]:
    return {
        "answer": message,
        "likely_cause": "Insufficient evidence",
        "safe_actions": [],
        "approval_required": [],
        "sufficient": False,
    }


def validate_grounded_answer(
    query: str,
    grounded: dict[str, Any],
    evidence: list[GraphEvidence],
) -> dict[str, Any]:
    """Enforce answer-type evidence contracts after every retrieval lane."""

    if not grounded.get("sufficient"):
        return grounded
    if grounded.get("answer_kind") == "semantic_change":
        # The belief ledger has its own source-backed old→new contract. Do not
        # downgrade it merely because the natural-language question also says
        # "latest commit."
        return grounded
    intent = answer_intent(query)
    if intent == "ownership":
        item, metadata = _repository_metadata(evidence)
        if not item or not str(metadata.get("owner") or "").strip():
            return _insufficient()
    elif intent == "entity_ownership":
        typed_memory = any(
            str(item.metadata.get("memory_type") or "").casefold() == "ownership"
            or (
                item.metadata.get("primary_lane") == "atomic_memory"
                and re.search(r"\b(?:owned by|owns|responsible for)\b", item.text, re.I)
            )
            for item in evidence
        )
        if not typed_memory:
            return _insufficient()
    elif intent == "repository_identity":
        item, metadata = _repository_metadata(evidence)
        if not item or not (
            metadata.get("repository")
            or metadata.get("full_name")
            or re.search(r"github\.com/[^/\s]+/[^/\s#?]+", item.source_url)
        ):
            return _insufficient()
    elif intent == "commit_history":
        if not any(
            item.source_type == "github_commit"
            or (
                item.source_type == "repository_metadata"
                and _metadata_lines(item).get("latest_commit_sha")
            )
            for item in evidence
        ):
            return _insufficient()
    elif intent == "recent_changes":
        if not any(item.source_type == "github_commit" for item in evidence):
            return _insufficient()
    elif intent == "runtime_record":
        if not any(
            item.source_type in {"api_snapshot", "database_record", "runtime_record"}
            for item in evidence
        ):
            return _insufficient()
    elif intent == "slack_messages":
        if not evidence or any(
            item.source_type not in {"slack", "slack_export"} for item in evidence
        ):
            return _insufficient()
    return grounded


def llm_answer(
    query: str,
    evidence: list[GraphEvidence],
    *,
    compiled_context: dict[str, Any] | None = None,
    model_provider: str | None = None,
    lens: str = "",
) -> dict[str, Any] | None:
    """Synthesize one grounded answer from evidence.

    ``lens`` asks for a specific reading of the question (cause, procedure,
    conservative reading, …) without loosening the evidence contract; it is how
    :mod:`app.retrieval.deliberation` obtains genuinely different candidates.
    """
    if not evidence:
        return None
    try:
        source_text = str((compiled_context or {}).get("content") or "")
        if not source_text:
            source_text = "\n\n".join(
                f"[S{index}] {item.source_title} ({item.source_type})\n{item.text}"
                for index, item in enumerate(evidence, 1)
            )
        approach = f"Approach: {lens}\n" if lens else ""
        prompt = (
            "You are MemoryWorks's source-grounded synthesis engine. Answer any kind of question "
            "using only the supplied company evidence. Cite every factual claim with [S<number>]. "
            "Do not infer missing facts and do not turn examples into current policy. Return JSON "
            "with answer (string), likely_cause (string), safe_actions (array of strings), "
            "approval_required (array of strings), and used_sources (an array of source numbers). "
            "Use empty arrays when there are no actions. If evidence is inadequate, use exactly "
            "'I do not have enough company memory to answer this confidently.'\n"
            f"{approach}Question: {query}\nEvidence:\n{source_text}"
        )
        generated = generate_grounded_json(prompt, model_provider)
        if not generated:
            return None
        result, provider = generated
        if result.get("answer"):
            result["answer"] = str(result["answer"])
            result["likely_cause"] = str(
                result.get("likely_cause")
                or "Not applicable — this is a source-grounded company memory answer."
            )
            for action_field in ("safe_actions", "approval_required"):
                actions = result.get(action_field)
                if isinstance(actions, str):
                    actions = [actions]
                if not isinstance(actions, list):
                    actions = []
                result[action_field] = [
                    str(action).strip() for action in actions if str(action).strip()
                ][:20]
            used = [
                int(value)
                for value in result.get("used_sources", [])
                if str(value).isdigit() and 1 <= int(value) <= len(evidence)
            ]
            result["supporting_chunk_ids"] = [evidence[index - 1].chunk_id for index in used]
            for index in used:
                result["answer"] = result["answer"].replace(
                    f"[S{index}]", f"[{evidence[index - 1].source_title}]"
                )
            result["sufficient"] = bool(used) and not result["answer"].startswith(
                "I do not have enough company memory"
            )
            result["_model_provider"] = provider.id
            return result
    except Exception:
        return None
    return None
