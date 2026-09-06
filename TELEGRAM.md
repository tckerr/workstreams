# Telegram

Drive workstream orchestration from your phone: send requests, receive the
orchestrator's questions and blocked-agent alerts, and reply straight back. The
optional bridge connects one private Telegram chat to the orchestrator agent and
to the streams it spawns. When the bridge is up, spawning a stream registers it as
its own phone target, so a stream that stalls on a question or a blocker reaches
you directly rather than pausing silently where nobody is watching; it is
unregistered again at teardown.

The bridge uses Python 3.9+ with no third-party packages. Run it inside Herdr on
the same Mac as the agents. It uses Telegram long polling, so there is no public
server or inbound port to configure. Automated tests cover account restrictions,
routing, receipt reactions, queuing, and interrupted delivery.

## One-time setup

Each user creates their own bot with [BotFather](https://t.me/BotFather), supplies
their own token during `setup`, and pairs their own Telegram account. There is no
shared project bot, token, or hardcoded account allowlist. Credentials, pairing,
and message history belong to the local instance, not the plugin or the repository.

```bash
python3 scripts/telegram_bridge.py setup
# Enter the bot token at the hidden prompt. Send the printed /pair command
# to your bot in a private Telegram chat.
```

The one-time pairing code binds a numeric Telegram user ID and private chat ID.
Every incoming message checks both; other accounts and groups are ignored, and the
code cannot be reused after pairing. The token and state live under
`~/.config/workstreams/telegram/`, outside the repo, with owner-only permissions.
Use `--state-dir PATH` before the subcommand to select another state directory, or
`WORKSTREAMS_TELEGRAM_STATE` for the same override; use one running bridge and one
paired account per bot token.

## Connecting the orchestrator

Once the bot is paired, run the plugin command in the orchestrator's session:

```text
/telegram-connect
```

The command resolves the bridge through `${CLAUDE_PLUGIN_ROOT}`, so it works from
the target repo. It registers `$HERDR_PANE_ID` as the default orchestrator, opens a
`Telegram` tab in `$HERDR_WORKSPACE_ID` without changing focus, and launches the
bridge there. Reconnecting replaces an older orchestrator registration; queued
requests for the replaced session are cancelled. Registering the same session
again preserves its queue and setup brief.

The bridge keeps running in that tab; stop it with Ctrl-C there. No launch agent is
installed, so the Mac must stay awake and connected. Restarting `run` preserves
pairing, registration, and queued work.

## On your phone

- Send a message to instruct the orchestrator, including requests to spawn
  workstreams using its existing project setup and rules.
- Reply to a notification to continue that thread — a stream's own alert routes
  your reply straight back to that stream, not to the orchestrator.
- `/status` lists every registered agent by name — the orchestrator and each live
  stream — with its delivery state.
- `/use NAME` makes a named agent the default for plain messages, and
  `/to NAME MESSAGE` sends one message to a named agent. A spawned stream's name
  is its slug, the same name shown on the spawn summary's `telegram` line.

Each accepted message gets a 👀 reaction rather than an acknowledgement message.
There is no separate delivery notification; the orchestrator's answer replies to
your original message, and a failed reaction does not block the answer.

## Bridge instructions for the orchestrator

The same protocol serves the orchestrator and any stream registered on the bridge.
The orchestrator learns it from a setup brief the bridge sends once when its
session first goes idle, pointing here rather than repeating it in the prompt; a
spawned stream is wired at spawn instead, so its opening prompt carries the helper
commands directly and the bridge does not re-brief it. Either way the agent reaches
the phone only through the bridge helper — the absolute `telegram_bridge.py`
invocation with its `--state-dir` — because its own terminal output never reaches
Telegram. Each delivered prompt is `Telegram request <id>:` followed by your
message; the agent treats it as a user request and answers, or asks a clarifying
question, with `reply` and the request ID. It uses `--file PATH` instead of `--text` for a long
response, and `notify` for an unsolicited question or alert. After replying it
finishes its turn rather than sleeping or polling; the bridge delivers the next
message later as a fresh prompt.

```bash
# Answer a delivered request (its ID comes in the prompt); the reply routes to your phone.
python3 scripts/telegram_bridge.py reply <request-id> --text 'Started the stream.'
# Use --file PATH instead of --text for a long or multiline response.
# Send an unsolicited question or alert; your reply routes back to the orchestrator.
python3 scripts/telegram_bridge.py notify --target orchestrator --text 'Which project should I use?'
python3 scripts/telegram_bridge.py status
```

Delivery waits for an idle or done orchestrator and checks its terminal and
foreground process identity, so a replaced or missing session never receives
queued commands. Later prompts contain only `Telegram request <id>:` and your
message text, with no message files. The setup brief is sent once per session and
is not repeated when the bridge restarts.

## Delivery semantics and limits

The bridge alerts once when a registered agent enters `blocked` at an approval or
question dialog, and once when its pane goes `unavailable` — the pane is gone or
its process was swapped. Handle a `blocked` dialog in Herdr; approval keystrokes
are not forwarded. Questions sent through `notify` can be answered from your phone
normally.

Only those states auto-alert. An agent that quietly ends its turn — including
after an API error — sits at `idle` or `done`, the normal ready-to-receive states,
indistinguishable from finished work, so it is never auto-alerted. That is why a
wired stream must notify the phone itself, from within its turn, when it wants to
ask something: run its `notify` helper before it stops. A hard crash mid-turn
cannot do that, so `blocked` and `unavailable` remain the automatic safety net for
the cases the agent cannot report on its own. Herdr may also report a usage-limited
session as `done`, which the bridge does not detect separately, so “delivered”
means the prompt was submitted, not answered.

Incoming updates are deduplicated and stored before forwarding. A crash or timeout
during delivery is marked `uncertain` and is not retried automatically, because the
agent may already have acted — check Herdr before resending. Outbound messages with
ambiguous delivery are marked `uncertain` too; inspect local `status` for those
counts. The bridge favors avoiding duplicate actions over automatic retries and
does not promise exactly-once delivery. Shared team access and additional chat
transports are possible future extensions.

```bash
python3 -m unittest discover -s tests -v
```
