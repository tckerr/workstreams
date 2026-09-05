#!/usr/bin/env bash
#
# Provisions one workstream and hands it its task.
#
# Usage: [HERDR_WS_DESC=<phrase>] [HERDR_WS_KIND=<claude|codex>] [HERDR_WS_MODEL=<model>] \
#          [HERDR_WS_CONFIG_DIR=<dir>] bootstrap.sh <slug> [task...]
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
: "${HERDR_WS_DEFAULT_KIND:=claude}"
: "${HERDR_WS_DEFAULT_MODEL:=claude-opus-4-8}"
: "${HERDR_WS_DEFAULT_CONFIG_DIR:=}"
: "${HERDR_WS_PANE_INIT:=}"
: "${HERDR_WS_PANE_INIT_CHECK:=}"

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

# A file browser for the worktree, in its own tab so it stays out of the
# two-pane budget the implementer works under. yazi's preview pane reads a source
# tree well; install it if the machine lacks it. This is a convenience, not part
# of the stream, so every step here is best-effort and never aborts the spawn.
files=""
have_yazi=0
if command -v yazi >/dev/null 2>&1; then
  have_yazi=1
elif [ "$(uname)" = Darwin ]; then
  brew install yazi >/dev/null 2>&1 && have_yazi=1
fi
if [ "$have_yazi" = 1 ]; then
  files=$(herdr tab create --workspace "$workspace" --cwd "$tree" --label Files \
    --no-focus | field '["root_pane"]["pane_id"]') || files=""
  [ -n "$files" ] && { herdr pane run "$files" "yazi $tree" >/dev/null 2>&1 || true; }
fi

# A git viewer for the branch, in its own tab. lazygit shows the working tree —
# unstaged and staged changes — and the branch's commit log, with diffs, at a
# glance. Same best-effort contract as the file browser above.
gitview=""
have_lazygit=0
if command -v lazygit >/dev/null 2>&1; then
  have_lazygit=1
elif [ "$(uname)" = Darwin ]; then
  brew install lazygit >/dev/null 2>&1 && have_lazygit=1
fi
if [ "$have_lazygit" = 1 ]; then
  gitview=$(herdr tab create --workspace "$workspace" --cwd "$tree" --label Git \
    --no-focus | field '["root_pane"]["pane_id"]') || gitview=""
  [ -n "$gitview" ] && { herdr pane run "$gitview" "lazygit" >/dev/null 2>&1 || true; }
fi

# A bare shell in the worktree, for the user to poke around in — a one-off
# command, a grep, a look at a file — without interrupting the agent's dev pane.
# The tab's own pane is already a shell, so there is nothing to launch.
shell=$(herdr tab create --workspace "$workspace" --cwd "$tree" --label Shell \
  --no-focus | field '["root_pane"]["pane_id"]') || shell=""

# Resolve which agent this stream starts as — kind, model, and launch argv. The
# decision lives in a sibling helper so it can be tested without herdr or git.
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd) || die "could not locate the skill directory"
# shellcheck disable=SC1091
source "$here/resolve-agent.sh" || die "could not source $here/resolve-agent.sh"
resolve_agent || die "could not resolve the agent kind (HERDR_WS_KIND=${HERDR_WS_KIND:-} HERDR_WS_DEFAULT_KIND=$HERDR_WS_DEFAULT_KIND)"
kind=$WS_KIND
model=$WS_MODEL

# herdr agent start takes no environment, but the pane is a shell and the agent
# starts inside it, so the profile has to be exported first. The env var that
# names a profile differs by kind (CLAUDE_CONFIG_DIR / CODEX_HOME). Empty means
# run under whatever profile the orchestrator is in.
config_dir=${HERDR_WS_CONFIG_DIR:-$HERDR_WS_DEFAULT_CONFIG_DIR}
if [ -n "$config_dir" ]; then
  [ -d "$config_dir" ] || die "no $kind profile at $config_dir"
  herdr pane run "$dev" "export $WS_PROFILE_ENV=$config_dir" >/dev/null \
    || die "could not set the profile on $dev"
fi

