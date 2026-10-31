#!/usr/bin/env bash
# Freeze the submission: check the repo and create an annotated tag on main. Never pushes or deploys.
#
#   scripts/release/freeze.sh v1.0-submission
#
# Refuses unless HEAD is the main branch with a clean working tree (no changes, no untracked files) and the tag
# does not exist yet. Runs scripts/release/check_repo.sh, creates the tag, then prints the push and deploy
# commands for a person to run.
set -euo pipefail

TAG=${1:-}
if [ -z "$TAG" ]; then
  echo "usage: scripts/release/freeze.sh TAG   (e.g. v1.0-submission)"
  exit 2
fi

ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"

BRANCH=$(git symbolic-ref --quiet --short HEAD || echo "(detached)")
if [ "$BRANCH" != "main" ]; then
  echo "refusing: on '$BRANCH', not main"
  exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "refusing: the working tree is not clean"
  git status --short
  exit 1
fi
if git rev-parse --quiet --verify "refs/tags/$TAG" >/dev/null; then
  echo "refusing: tag $TAG already exists"
  exit 1
fi

echo "Running release checks..."
if ! scripts/release/check_repo.sh; then
  echo "refusing: release checks failed; nothing was tagged"
  exit 1
fi

COMMIT=$(git rev-parse --short HEAD)
git tag -a "$TAG" -m "StitchCheck submission $TAG"
echo
echo "Tagged $TAG at $COMMIT (annotated, local only)."
echo
echo "Next steps, run by hand (this script does not push or deploy):"
echo
echo "  # push main and the tag"
echo "  git push origin main"
echo "  git push origin $TAG"
echo
echo "  # deploy from the tag, then return to main"
echo "  git checkout --detach $TAG"
echo "  STITCHCHECK_WARM=1 modal deploy scripts/cloud/serve_modal.py"
echo "  git checkout main"
echo
echo "  # then: smoke test the live URL and check that /version reports $TAG ($COMMIT)"
