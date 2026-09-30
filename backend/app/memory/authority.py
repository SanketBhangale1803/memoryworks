from __future__ import annotations

from typing import Any

from app.core.config import settings

from .beliefs import BeliefStore

DEFAULT_AUTHORITY_ORDER = (
    "current_code_config",
    "approved_policy_decision",
    "recent_authoritative_slack",
    "merged_pull_request",
    "open_issue",
    "readme_documentation",
    "old_slack",
    "inferred_memory",
)

INTENTION_TIERS = {"approved_policy_decision", "recent_authoritative_slack"}
REALITY_TIERS = {"current_code_config", "merged_pull_request"}


class AuthorityResolver:
    """Resolve competing beliefs without deleting either side of the disagreement."""

    def __init__(self, store: BeliefStore, order: list[str] | tuple[str, ...] | None = None):
        configured = tuple(
            value.strip()
            for value in settings.org_memory_authority_order.split(",")
            if value.strip()
        )
        self.store = store
        self.order = tuple(order or configured or DEFAULT_AUTHORITY_ORDER)
        self.rank = {tier: index for index, tier in enumerate(self.order)}

    def resolve(self, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
        left_rank = self.rank.get(left["authority_tier"], len(self.order))
        right_rank = self.rank.get(right["authority_tier"], len(self.order))
        intention_vs_reality = self._intention_vs_reality(left, right)
        if left_rank == right_rank:
            return {
                "outcome": "human_review",
                "winner": None,
                "loser": None,
                "requires_human_review": True,
                "intention_vs_reality": intention_vs_reality,
                "reason": (
                    f"Both beliefs have the same authority tier ({left['authority_tier']}); "
                    "MemoryWorks preserved both and requires human review."
                ),
                "explanation": self._explanation(left, right, intention_vs_reality),
            }
        winner, loser = (left, right) if left_rank < right_rank else (right, left)
        outcome = "intention_vs_reality" if intention_vs_reality else "authoritative_override"
        return {
            "outcome": outcome,
            "winner": winner,
            "loser": loser,
            "requires_human_review": False,
            "intention_vs_reality": intention_vs_reality,
            "reason": (
                f"{winner['authority_tier']} outranks {loser['authority_tier']} under the "
                "configured company authority order."
            ),
            "explanation": self._explanation(left, right, intention_vs_reality),
        }

    def reconcile(self, left_id: str, right_id: str) -> dict[str, Any]:
        left = self.store.get(left_id)
        right = self.store.get(right_id)
        if not left or not right:
            raise ValueError("Both beliefs must exist before authority resolution")
        decision = self.resolve(left, right)
        chronological = sorted((left, right), key=lambda item: item["created_at"])
        relationship = self.store.link_relationship(
            chronological[0]["id"],
            chronological[1]["id"],
            "CONTRADICTS",
            metadata={
                "authority_outcome": decision["outcome"],
                "winner_id": (decision["winner"] or {}).get("id", ""),
                "requires_human_review": decision["requires_human_review"],
                "intention_vs_reality": decision["intention_vs_reality"],
            },
        )
        if decision["loser"]:
            decision["loser"] = self.store.set_status(decision["loser"]["id"], "contradicted")
            decision["winner"] = self.store.get(decision["winner"]["id"])
        return {**decision, "relationship": relationship}

    @staticmethod
    def infer_tier(
        source_type: str, title: str = "", metadata: dict[str, Any] | None = None
    ) -> str:
        metadata = metadata or {}
        normalized_type = source_type.casefold()
        normalized_title = title.casefold()
        if normalized_type in {"github_push", "github_commit"}:
            return "current_code_config"
        if normalized_type == "repo_file":
            if normalized_title.startswith("readme") or normalized_title.endswith(".md"):
                return "readme_documentation"
            return "current_code_config"
        if normalized_type == "pull_request" and str(metadata.get("state")).casefold() in {
            "merged",
            "closed",
        }:
            return "merged_pull_request"
        if normalized_type in {"github_issue", "github_issue_export"}:
            return "open_issue"
        if normalized_type in {"policy", "decision", "report"} and metadata.get("approved"):
            return "approved_policy_decision"
        if normalized_type in {"slack", "slack_export"}:
            return "recent_authoritative_slack" if metadata.get("authoritative") else "old_slack"
        if normalized_type in {"doc", "report"}:
            return "readme_documentation"
        return "inferred_memory"

    @staticmethod
    def _intention_vs_reality(left: dict[str, Any], right: dict[str, Any]) -> bool:
        tiers = {left["authority_tier"], right["authority_tier"]}
        return bool(tiers.intersection(INTENTION_TIERS) and tiers.intersection(REALITY_TIERS))

    @staticmethod
    def _explanation(
        left: dict[str, Any], right: dict[str, Any], intention_vs_reality: bool
    ) -> str:
        if intention_vs_reality:
            intention = left if left["authority_tier"] in INTENTION_TIERS else right
            reality = left if left["authority_tier"] in REALITY_TIERS else right
            return (
                f"The intended company policy is: {intention['current_value']} "
                f"The current implementation shows: {reality['current_value']}"
            )
        return (
            f"Conflicting company beliefs were preserved: {left['current_value']} "
            f"versus {right['current_value']}"
        )
