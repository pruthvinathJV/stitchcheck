# Shared by the training scripts: run the trainer under caffeinate; on an MPS stall (exit 75) resume, up to 4 tries.
run() {
  for attempt in 1 2 3 4; do
    caffeinate -i .venv/bin/python -u -m stitchcheck.classifier.train "$@" --resume 2>&1 | grep --line-buffered -vi warning
    code=${pipestatus[1]}
    [[ $code -ne 75 ]] && return $code
    echo "== stalled, retry $attempt: $*"
  done
  return 75
}
