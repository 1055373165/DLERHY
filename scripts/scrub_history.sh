#!/usr/bin/env bash
# Rewrite git history to remove copyrighted book content and database dumps
# that were tracked before P1.3 (deliverables, book translations, EPUBs,
# artifacts, scratch state). Run this ONLY on a fresh clone; it never touches
# the repository it is invoked from.
#
#   scripts/scrub_history.sh <fresh-clone-path>
#
# Afterwards, verify (the script prints the checks) and then force-push every
# branch from the clone:  git push --force --all origin && git push --force --tags origin
# Every collaborator must re-clone; commit hashes change.
set -euo pipefail

CLONE="${1:?usage: scrub_history.sh <fresh-clone-path>}"
cd "$CLONE"

if [[ -n "$(git status --porcelain)" ]]; then
    echo "clone has uncommitted changes; refusing" >&2
    exit 1
fi

# Paths whose entire history is removed.
PATHS=(
    deliverable
    LLM-Book
    LLM-Book-review-ch1-ch2
    books
    artifacts
    .scratch
    frontend/.omc
    frontend/artifacts
    book-agent.db
    book_agent.db
    .test-tmp
    .forge
    .omc
    forge
    forge-v2
    .claude/settings.local.json
)

ARGS=()
for p in "${PATHS[@]}"; do
    ARGS+=(--path "$p")
done

# --invert-paths: keep everything except the listed paths.
# --strip-blobs-bigger-than: safety net for database dumps that lived elsewhere.
git filter-repo --force --invert-paths "${ARGS[@]}" --strip-blobs-bigger-than 5M

git reflog expire --expire=now --all
git gc --prune=now --aggressive --quiet

echo "== verification"
for p in "${PATHS[@]}"; do
    n=$(git log --all --pretty=format: --name-only -- "$p" | grep -c . || true)
    printf '%-28s %s path-commits\n' "$p" "$n"
done
echo "largest remaining blobs:"
git rev-list --all --objects | git cat-file --batch-check='%(objectsize) %(rest)' | sort -n | tail -5
echo ".git size: $(du -sh .git | cut -f1)"
echo "branches: $(git for-each-ref --format='%(refname:short)' refs/heads | tr '\n' ' ')"
