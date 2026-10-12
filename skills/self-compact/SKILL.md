---
name: self-compact
description: Compact the current Claude Code session by sending /compact to its own herdr pane, then resume work automatically. Use when the user asks to self-compact, compact this session, free up context, or to "compact at a stopping point and continue".
---

# Self-compact

Compaction cannot run inside your own turn: `/compact` only runs between turns,
and after it finishes the session sits idle until something prompts it. So you
arm a detached watcher in your own herdr pane, then end your turn. The watcher
waits for that turn to end, submits `/compact`, waits for the compaction to
finish, and then submits a resume prompt, so you carry on without the user
typing "continue".

## When

If the user asked you to compact now, do it now. If they asked you to compact
"at a stopping point", keep working until you reach one: a finished step, with
work committed or saved and no tool call or edit half done. Do not stop at a
point where you are waiting on the user. Compact there instead of continuing.

## Write the resume prompt

The compaction summary keeps the gist, but it loses detail. Write the resume
prompt yourself, as a note to the session that comes after the compaction. Make
it a few sentences long. Say what the task is, what is done, and the concrete
next step, plus any file, branch or command it will need first. If the user only
asked you to compact and not to continue, use the resume prompt to restate where
things stand and tell that session to wait for the user.

## Arm it and stop

```bash
if [ -n "$HERDR_PANE_ID" ]; then
  "${CLAUDE_PLUGIN_ROOT}/skills/self-compact/self-compact.sh" "$HERDR_PANE_ID" "<resume prompt>"
else
  echo "Not running in a herdr pane (HERDR_PANE_ID is unset); cannot self-compact."
fi
```

Then end your turn at once, with one short line at most and no more tool calls.
The watcher is waiting for this turn to end, and anything more you do delays the
compaction. If the guard reports that the pane is unset, tell the user this
session is not inside herdr, and stop. The watcher logs to
`$TMPDIR/self-compact-<pane>.log`.
