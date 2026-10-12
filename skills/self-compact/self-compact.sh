#!/usr/bin/env bash
# self-compact.sh <pane> [resume prompt]
#
# Detaches a watcher that waits for the agent in <pane> to finish its current
# turn, submits /compact, waits for the compaction to finish, then submits the
# resume prompt so the agent carries on instead of sitting idle.
set -u

pane=${1:?usage: self-compact.sh <pane> [resume prompt]}
resume=${2:-"Compaction finished. Continue the task where you left off."}
log="${TMPDIR:-/tmp}/self-compact-${pane//:/_}.log"

watch() {
  echo "$(date '+%T') waiting for the current turn to end"
  herdr agent wait "$pane" --until idle --until done || exit 1

  echo "$(date '+%T') submitting /compact"
  if ! herdr agent prompt "$pane" "/compact" --wait --until idle --until done --timeout 1800000; then
    # Compaction may not register as "working"; fall back to waiting for idle.
    herdr agent wait "$pane" --until idle --until done --timeout 1800000 || exit 1
  fi

  echo "$(date '+%T') submitting resume prompt"
  herdr agent prompt "$pane" "$resume"
  echo "$(date '+%T') done"
}

nohup bash -c "$(declare -f watch); pane=$(printf %q "$pane"); resume=$(printf %q "$resume"); watch" \
  >"$log" 2>&1 </dev/null &
disown
echo "self-compact armed for $pane (log: $log)"
