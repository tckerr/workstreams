#!/usr/bin/env bash
#
# Show a file to the user, and open the orchestrator's browse tabs.
#
#   browse.sh show <file>        # open the file in a preview pane beside your work
#   browse.sh dir  <path>        # open a directory in this workspace's Files tab
#   browse.sh open-tabs [root]   # Files (yazi) + Git (lazygit) tabs, idempotent
#
# `show` splits a right-hand preview pane in your current tab and views the file
# there with a preview-only yazi (all width to the file, no navigation columns).
# It splits only when the tab has room: if the tab already holds another pane
# (a stream's artifact pane, say) a split would be cramped, so it falls back to
# revealing the file in the workspace's Files tab instead. It reuses a single
# preview pane across calls rather than piling up splits.
#
# `dir`, and the `show` fallback, drive the workspace's persistent Files tab,
# whose yazi is launched with a known --client-id so any agent in the workspace
# can move its view through yazi's DDS — no keystroke faking, no focus stealing.
# The id is derived from the herdr workspace id deterministically, so the
# launcher (open-tabs here, and bootstrap.sh for a stream) and the driver agree
# with no stashed state. bootstrap.sh inlines the SAME formula (cksum of the
# workspace id) — keep the two in step if you ever change it.
#
# Everything is scoped to the CURRENT herdr workspace (from HERDR_PANE_ID): the
# orchestrator acts on its primary checkout, a stream on its worktree. Nothing
# here crosses workspaces or steals focus.

set -euo pipefail

die() { printf 'browse: %s\n' "$*" >&2; exit 1; }

# Where this script lives, so the shipped preview-only yazi config resolves both
# from the installed plugin cache and the dev repo.
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PREVIEW_CFG="$HERE/yazi-preview"   # a config dir whose yazi.toml is preview-only
PREVIEW_LABEL=Preview              # names the split pane so `show` can reuse it

# Stable numeric yazi --client-id for a workspace id. emit-to needs a u64; cksum
# yields a deterministic number from the workspace-id string.
ws_client_id() { printf '%s' "$1" | cksum | awk '{print $1}'; }

# The current herdr workspace, from HERDR_PANE_ID (e.g. "w12:p1" -> "w12").
current_ws() {
  local ws=${HERDR_PANE_ID%%:*}
  [ -n "$ws" ] && [ "$ws" != "${HERDR_PANE_ID:-}" ] && printf '%s' "$ws"
}

# Absolute, normalised path without requiring the target to exist yet.
abspath() { python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$1"; }

# Best-effort: make sure a command is on PATH, installing via brew on macOS.
# Returns non-zero (without aborting the caller) if it still is not there.
ensure_cmd() {
  command -v "$1" >/dev/null 2>&1 && return 0
  [ "$(uname)" = Darwin ] || return 1
  brew install "$1" >/dev/null 2>&1 && command -v "$1" >/dev/null 2>&1
}

# Reveal a file / cd a directory in this workspace's Files-tab yazi.
emit() {
  local action=$1 path=$2
  [ -n "$path" ] || die "usage: browse.sh {show|dir} <path>"
  path=$(abspath "$path")
  [ -e "$path" ] || die "no such path: $path"
  command -v ya >/dev/null 2>&1 || die "the 'ya' CLI (yazi's companion) is not installed"
  local ws id out
  ws=$(current_ws) || die "not in a herdr workspace (HERDR_PANE_ID unset); nothing to drive"
  id=$(ws_client_id "$ws")
  if ! out=$(ya emit-to "$id" "$action" "$path" 2>&1); then
    case $out in
      *"not found"*) die "no drivable Files tab in workspace $ws (yazi client $id is not running). Reopen it: browse.sh open-tabs — or, in a stream, the Files tab from spawn was closed." ;;
      *) die "ya emit-to failed: $out" ;;
    esac
  fi
}

# The herdr tab that holds the current pane.
current_tab() {
  herdr pane get "$HERDR_PANE_ID" 2>/dev/null | jq -r '.result.pane.tab_id // empty'
}

