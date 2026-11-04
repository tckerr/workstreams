---
name: orchestrate-workstreams
description: Act as the workstreams orchestrator in Codex when the user asks you to coordinate parallel Herdr workstreams, provision workers, or handle their merge reports.
---

# Orchestrate workstreams

Resolve this skill directory to its real filesystem path first (it may be a
symlink). Two directories above it is the workstreams plugin root. Read
`agents/orchestrator.md` there and adopt that role for this conversation. Read
the target repository's `.herdr/orchestrator.md` at startup if it exists. All
project-specific instructions belong in the target repository's `.herdr/`
files; do not copy them into the plugin.

The shared brief also serves Claude. Apply these Codex adaptations:

- Resolve `${CLAUDE_PLUGIN_ROOT}` and Claude plugin skill references against the
  absolute plugin root. Before spawning, read `skills/spawn-workstream/SKILL.md`
  there and run its bootstrap by absolute path from the target repository.
- Bind each spawn explicitly to this conversation. Prefix the bootstrap command
  with `HERDR_WS_ORCHESTRATOR_KIND=codex` and
  `HERDR_WS_ORCHESTRATOR_THREAD="$CODEX_THREAD_ID"`. If `CODEX_THREAD_ID` is not
  available, obtain this conversation's UUID from the host; never guess from a
  title or select the most recent conversation. If it cannot be determined,
  explain that reporting is unavailable before spawning.
- Bootstrap snapshots the orchestrator's `CODEX_HOME` before workers switch
  profiles. For a remote server, also supply
  `HERDR_WS_ORCHESTRATOR_REMOTE` and, when needed,
  `HERDR_WS_ORCHESTRATOR_AUTH_TOKEN_ENV` naming an environment variable holding
  its token. The thread must belong to that server. These are session routing
  values, not project defaults.
- Both Claude and Codex workers report to this thread through the supplied
  helper. Reply using `herdr agent prompt <agent> <message>` from the spawn
  summary; Codex has no Claude `SendMessage` tool. Keep the summary's agent,
  workspace, branch, and worktree identifiers.
- A `started`, `ready`, or `blocked` report keeps the stream open. Only a
  `merged` report triggers verification of the PR and clean worktree before
  teardown. A queue acknowledgement alone is not evidence a report was
  processed. Do not poll or retry an uncertain delivery automatically.
- Rename a Herdr tab and open browse tabs only when this session has the
  corresponding Herdr tab/pane context. A Codex conversation outside a terminal
  pane can still provision workers through Herdr. Report any repository startup
  step that needs unavailable pane context.
- Claude slash commands and the Telegram bridge are not Codex entry points.
  Use the referenced shell procedures for spawning and browsing; do not invoke
  Claude's self-compact or Telegram commands in Codex.

Keep implementation in workers. Changes to the workstream machinery or the
target repository's workstream configuration are the orchestrator's own work.
