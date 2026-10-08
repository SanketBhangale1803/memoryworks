#!/usr/bin/env bash
# Run the executor (Muse Spark 1.3 via opencode) on a loop in its own git
# worktree, so it never touches your checkout. Run from inside your clone:
#
#   caffeinate -i bash <(git show origin/claude/credit-balance-question-2jp80s:orchestration/run-agents.sh)
#
# Env: MUSE_MODEL (default: the first "muse" entry in `opencode models`),
#      INTERVAL_MIN (default 30), ONCE=1 for a single round,
#      AGENT_WORKTREES (default: a sibling folder of your clone).
set -euo pipefail

REPO="$(git rev-parse --show-toplevel)"
WT="${AGENT_WORKTREES:-$(dirname "$REPO")/memoryworks-agents}"
MUSE_MODEL="${MUSE_MODEL:-}"
INTERVAL_MIN="${INTERVAL_MIN:-30}"
CONTROL=claude/credit-balance-question-2jp80s

command -v opencode >/dev/null || { echo "opencode not found"; exit 1; }
if [ -z "$MUSE_MODEL" ]; then
  MUSE_MODEL="$(opencode models 2>/dev/null | grep -i 'muse' | head -n 1 | awk '{print $1}')"
  [ -n "$MUSE_MODEL" ] || { echo "No Muse model in 'opencode models'. Set MUSE_MODEL=<provider/model>."; exit 1; }
fi
echo "executor: opencode $MUSE_MODEL (plans and reviews come from the orchestrator on $CONTROL)"

git -C "$REPO" fetch -q origin
mkdir -p "$WT"
[ -d "$WT/muse" ] || git -C "$REPO" worktree add --detach "$WT/muse" origin/main

# opencode refuses files outside the working tree when nobody is there to
# approve it, so Muse keeps scratch files in .muse-scratch/ inside the tree.
# Excluding it here (shared by all worktrees) keeps it out of every commit.
EXCLUDE="$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir)/info/exclude"
mkdir -p "$(dirname "$EXCLUDE")"
grep -qx '.muse-scratch/' "$EXCLUDE" 2>/dev/null || echo '.muse-scratch/' >> "$EXCLUDE"
# An absolute path, so a command run from backend/ or frontend/ cannot resolve
# a relative ../.muse-scratch to a directory outside the worktree.
export MUSE_SCRATCH="$WT/muse/.muse-scratch"
mkdir -p "$MUSE_SCRATCH"

prompt() {
  git -C "$REPO" show "origin/$CONTROL:orchestration/prompts/$1" | sed -n '/^---8<---$/,$p' | tail -n +2
}

while true; do
  git -C "$REPO" fetch -q origin
  echo "== executor $(date '+%F %T')"
  (
    cd "$WT/muse"
    opencode run -m "$MUSE_MODEL" "$(prompt MUSE_EXECUTOR.md)"
  ) || echo "executor round failed; continuing"

  [ "${ONCE:-}" = 1 ] && break
  echo "== sleeping ${INTERVAL_MIN}m (Ctrl-C to stop)"
  sleep $((INTERVAL_MIN * 60))
done
