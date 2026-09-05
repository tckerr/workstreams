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
#   WS_BRIEF_IN_PROMPT 1 when the implementer brief must be delivered in the
#                      opening prompt (the kind has no plugin-agent flag)

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
      WS_BRIEF_IN_PROMPT=0
      ;;
    codex)
      # Codex has no plugin-agent flag, so the implementer brief travels in the
      # opening prompt instead of via --agent. It also spells full autonomy and
      # reasoning effort differently from Claude.
      args=(--dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=high)
      WS_AGENT_ARGV0=codex
      WS_PROFILE_ENV=CODEX_HOME
      WS_BRIEF_IN_PROMPT=1
      ;;
  esac
  [ -n "$model" ] && args+=(--model "$model")

  WS_KIND=$kind
  WS_MODEL=$model
  WS_AGENT_ARGS=("${args[@]}")
}
