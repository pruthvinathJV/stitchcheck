#!/bin/zsh
# Day 2 baseline classifier: clean EfficientNet-B0 on the 8 training fabrics, validated on Linen.
# With --holdouts, also once per held-out training fabric.
#
#   scripts/train/day2.sh [--holdouts]
cd "$(dirname "$0")/../.."
source scripts/train/lib.sh

run --variant clean
if [[ "$1" == "--holdouts" ]]; then
  for f in Chiffon Denim Jacquard Nylon Polyester "Polyester (coated)" Satin Velveteen; do
    echo "== holdout $f $(date)"
    run --variant clean --holdout "$f"
  done
fi
echo "== done $(date)"
