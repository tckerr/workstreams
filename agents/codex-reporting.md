## Reporting to the orchestrator

Your first message gives you a shell command for reporting to the orchestrator.
Use that command for every report, including setup faults. It sends the framed
message for you; Codex has no `SendMessage` tool. Do not write plain text to a
`uds:` socket.

Before starting the task, run the command with `send started --message '...'` to
acknowledge it and say what you are beginning. With no task, acknowledge that you
are getting ready and will wait for instructions.

Follow the project's definition of done and any instruction to wait for review.
If your work ends at an open PR, send `ready` with the branch, PR URL, and a short
summary, explicitly saying it is not merged. The stream must stay open for review.
Use `blocked` for setup faults or other problems that need the orchestrator.

Only after the merge has landed and nothing is uncommitted, send `merged` with
the branch, PR URL, one line on what landed, and confirmation that teardown is
safe. Send that report once. Do not update the worktree from main or remove it
yourself; the orchestrator verifies the merge and tears it down. Keep any
artifact running and wait.

Use `--message-file /path/to/report.txt` (or `--message-file -` for stdin) for
multiline text or text containing shell metacharacters. The helper saves the last
report in its private directory before sending. A successful exit means the
socket write completed, not that the orchestrator has read or acted on it.
If the command is missing or fails, show the PR URL and summary in your dev pane,
including the error and saved report path when available. Do not loop on retries;
delivery may already have happened.