# The pane is a shell and the agent inherits its environment, so a toolchain the
# project needs — a node version, a language runtime — has to be selected here,
# before the agent starts. A stream on the wrong one reports failures that are
# really the environment, and the user chases them as if they were the change.
if [ -n "$HERDR_WS_PANE_INIT" ]; then
  herdr pane run "$dev" "$HERDR_WS_PANE_INIT" >/dev/null \
    || die "could not run the project's pane init on $dev"
fi

herdr agent start "$agent" --kind "$kind" --pane "$dev" \
  -- "${WS_AGENT_ARGS[@]}" >/dev/null \
  || die "the agent did not start in $dev; check the pane"

agent_pid() {
  herdr pane process-info --pane "$1" 2>/dev/null \
    | WS_AGENT_ARGV0="$WS_AGENT_ARGV0" python3 -c 'import json,os,sys
want = os.environ["WS_AGENT_ARGV0"]
try:
    ps = json.load(sys.stdin)["result"]["process_info"]["foreground_processes"]
except Exception:
    raise SystemExit
for p in ps:
    if p.get("argv0") == want:
        print(p["pid"]); break' 2>/dev/null || true
}

pid=$(agent_pid "$dev")
[ -n "$pid" ] || die "agent started in $dev but its pid was not found; check the pane"

# herdr pane run returns before the export is guaranteed to have landed, so a
# lost race would start the stream under the wrong profile and say nothing.
if [ -n "$config_dir" ]; then
  ps eww -p "$pid" 2>/dev/null | tr ' ' '\n' | grep -qxF "$WS_PROFILE_ENV=$config_dir" \
    || die "agent in $dev is not under $config_dir; the profile export did not land before it started. Kill the agent and re-run."
fi

# Same lost race as the profile export: pane run returns before the init has
# landed, and a stream on the default toolchain says nothing about it.
if [ -n "$HERDR_WS_PANE_INIT_CHECK" ]; then
  ps eww -p "$pid" 2>/dev/null | grep -qF "$HERDR_WS_PANE_INIT_CHECK" \
    || die "agent in $dev did not inherit the project's pane init (nothing matching '$HERDR_WS_PANE_INIT_CHECK' in its environment); the init did not land before it started. Kill the agent and re-run."
fi

# A Claude stream carries the implementer brief through --agent; a Codex stream
# has no such flag, so the brief (minus its YAML frontmatter) rides in front of
# the opening prompt instead.
brief_prefix=""
if [ "$WS_BRIEF_IN_PROMPT" = 1 ]; then
  brief_file="$here/../../agents/implementer.md"
  [ -f "$brief_file" ] || die "cannot deliver the implementer brief: $brief_file is missing"
  brief_body=$(awk 'NR==1 && $0=="---"{f=1;next} f && $0=="---"{f=0;next} !f' "$brief_file") \
    || die "could not read the implementer brief at $brief_file"
  brief_prefix="$brief_body

----------------------------------------------------------------------

"
fi

priming="Your worktree is $tree."
[ -n "$second" ] && priming="$priming
Your artifact pane is $second."
[ -n "$files" ] && priming="$priming
A file browser (yazi) is already open in the Files tab, pane $files. It is
there for the user; do not open another unless you have a real reason to."
[ -n "$gitview" ] && priming="$priming
A git viewer (lazygit) is already open in the Git tab, pane $gitview —
working-tree changes and the branch's commits. Same as above: reuse it."
[ -n "$shell" ] && priming="$priming
A bare Shell tab, pane $shell, is there for the user to poke around in. Leave
it for them; do your own work in the dev pane."

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

opening="$brief_prefix$opening"

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
files      ${files:-(none)}${files:+ (yazi, Files tab)}
git        ${gitview:-(none)}${gitview:+ (lazygit, Git tab)}
shell      ${shell:-(none)}${shell:+ (Shell tab)}
survivor   $HERDR_WS_SURVIVOR_GLOB
kind       $kind
model      ${model:-(the agent's default)}
profile    ${config_dir:-(the orchestrator)}
address    ${sock:-(not resolved; find it with ListAgents)}
agent is   $status
SUMMARY
