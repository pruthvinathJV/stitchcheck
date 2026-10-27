#!/bin/zsh
# Stage 1 of the classifier search: screen training settings on the clean model, Linen validation only.
# The baseline (basic augmentation, lr 5e-4, seed 0) is the Day 2 clean run; s0-seed1 measures run-to-run noise.
# Stage 2 re-checks the top settings on held-out training fabrics before anything is locked.
#
#   scripts/train/sweep_stage1.sh
cd "$(dirname "$0")/../.."
source scripts/train/lib.sh

run --variant clean --tag s0-seed1 --seed 1
run --variant clean --tag s1-strong --aug strong
run --variant clean --tag s2-trivial --aug trivial
run --variant clean --tag s3-strong-lr2e-4 --aug strong --lr 2e-4
run --variant clean --tag s4-strong-bb0.1 --aug strong --backbone-lr-mult 0.1
run --variant clean --tag s5-strong --arch mobilenet_v3_large --aug strong
run --variant clean --tag s6-strong-lr2e-4 --arch convnext_tiny --aug strong --lr 2e-4
echo "== stage 1 done $(date)"
.venv/bin/python scripts/train/summarize.py
