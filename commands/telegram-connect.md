---
description: Connect the paired Telegram bridge to this orchestrator in Herdr
allowed-tools: Bash
---

Run this command from the orchestrator's Herdr pane:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/telegram_bridge.py" connect
```

Use the plugin root supplied for this command, regardless of the current project
directory. The helper replaces the orchestrator registration, makes it the default
target, and launches the bridge in a new Telegram tab without changing focus.
It uses the existing local bot setup and pairing. Report any error from the helper.
Before saying the bridge is running, check the returned pane with
`herdr pane process-info <pane>`; dispatching the command alone does not confirm
startup.
