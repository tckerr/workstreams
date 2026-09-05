# Resolves which agent a stream starts as: the kind, the model, and the argv
# handed to `herdr agent start ... -- <argv>`. Sourced by bootstrap.sh and kept
# separate so this decision is unit-testable without herdr or git.
#
# Inputs, from the environment (populated from the repo's .herdr/workstreams.sh
# plus any per-spawn overrides):
#   HERDR_WS_DEFAULT_KIND   per-repo default agent kind (claude if unset)
#   HERDR_WS_KIND           per-spawn override of the kind
#   HERDR_WS_DEFAULT_MODEL  per-repo default model, belonging to the default kind
#   HERDR_WS_MODEL          per-spawn override of the model
#
# Outputs, as globals set by resolve_agent:
#   WS_KIND            resolved agent kind (claude|codex)
#   WS_MODEL           resolved model, or empty to use the kind's own default
#   WS_AGENT_ARGS      array of args to pass after `--` to herdr agent start
#   WS_AGENT_ARGV0     the process name to look for when finding the agent's pid
#   WS_PROFILE_ENV     env var that points the agent at a profile directory
#   WS_BRIEF           how the implementer brief reaches the agent:
#                        agent-flag  carried by --agent (Claude)
#                        agents-md   written as an AGENTS.md the agent loads, with
#                                    a prompt fallback (Codex)
#   WS_PERMISSION_MODE wire attestation of this launch's permission class

resolve_agent() {
  local default_kind=${HERDR_WS_DEFAULT_KIND:-claude}
  local kind=${HERDR_WS_KIND:-$default_kind}

  case "$kind" in
    claude | codex) ;;
    *)
      printf 'resolve-agent: unsupported agent kind %s (want claude or codex)\n' "$kind" >&2
      return 1
      ;;
  esac

  # An explicit HERDR_WS_MODEL always wins. The per-repo default model belongs to
  # the default kind, so overriding only the kind for one spawn falls back to that
  # kind's own default rather than forcing a mismatched model onto it.
  local model=""
  if [ -n "${HERDR_WS_MODEL:-}" ]; then
    model=$HERDR_WS_MODEL
  elif [ "$kind" = "$default_kind" ]; then
    model=${HERDR_WS_DEFAULT_MODEL:-}
  fi

  local -a args=()
  case "$kind" in
    claude)
      args=(--dangerously-skip-permissions --effort high --agent workstreams:implementer)
      WS_AGENT_ARGV0=claude
      WS_PROFILE_ENV=CLAUDE_CONFIG_DIR
      WS_BRIEF=agent-flag
      WS_PERMISSION_MODE=bypass
      ;;
    codex)
      # Codex has no plugin-agent flag. It does read an AGENTS.md project doc on
      # its own, so the implementer brief is written there and bootstrap raises
      # the project-doc byte cap so the whole brief loads. Codex also spells full
      # autonomy and reasoning effort differently from Claude.
      args=(
        --dangerously-bypass-approvals-and-sandbox
        -c model_reasoning_effort=high
        -c project_doc_max_bytes=1048576
      )
      WS_AGENT_ARGV0=codex
      WS_PROFILE_ENV=CODEX_HOME
      WS_BRIEF=agents-md
      # Must track the bypass flag above, never the orchestrator's inbound policy.
      WS_PERMISSION_MODE=bypass
      ;;
  esac
  [ -n "$model" ] && args+=(--model "$model")

  WS_KIND=$kind
  WS_MODEL=$model
  WS_AGENT_ARGS=("${args[@]}")
}

# Delivers the implementer brief to a Codex stream. Codex reads an AGENTS.md
# project doc on its own, so the brief (YAML frontmatter stripped) is written
# there and kept out of git, so no stream commits it into its PR. If the project
# already ships its own AGENTS.md we leave it untouched and hand the brief back
# to ride in front of the opening prompt instead.
#
# Args:  <brief_file> <worktree>
# Sets:  WS_BRIEF_VIA     human-readable delivery method, for the spawn summary
#        WS_BRIEF_PREFIX  text to prepend to the opening prompt (empty unless the
#                         prompt fallback is taken)
deliver_codex_brief() {
  local brief_file=$1 tree=$2
  local reporting_file
  reporting_file="$(dirname "${BASH_SOURCE[0]}")/../../agents/codex-reporting.md"
  [ -f "$brief_file" ] || {
    printf 'resolve-agent: cannot deliver the implementer brief: %s is missing\n' "$brief_file" >&2
    return 1
  }
  local body
  # Substitute only the reporting section; Claude still loads the original brief.
  body=$(awk '
    FILENAME==ARGV[1] {report=report $0 "\n"; next}
    FNR==1 && $0=="---" {f=1; next}
    f && $0=="---" {f=0; next}
    f {next}
    /^## Reporting done$/ {printf "%s\n", report; skip=1; next}
    skip && /^## / {skip=0}
    !skip {print}
  ' "$reporting_file" "$brief_file") || {
    printf 'resolve-agent: could not read the implementer brief at %s\n' "$brief_file" >&2
    return 1
  }

  local agents_md="$tree/AGENTS.md"
  if [ -e "$agents_md" ]; then
    WS_BRIEF_VIA="opening prompt (project ships its own AGENTS.md)"
    WS_BRIEF_PREFIX="$body

----------------------------------------------------------------------

"
    return 0
  fi

  printf '%s\n' "$body" >"$agents_md" || {
    printf 'resolve-agent: could not write the brief to %s\n' "$agents_md" >&2
    return 1
  }
  # A worktree reads ignore patterns from the shared common git dir, not a
  # per-worktree one, so the exclude lands there. It is idempotent and only ever
  # matches this generated file — this branch runs solely when the project has no
  # AGENTS.md of its own.
  local common
  common=$(git -C "$tree" rev-parse --git-common-dir)
  case "$common" in /*) ;; *) common="$tree/$common" ;; esac
  local exclude="$common/info/exclude"
  mkdir -p "$common/info"
  grep -qxF '/AGENTS.md' "$exclude" 2>/dev/null || printf '/AGENTS.md\n' >>"$exclude"
  WS_BRIEF_VIA="AGENTS.md"
  WS_BRIEF_PREFIX=""
}
