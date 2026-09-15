#!/usr/bin/env bash
# Publish the tail of every CI log collected during a failing job to the pull request.
#
# Why this exists: the Actions log endpoint is not always reachable (proxied/offline networks,
# restricted tokens), and "the job failed" without the traceback is useless. Every step therefore
# tees its output into a directory; on failure this script turns that directory into one comment,
# replacing the previous report instead of stacking one per push.
#
# usage: scripts/ci-report.sh <pr-number> <job-name> [log-dir] [run-url]
set -euo pipefail

PR="${1:?usage: ci-report.sh <pr-number> <job-name> [log-dir] [run-url]}"
JOB="${2:-ci}"
LOG_DIR="${3:-/tmp/azir-ci}"
RUN_URL="${4:-}"
REPO="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY must be set}"
MARKER='<!-- azir-ci-report -->'

BODY="$(mktemp)"
trap 'rm -f "$BODY"' EXIT

{
  echo "$MARKER"
  echo "### CI failure — \`${JOB}\`"
  if [ -n "$RUN_URL" ]; then echo "[full run]($RUN_URL)"; fi
  echo
  shopt -s nullglob
  logs=("$LOG_DIR"/*.log)
  if [ ${#logs[@]} -eq 0 ]; then
    echo "_no step logs were collected_"
  fi
  for file in "${logs[@]}"; do
    echo "<details>"
    echo "<summary><code>$(basename "$file")</code> — last 100 lines</summary>"
    echo
    echo '```text'
    tail -n 100 "$file"
    echo '```'
    echo
    echo "</details>"
    echo
  done
} > "$BODY"

previous="$(gh api "repos/$REPO/issues/$PR/comments" --paginate \
  -q ".[] | select(.body | startswith(\"$MARKER\")) | .id" 2>/dev/null | tail -n 1 || true)"

if [ -n "$previous" ]; then
  gh api -X PATCH "repos/$REPO/issues/comments/$previous" -F body=@"$BODY" > /dev/null
  echo "updated CI report comment #$previous on PR #$PR"
else
  gh pr comment "$PR" --repo "$REPO" --body-file "$BODY" > /dev/null
  echo "posted CI report to PR #$PR"
fi
