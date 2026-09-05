# Per-repo workstream config for the herdr-orchestrator plugin.
#
# This is a Python 3 (stdlib-only) plus Bash project: no compiled build, no
# per-worktree runtime store, no live artifact. Sourced by
# skills/spawn-workstream/bootstrap.sh at spawn time from the main checkout.

# No live artifact to drop into and try — the implementer works in the dev pane
# only. Empty means no second pane.
HERDR_WS_SECOND_PANE_LABEL=""

# Nothing long-running serves a worktree here (no server, no build output dir),
# so teardown should find no survivors. This sentinel is intentionally a path
# fragment that never matches a real process.
HERDR_WS_SURVIVOR_GLOB=".herdr/no-survivors"

# Model default for streams.
HERDR_WS_DEFAULT_MODEL="claude-opus-4-8"

# The Claude profile is machine-specific and intentionally NOT committed here.
# The orchestrator supplies it at spawn via HERDR_WS_CONFIG_DIR.
HERDR_WS_DEFAULT_CONFIG_DIR=""

# Python 3.9+ stdlib only — no toolchain to pin, so no pane init.
HERDR_WS_PANE_INIT=""
HERDR_WS_PANE_INIT_CHECK=""
