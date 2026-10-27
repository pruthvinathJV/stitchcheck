#!/bin/zsh
# Stage 2 of the classifier search, then the 11-fabric demo model with the winning setting.
#
#   scripts/train/stage2.sh   (after sweep_stage1.sh)
cd "$(dirname "$0")/../.."
source scripts/train/lib.sh

repeats=("${(@f)$(.venv/bin/python scripts/train/stage2.py top3)}")
for r in $repeats; do
  [[ -n "$r" ]] && run --variant clean ${=r}
done
winner=$(.venv/bin/python scripts/train/stage2.py winner) || exit 1
echo "== stage 2 winner: $winner $(date)"
.venv/bin/python scripts/train/summarize.py
run --variant clean --all-fabrics ${=winner}
echo "== demo model done $(date)"
