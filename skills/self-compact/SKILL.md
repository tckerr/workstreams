---
name: self-compact
description: Compact the current Claude Code session by sending /compact to its own herdr pane. Use when the user asks to self-compact, compact this session, or free up context.
---

# Self-compact

Identify this session's own herdr pane and inject `/compact` into it. Claude Code
is running in that pane, so typing `/compact` and Enter there queues the compact
command as the next turn.

Run exactly this, then stop — do not add further output or tool calls, because the
queued `/compact` runs as soon as this turn ends:

```bash
if [ -n "$HERDR_PANE_ID" ]; then
  herdr pane run "$HERDR_PANE_ID" "/compact"
else
  echo "Not running in a herdr pane (HERDR_PANE_ID is unset); cannot self-compact."
fi
```

`HERDR_PANE_ID` is set by herdr to the current pane, so no discovery is needed.
`herdr pane run` sends the text and Enter together. If the guard reports the pane
is unset, tell the user this session is not inside herdr and stop.
