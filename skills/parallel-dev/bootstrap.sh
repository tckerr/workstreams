#!/usr/bin/env bash
#
# Provisions one parallel development stream and hands it its task.
#
# Everything here is mechanical and identical every time, which is exactly why
# it is a script: an orchestrator that re-derives these steps tends to keep
# going and start doing the work itself.
#
# The plugin carries no project knowledge. Per-project values come from
# `.herdr/parallel-dev.sh` in the target repo, sourced below. See README.md for
# the contract.
#
# Usage: [HPD_DESC=<phrase>] [HPD_MODEL=<model>] [HPD_CONFIG_DIR=<dir>] \
#          bootstrap.sh <slug> [task...]
#
# HPD_DESC names the dev pane. A short phrase saying what is being built, in the
# user's terms rather than the branch's. Unset, it falls back to the slug.
#
# HPD_MODEL pins the stream to a model. Unset, it takes the project's
# HPD_DEFAULT_MODEL so every stream runs on the same model whoever started it.
#
# HPD_CONFIG_DIR picks the Claude profile the stream runs under. Unset, it takes
# the project's HPD_DEFAULT_CONFIG_DIR.
#
# With no task the stream agent is primed and left idle, waiting for the user
# to tell it what to build. That is the default on purpose: a stream that
# starts guessing from a topic name is worse than one that waits.

set -euo pipefail

die() { printf 'parallel-dev: %s\n' "$*" >&2; exit 1; }