# Show a single file in a preview pane split off the current pane. Splits only
# when the current tab has room (no pane other than this one and a reused
# preview); otherwise falls back to revealing the file in the Files tab.
show_file() {
  local path=$1
  [ -n "$path" ] || die "usage: browse.sh show <file>"
  path=$(abspath "$path")
  [ -e "$path" ] || die "no such path: $path"
  [ -d "$path" ] && die "show is for a file; use 'dir <path>' to open a folder in the Files tab"
  command -v ya >/dev/null 2>&1 || die "the 'ya' CLI (yazi's companion) is not installed"
  local ws tab
  ws=$(current_ws) || die "not in a herdr workspace (HERDR_PANE_ID unset); nothing to drive"
  tab=$(current_tab)
  [ -n "$tab" ] || die "could not resolve the current tab from $HERDR_PANE_ID"

  # Walk the panes in this tab: note a preview pane we own, count any others.
  local preview="" others=0 pid name
  while IFS=$'\t' read -r pid name; do
    [ "$pid" = "$HERDR_PANE_ID" ] && continue
    if [ "$name" = "$PREVIEW_LABEL" ]; then preview=$pid; continue; fi
    others=$((others + 1))
  done < <(herdr pane list --workspace "$ws" 2>/dev/null \
    | jq -r --arg t "$tab" '.result.panes[]? | select(.tab_id==$t) | [.pane_id, (.label // "")] | @tsv')

  if [ "$others" -ne 0 ]; then
    # Another pane is already open here; a third split would be cramped.
    emit reveal "$path"
    echo "revealed $(basename "$path") in the Files tab (this tab already has another pane)"
    return 0
  fi

  ensure_cmd yazi || die "yazi is not installed"
  # Reuse the one preview pane across calls: closing and re-splitting is simpler
  # and more reliable than quitting the running viewer in place.
  [ -n "$preview" ] && herdr pane close "$preview" >/dev/null 2>&1 || true
  local pane
  pane=$(herdr pane split "$HERDR_PANE_ID" --direction right --cwd "$(dirname "$path")" --no-focus \
    | jq -r '.result.pane.pane_id') || die "could not split a preview pane"
  herdr pane rename "$pane" "$PREVIEW_LABEL" >/dev/null 2>&1 || true
  herdr pane run "$pane" "YAZI_CONFIG_HOME=\"$PREVIEW_CFG\" yazi \"$path\"" >/dev/null 2>&1 || true
  echo "showing $(basename "$path") in a preview pane ($pane)"
}

# Open a labelled tab running a command in the workspace, unless one is already
# open. Idempotent by label; never steals focus.
open_tab() {
  local ws=$1 root=$2 label=$3 cmd=$4 existing pane
  existing=$(herdr tab list --workspace "$ws" 2>/dev/null \
    | jq -r --arg l "$label" '.result.tabs[]? | select(.label==$l) | .tab_id' | head -1)
  if [ -n "$existing" ]; then
    echo "$label tab already open ($existing)"
    return 0
  fi
  pane=$(herdr tab create --workspace "$ws" --cwd "$root" --label "$label" --no-focus \
    | jq -r '.result.root_pane.pane_id') || { echo "could not create $label tab"; return 0; }
  herdr pane run "$pane" "$cmd" >/dev/null 2>&1 || true
  echo "opened $label tab ($pane)"
}

# Files (yazi) + Git (lazygit) tabs on a checkout, in the current workspace.
open_tabs() {
  local root=${1:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}
  local ws
  ws=$(current_ws) || die "not in a herdr workspace (HERDR_PANE_ID unset); no tabs to open"
  if ensure_cmd yazi; then
    open_tab "$ws" "$root" Files "yazi --client-id $(ws_client_id "$ws") $root"
  else
    echo "yazi not installed; skipping Files tab"
  fi
  if ensure_cmd lazygit; then
    open_tab "$ws" "$root" Git "lazygit"
  else
    echo "lazygit not installed; skipping Git tab"
  fi
}

main() {
  local cmd=${1:-}
  shift || true
  case $cmd in
    open-tabs) open_tabs "$@" ;;
    show)      show_file "${1:-}" ;;
    dir)       emit cd "${1:-}" ;;
    *)         die "usage: browse.sh {open-tabs [root]|show <path>|dir <path>}" ;;
  esac
}

# Run only when executed directly, so the derivation helpers can be sourced
# (an `if` returns 0 when sourced, so `set -e` in the caller does not abort).
if [ "${BASH_SOURCE[0]:-}" = "${0:-}" ]; then
  main "$@"
fi
