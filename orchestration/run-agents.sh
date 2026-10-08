#!/usr/bin/env bash
# Start the planner (codex, GPT 6.1 Sol, medium) and the executor (Muse Spark 1.3
# via opencode) on a loop, each in its own git worktree so neither touches your
# checkout. Run from anywhere inside your clone:
#
#   bash <(git show origin/claude/credit-balance-question-2jp80s:orchestration/run-agents.sh)
#
# Env: CODEX_MODEL, MUSE_MODEL (default: the first "muse" entry in `opencode models`),
#      INTERVAL_MIN (default 30), ONCE=1 for a single round,
#      AGENT_WORKTREES (default: a sibling folder of your clone).
set -euo pipefail

REPO="$(git rev-parse --show-toplevel)"
WT="${AGENT_WORKTREES:-$(dirname "$REPO")/memoryworks-agents}"
CODEX_MODEL="${CODEX_MODEL:-gpt-6.1-sol}"
MUSE_MODEL="${MUSE_MODEL:-}"
INTERVAL_MIN="${INTERVAL_MIN:-30}"
CONTROL=claude/credit-balance-question-2jp80s

command -v codex >/dev/null || { echo "codex not found: npm i -g @openai/codex"; exit 1; }
command -v opencode >/dev/null || { echo "opencode not found"; exit 1; }

# Use the Muse model id opencode actually lists, unless one was given.
if [ -z "$MUSE_MODEL" ]; then
  MUSE_MODEL="$(opencode models 2>/dev/null | grep -i 'muse' | head -n 1 | awk '{print $1}')"
  [ -n "$MUSE_MODEL" ] || { echo "No Muse model in 'opencode models'. Set MUSE_MODEL=<provider/model>."; exit 1; }
fi
echo "planner:  codex $CODEX_MODEL (reasoning medium)"
echo "executor: opencode $MUSE_MODEL"

# Worktrees keep their git metadata in the main repository's .git, which is
# outside the planner's sandboxed workspace; without this, commits fail.
GIT_COMMON="$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir)"

git -C "$REPO" fetch -q origin
mkdir -p "$WT"
[ -d "$WT/codex" ] || git -C "$REPO" worktree add -B agents/codex-plans "$WT/codex" origin/agents/codex-plans
[ -d "$WT/muse" ] || git -C "$REPO" worktree add --detach "$WT/muse" origin/main

prompt() {
  git -C "$REPO" show "origin/$CONTROL:orchestration/prompts/$1" | sed -n '/^---8<---$/,$p' | tail -n +2
}

while true; do
  git -C "$REPO" fetch -q origin
  echo "== planner  $(date '+%F %T')"
  (
    cd "$WT/codex"
    git pull -q --ff-only origin agents/codex-plans || true
    codex exec -m "$CODEX_MODEL" -c model_reasoning_effort=medium \
      -s workspace-write -c sandbox_workspace_write.network_access=true \
      --add-dir "$GIT_COMMON" "$(prompt CODEX_PLANNER.md)"
  ) || echo "planner round failed; continuing"

  echo "== executor $(date '+%F %T')"
  (
    cd "$WT/muse"
    opencode run -m "$MUSE_MODEL" "$(prompt MUSE_EXECUTOR.md)"
  ) || echo "executor round failed; continuing"

  [ "${ONCE:-}" = 1 ] && break
  echo "== sleeping ${INTERVAL_MIN}m (Ctrl-C to stop)"
  sleep $((INTERVAL_MIN * 60))
done