[ "${HERDR_ENV:-}" = 1 ] || die "not running inside herdr"
[ $# -ge 1 ] || die "usage: bootstrap.sh <slug> [task...]"

slug=$1; shift
task=$*

[[ $slug =~ ^[a-z][a-z0-9-]*$ ]] || die "slug must be lowercase, hyphenated: got '$slug'"

repo=$(git rev-parse --show-toplevel) || die "not in a git repository"

# The per-project contract. Its absence is not an error to paper over with
# defaults: the orchestrator is meant to notice and walk the user through
# writing it. If a spawn reaches here without it, stop loudly.
config="$repo/.herdr/parallel-dev.sh"
[ -f "$config" ] || die "no project config at $config; the orchestrator configures this before spawning (see the plugin's orchestrator brief)"
# shellcheck disable=SC1090
source "$config" || die "could not source $config"

# Defaults fill only what the project left unset.
: "${HPD_BRANCH_PREFIX:=}"
: "${HPD_LABEL_PREFIX:=}"
: "${HPD_STORE_SUBDIR:=}"
: "${HPD_STORE_ENV:=}"
: "${HPD_SECOND_PANE_LABEL:=}"
: "${HPD_SURVIVOR_GLOB:=target}"
: "${HPD_DEFAULT_MODEL:=claude-opus-4-8}"
: "${HPD_DEFAULT_CONFIG_DIR:=$HOME/.claude-field}"

branch="${HPD_BRANCH_PREFIX}${slug}"
label="${HPD_LABEL_PREFIX}${slug//-/ }"
# Agent names are capped at 32 characters by herdr.
agent="${slug:0:32}"

# A worktree cut from a stale main produces a PR full of other people's
# commits, so this is checked rather than assumed.
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

# A per-worktree store, if the project isolates one. Empty subdir means the
# project keeps no isolated store and the implementer needs no store path.
store=""
[ -n "$HPD_STORE_SUBDIR" ] && store="$tree/$HPD_STORE_SUBDIR"

# The pane is what the user reads when scanning workspaces, so it says what is
# being built rather than that a dev agent lives here. They can see that.
desc=${HPD_DESC:-${slug//-/ }}

herdr tab rename "$tab" Primary >/dev/null
herdr pane rename "$dev" "$desc" >/dev/null

# The second pane holds the project's live artifact — a TUI, a dev server, a
# running game — that the user drops into. Projects without one skip it. The
# implementer starts the artifact per its project's definition; bootstrap only
# opens the pane and carries the store env onto it.
second=""
if [ -n "$HPD_SECOND_PANE_LABEL" ]; then
  pane_env_args=()
  [ -n "$HPD_STORE_ENV" ] && [ -n "$store" ] && pane_env_args=(--env "$HPD_STORE_ENV=$store")
  second=$(herdr pane split "$dev" --direction right --cwd "$tree" \
    "${pane_env_args[@]+"${pane_env_args[@]}"}" --no-focus | field '["pane"]["pane_id"]')
  herdr pane rename "$second" "$HPD_SECOND_PANE_LABEL" >/dev/null
fi

# `agent start` over typing the alias into a shell: it waits for the agent to
# be ready and cannot be garbled by zsh autosuggestions. `--agent` loads the
# implementer brief from the plugin, so the stream knows how to work in a
# worktree without spending a turn reading a file. Streams run on a named model
# rather than whatever the CLI happens to default to, so a stream started today
# behaves like one started last week.
model=${HPD_MODEL:-$HPD_DEFAULT_MODEL}
model_args=(--model "$model")

# Streams run under a named Claude profile, the way a `cldf`-style alias does.
# `herdr agent start` takes no environment, but the pane is a shell and the
# agent starts inside it, so exporting first is how the setting reaches the
# process. It is verified below, because the export can lose the race.
config_dir=${HPD_CONFIG_DIR:-$HPD_DEFAULT_CONFIG_DIR}
[ -d "$config_dir" ] || die "no Claude profile at $config_dir"
herdr pane run "$dev" "export CLAUDE_CONFIG_DIR=$config_dir" >/dev/null \
  || die "could not set the profile on $dev"

herdr agent start "$agent" --kind claude --pane "$dev" \
  -- --dangerously-skip-permissions --effort high --agent herdr:implementer \
  "${model_args[@]+"${model_args[@]}"}" >/dev/null \
  || die "the agent did not start in $dev; check the pane"

# Find the claude process in a pane by argv0. Used to confirm the profile below
# and to resolve the report socket at the end, so it lives here once.
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

# The profile reaches the agent through the `export` dispatched to the pane
# above, then the agent starts in that same shell. `herdr pane run` returns
# before the export is guaranteed to have landed, so a lost race would bring the
# stream up under the default profile — the very login this switch exists to
# spare — and nothing downstream would say so. Confirm it took, and fail loudly.
pid=$(agent_pid "$dev")
[ -n "$pid" ] || die "agent started in $dev but its pid was not found; check the pane"
ps eww -p "$pid" 2>/dev/null | tr ' ' '\n' | grep -qxF "CLAUDE_CONFIG_DIR=$config_dir" \
  || die "agent in $dev is not under $config_dir; the profile export did not land before it started. Kill the agent and re-run."

# The worktree carries the procedure through the implementer brief, so the
# prompt carries only what varies per stream.
priming="Your worktree is $tree."
[ -n "$store" ] && priming="$priming
Your store is $store."
[ -n "$second" ] && priming="$priming
Your artifact pane is $second."

# How the stream reports back when its work lands. herdr cannot address the
# orchestrator: only agents it started have names, and the orchestrator is a
# plain pane. Claude's cross-session socket can, and the address is stable for
# the life of the orchestrator's session.
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
  # Primed but idle. It has read its instructions and set up its store, so the
  # user's first message can be the task itself rather than a briefing.
  opening="$priming

You were given no task. Follow the last section of your brief: get ready,
report, and wait."
  status="idle, waiting for your instructions"
fi

herdr agent prompt "$agent" "$opening" >/dev/null \
  || die "the agent started but did not accept the prompt; prompt $agent by hand"

# A stream under a different profile is invisible to the orchestrator's
# ListAgents — discovery is per-profile, though the sockets share one directory
# and an explicit address still reaches. So resolve the socket here, reusing the
# pid found for the profile check, rather than leaving the orchestrator to hunt.
sock=""
[ -n "$pid" ] && [ -S "/tmp/cc-socks/$pid.sock" ] && sock="uds:/tmp/cc-socks/$pid.sock"

cat <<SUMMARY
workspace  $workspace
branch     $branch
worktree   $tree
dev pane   $dev "$desc" (agent: $agent)
artifact   ${second:-(none)}${second:+ ($HPD_SECOND_PANE_LABEL)}
store      ${store:-(none)}
survivor   $HPD_SURVIVOR_GLOB
model      $model
profile    $config_dir
address    ${sock:-(not resolved; find it with ListAgents)}
agent is   $status
SUMMARY
