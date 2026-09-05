# Telegram

Drive workstream orchestration from your phone: send requests, receive the
orchestrator's questions and blocked-agent alerts, and reply straight back. The
optional bridge connects one private Telegram chat to the orchestrator agent.
Telegram is a capability of the orchestrator only — streams are never wired to the
phone; the orchestrator relays to and from them through Herdr as usual.

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

Once the bot is paired, ask the orchestrator to connect Telegram. It registers its
own pane as the default target, opens a new `Telegram` tab in its Herdr workspace,
and runs the bridge there so it stays clear of its working pane (you can also run
these commands yourself):

```bash
python3 scripts/telegram_bridge.py register orchestrator --pane "$HERDR_PANE_ID" --default
herdr tab create --workspace "$HERDR_WORKSPACE_ID" --label Telegram --no-focus
herdr pane run <new tab pane> "python3 scripts/telegram_bridge.py run"
```

The bridge keeps running in that tab; stop it with Ctrl-C there. No launch agent is
installed, so the Mac must stay awake and connected. Restarting `run` preserves
pairing, registration, and queued work.

## On your phone

- Send a message to instruct the orchestrator, including requests to spawn
  workstreams using its existing project setup and rules.
- Reply to a notification to continue that thread.
- `/status` lists the orchestrator's delivery state.

Each accepted message gets a 👀 reaction rather than an acknowledgement message.
There is no separate delivery notification; the orchestrator's answer replies to
your original message, and a failed reaction does not block the answer.

## Bridge instructions for the orchestrator

The setup brief points the orchestrator here rather than repeating the protocol in
the prompt. The orchestrator reaches the phone only through the bridge helper — the
same `telegram_bridge.py` invocation used above, including any `--state-dir` —
because its own terminal output never reaches Telegram. After setup, each delivered
prompt is `Telegram request <id>:` followed by your message; the orchestrator
treats it as a user request and answers, or asks a clarifying question, with
`reply` and the request ID. It uses `--file PATH` instead of `--text` for a long
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

The bridge alerts once when the orchestrator enters `blocked` at an approval or
question dialog. Handle these in Herdr; approval keystrokes are not forwarded.
Questions sent through `notify` can be answered from your phone normally. Herdr may
report a usage-limited session as `done`, which the bridge does not detect
separately, so “delivered” means the prompt was submitted, not answered.

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
