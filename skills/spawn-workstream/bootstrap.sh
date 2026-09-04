#!/usr/bin/env bash
#
# Provisions one workstream and hands it its task.
#
# Usage: [HERDR_WS_DESC=<phrase>] [HERDR_WS_MODEL=<model>] [HERDR_WS_CONFIG_DIR=<dir>] \
#          bootstrap.sh <slug> [task...]
#
# Per-repo values come from .herdr/workstreams.sh in the target repo. With no
# task the agent is primed and left idle.

set -euo pipefail

die() { printf 'spawn-workstream: %s\n' "$*" >&2; exit 1; }

[ "${HERDR_ENV:-}" = 1 ] || die "not running inside herdr"
[ $# -ge 1 ] || die "usage: bootstrap.sh <slug> [task...]"

slug=$1; shift
task=$*

[[ $slug =~ ^[a-z][a-z0-9-]*$ ]] || die "slug must be lowercase, hyphenated: got '$slug'"

repo=$(git rev-parse --show-toplevel) || die "not in a git repository"

config="$repo/.herdr/workstreams.sh"
[ -f "$config" ] || die "no project config at $config; the orchestrator configures this before spawning (see the plugin's orchestrator brief)"
# shellcheck disable=SC1090
source "$config" || die "could not source $config"

: "${HERDR_WS_SECOND_PANE_LABEL:=}"
: "${HERDR_WS_SURVIVOR_GLOB:=target}"
: "${HERDR_WS_DEFAULT_MODEL:=claude-opus-4-8}"
: "${HERDR_WS_DEFAULT_CONFIG_DIR:=}"

branch="$slug"
label="${slug//-/ }"
# herdr caps agent names at 32 characters.
agent="${slug:0:32}"

# A worktree cut from a stale main carries other people's commits into the PR.
git -C "$repo" fetch origin --quiet
if [ "$(git -C "$repo" rev-parse main)" != "$(git -C "$repo" rev-parse origin/main)" ]; then
  if [ "$(git -C "$repo" symbolic-ref --quiet --short HEAD)" = main ]; then
    git -C "$repo" merge --ff-only origin/main --quiet \
      || die "main has diverged from origin/main; reconcile it before branching"
  else
    git -C "$repo" fetch origin main:main --quiet \
      || die "main has diverged from origin/main; reconcile it before branching"
  fi
fi

git -C "$repo" show-ref --quiet --verify "refs/heads/$branch" \
  && die "branch $branch already exists"

field() { python3 -c 'import json,sys;d=json.load(sys.stdin)["result"];print(eval("d"+sys.argv[1]))' "$1"; }

created=$(herdr worktree create --cwd "$repo" \
  --branch "$branch" --base main --label "$label" --no-focus) \
  || die "herdr worktree create failed"

workspace=$(printf '%s' "$created" | field '["workspace"]["workspace_id"]')
tab=$(printf '%s' "$created" | field '["tab"]["tab_id"]')
dev=$(printf '%s' "$created" | field '["root_pane"]["pane_id"]')
tree=$(printf '%s' "$created" | field '["worktree"]["path"]')

desc=${HERDR_WS_DESC:-${slug//-/ }}

herdr tab rename "$tab" Primary >/dev/null
herdr pane rename "$dev" "$desc" >/dev/null

second=""
if [ -n "$HERDR_WS_SECOND_PANE_LABEL" ]; then
  second=$(herdr pane split "$dev" --direction right --cwd "$tree" \
    --no-focus | field '["pane"]["pane_id"]')
  herdr pane rename "$second" "$HERDR_WS_SECOND_PANE_LABEL" >/dev/null
fi

model=${HERDR_WS_MODEL:-$HERDR_WS_DEFAULT_MODEL}
model_args=(--model "$model")

# herdr agent start takes no environment, but the pane is a shell and the agent
# starts inside it, so the profile has to be exported first. Empty means run
# under whatever profile the orchestrator is in.
config_dir=${HERDR_WS_CONFIG_DIR:-$HERDR_WS_DEFAULT_CONFIG_DIR}
if [ -n "$config_dir" ]; then
  [ -d "$config_dir" ] || die "no Claude profile at $config_dir"
  herdr pane run "$dev" "export CLAUDE_CONFIG_DIR=$config_dir" >/dev/null \
    || die "could not set the profile on $dev"
fi

herdr agent start "$agent" --kind claude --pane "$dev" \
  -- --dangerously-skip-permissions --effort high --agent workstreams:implementer \
  "${model_args[@]+"${model_args[@]}"}" >/dev/null \
  || die "the agent did not start in $dev; check the pane"

agent_pid() {
  herdr pane process-info --pane "$1" 2>/dev/null \
    | python3 -c 'import json,sys
try:
    ps = json.load(sys.stdin)["result"]["process_info"]["foreground_processes"]
except Exception:
    raise SystemExit
for p in ps:
    if p.get("argv0") == "claude":
        print(p["pid"]); break' 2>/dev/null || true
}

pid=$(agent_pid "$dev")
[ -n "$pid" ] || die "agent started in $dev but its pid was not found; check the pane"

# herdr pane run returns before the export is guaranteed to have landed, so a
# lost race would start the stream under the wrong profile and say nothing.
if [ -n "$config_dir" ]; then
  ps eww -p "$pid" 2>/dev/null | tr ' ' '\n' | grep -qxF "CLAUDE_CONFIG_DIR=$config_dir" \
    || die "agent in $dev is not under $config_dir; the profile export did not land before it started. Kill the agent and re-run."
fi

priming="Your worktree is $tree."
[ -n "$second" ] && priming="$priming
Your artifact pane is $second."

# herdr cannot address the orchestrator: it names only the agents it started,
# and the orchestrator is a plain pane. The cross-session socket reaches it.
if [ -n "${CLAUDE_CODE_MESSAGING_SOCKET:-}" ]; then
  priming="$priming
The orchestrator is uds:$CLAUDE_CODE_MESSAGING_SOCKET. Report there when you are
done, as your brief describes."
fi

if [ -n "$task" ]; then
  opening="$priming

Your task: $task"
  status="working on the task it was given"
else
  opening="$priming

You were given no task. Follow the last section of your brief: get ready,
report, and wait."
  status="idle, waiting for your instructions"
fi

herdr agent prompt "$agent" "$opening" >/dev/null \
  || die "the agent started but did not accept the prompt; prompt $agent by hand"

sock=""
[ -n "$pid" ] && [ -S "/tmp/cc-socks/$pid.sock" ] && sock="uds:/tmp/cc-socks/$pid.sock"

cat <<SUMMARY
workspace  $workspace
branch     $branch
worktree   $tree
dev pane   $dev "$desc" (agent: $agent)
artifact   ${second:-(none)}${second:+ ($HERDR_WS_SECOND_PANE_LABEL)}
survivor   $HERDR_WS_SURVIVOR_GLOB
model      $model
profile    ${config_dir:-(orchestrator's)}
address    ${sock:-(not resolved; find it with ListAgents)}
agent is   $status
SUMMARY
