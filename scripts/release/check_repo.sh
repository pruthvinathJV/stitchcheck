#!/usr/bin/env bash
# Pre-release checks for the public repository. Exits non-zero if any check fails and prints what failed.
#
#   scripts/release/check_repo.sh
#
# (a) no data, photos, audio, model weights or large binaries are tracked
# (b) private working notes, local agent settings and .env are not tracked
# (c) no tool attribution in tracked files or commit messages
# (d) no em dash or en dash in the public docs and the app
# (e) gitleaks finds no secret (skipped with a note if gitleaks is not installed)
# (f) the test suite passes
set -u

ROOT=$(git rev-parse --show-toplevel) || { echo "not inside a git repository"; exit 2; }
cd "$ROOT" || exit 2

FAILED=()
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; [ $# -gt 1 ] && printf '%s\n' "$2" | sed 's/^/      /'; FAILED+=("$1"); }

# (a) data and binaries
hits=$(git ls-files -- data/raw data/phone data/audio data/cache data/gallery data/app models \
       '*.pt' '*.onnx' '*.zip' '*.mp4' '*.wav')
if [ -z "$hits" ]; then pass "(a) no tracked data, photos, audio, weights or large binaries"
else fail "(a) tracked files that must stay out of the repo" "$hits"; fi

# (b) private files
hits=$(git ls-files -- CLAUDE.md DECISIONS.md OPEN_QUESTIONS.md .claude .env)
if [ -z "$hits" ]; then pass "(b) private notes, local agent settings and .env are not tracked"
else fail "(b) private files are tracked" "$hits"; fi

# (c) attribution words in tracked files and commit messages
WORDS="claude|anthropic|co-authored|generated with"
hits=$(git grep -n -i -I -E "$WORDS" -- . ':(exclude)scripts/release/check_repo.sh' ':(exclude)*.csv')
if [ -z "$hits" ]; then pass "(c1) no attribution words in tracked files"
else fail "(c1) attribution words in tracked files" "$hits"; fi
hits=$(git log --format='%h %B' | grep -i -E "$WORDS")
if [ -z "$hits" ]; then pass "(c2) no attribution words in commit messages"
else fail "(c2) attribution words in commit messages" "$hits"; fi

# (d) em dash (U+2014) and en dash (U+2013), matched as UTF-8 bytes
EM=$(printf '\342\200\224'); EN=$(printf '\342\200\223')
hits=$(git grep -n -F -e "$EM" -e "$EN" -- README.md 'docs/*.md' app/SPEC.md docs/ui_strings.json 'app/*.js' 'app/*.html')
if [ -z "$hits" ]; then pass "(d) no em or en dashes in README, docs, app spec, UI strings and app files"
else fail "(d) em or en dashes found" "$hits"; fi

# (e) secrets
if command -v gitleaks >/dev/null 2>&1; then
  if out=$(gitleaks detect --source . --redact --no-banner 2>&1); then pass "(e) gitleaks: no leaks found"
  else fail "(e) gitleaks found possible secrets" "$out"; fi
else
  echo "SKIP  (e) gitleaks is not installed; install it and rerun before release"
fi

# (f) tests
PY=/Users/pruthvi/.venvs/stitchcheck/bin/python
[ -x "$PY" ] || PY=python3
if out=$(PYTHONPATH=src "$PY" -m pytest -q 2>&1); then pass "(f) tests: $(printf '%s\n' "$out" | tail -n 1)"
else fail "(f) tests failed" "$(printf '%s\n' "$out" | tail -n 30)"; fi

echo
if [ ${#FAILED[@]} -eq 0 ]; then
  echo "All checks passed."
  exit 0
fi
echo "${#FAILED[@]} check(s) failed:"
printf '  %s\n' "${FAILED[@]}"
exit 1
