---
name: browse
description: Put a file or folder in front of the user without them navigating — open a single file in a preview pane beside your work, or move the Files-tab browser to a path. Use when the user asks to see, show, open, pull up, or reveal a file or folder, or to point them at where something lives. Works for both a stream (its worktree) and the orchestrator (the primary checkout).
---

# Show a file to the user

When the user asks to see a single file — or you want to put one in front of
them — open it in a preview pane next to your work:

    "${CLAUDE_PLUGIN_ROOT}/scripts/browse.sh" show <file>

This splits a right-hand pane in your current tab and views the file there with
a preview-only yazi: the whole pane is the file, syntax-highlighted, no
navigation columns. It reuses one preview pane across calls, so asking for one
file after another replaces the view rather than piling up splits.

It splits only when the tab has room. If the tab already holds another pane — a
stream's artifact pane, say — a third split would be cramped, so `show` falls
back to revealing the file in the workspace's Files tab instead and tells you it
did. The path may be relative or absolute, and must be an existing file (for a
folder, use `dir`).

# Move the file browser

Every workspace also has a persistent Files tab running yazi — the orchestrator's
on its primary checkout, a stream's on its worktree — launched with a known
client-id so you can move its view for the user through yazi's DDS. To open a
directory there (the browser cd's into it):

    "${CLAUDE_PLUGIN_ROOT}/scripts/browse.sh" dir <path>

This is the shared Files tab the user browses by hand, so it moves their view —
which is the point when they ask where something lives. Everything is scoped to
your own workspace; nothing crosses into another stream's.

## When it cannot drive the tab

If a command reports that no drivable Files tab is running, the tab was closed or
opened without the client-id. A stream cannot reopen its own Files tab with the
right id; say so rather than opening a bare one. The orchestrator can reopen its
browse tabs with `"${CLAUDE_PLUGIN_ROOT}/scripts/browse.sh" open-tabs`.
