#!/usr/bin/env bash
# One-time helper: push build/v1 and open the pull-request page.
# Usage: ./open_pr.sh git@github.com:YOUR-USER/YOUR-REPO.git   (or leave empty if origin exists)
set -e
cd "$(dirname "$0")"
if [ -n "$1" ]; then git remote remove origin 2>/dev/null || true; git remote add origin "$1"; fi
git remote get-url origin >/dev/null 2>&1 || { echo "Give the repo URL: ./open_pr.sh git@github.com:USER/REPO.git"; exit 1; }
git push -u origin main 2>/dev/null || true
git push -u origin build/v1
URL=$(git remote get-url origin | sed -E 's#git@github.com:#https://github.com/#; s#\.git$##')
echo
echo "Open this link to create the pull request (title and summary are pre-filled from PR_BODY.md):"
echo "  $URL/compare/main...build/v1?expand=1"
command -v open >/dev/null && open "$URL/compare/main...build/v1?expand=1" || true
