#!/bin/zsh
# Day 2 classifiers: clean and full EfficientNet-B0 on the 8 training fabrics, then (with --holdouts) the full
# model once per held-out training fabric. A run that hits the MPS stall watchdog (exit 75) resumes from its
# last epoch. caffeinate keeps the Mac from idle-sleeping while this runs.
#
#   scripts/train/day2.sh [--holdouts]
cd "$(dirname "$0")/../.."

run() {
  for attempt in 1 2 3 4; do
    caffeinate -i .venv/bin/python -u -m stitchcheck.classifier.train "$@" --resume 2>&1 | grep --line-buffered -vi warning
    code=${pipestatus[1]}
    [[ $code -ne 75 ]] && return $code
    echo "== stalled, retry $attempt: $*"
  done
  return 75
}

run --variant clean
run --variant full
if [[ "$1" == "--holdouts" ]]; then
  for f in Chiffon Denim Jacquard Nylon Polyester "Polyester (coated)" Satin Velveteen; do
    echo "== holdout $f $(date)"
    run --variant full --holdout "$f"
  done
fi
echo "== done $(date)"
