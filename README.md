# workstreams

Run several Claude sessions on one repo at once, each in its own git worktree, so
they never trip over each other's runs. One session, the **orchestrator**, starts
the streams and cleans them up. Each stream is an **implementer** working in
isolation, reporting back when its branch lands.

The plugin holds no knowledge of any particular repo. How to build, how to test,
whether there is a live artifact to keep running, and what "done" means all come
from the repo itself. So the same orchestrator serves very different repos
without changing.

## Install

```bash
claude plugin marketplace add tckerr/claude-plugins
claude plugin install workstreams@tckerr
```

Run a session as the orchestrator:

```bash
claude --agent workstreams:orchestrator
```

You can also spawn from any session with `/workstreams:spawn-workstream <task>`,
but a dedicated orchestrator session is what receives the done-reports and tears
streams down.

## What each repo supplies

A repo becomes ready for workstreams when it has a `.herdr/` directory with its
specs. On the first spawn the orchestrator notices they are missing and walks you
through writing them, including whether to commit them or keep them local, so you
rarely write them by hand.

`.herdr/workstreams.sh` holds the mechanical values `bootstrap.sh` sources.
`bootstrap.sh` is the authoritative list; the keys are:

| Key | What it sets |
| --- | --- |
| `HERDR_WS_SECOND_PANE_LABEL` | Label for the live-artifact pane. Its presence is what opens a second pane at all; empty means dev pane only. |
| `HERDR_WS_SURVIVOR_GLOB` | The path fragment the teardown check looks for to catch a process still serving a removed worktree. |
| `HERDR_WS_DEFAULT_CONFIG_DIR` | The Claude profile streams run under, so they can spend a separate usage allowance. Leave it empty to run them under the orchestrator's own profile. |
| `HERDR_WS_DEFAULT_MODEL` | The model streams run on. |
| `HERDR_WS_PANE_INIT` | Command run in the pane before the agent starts, to select a toolchain the project pins. Empty leaves the pane as the machine leaves it. |
| `HERDR_WS_PANE_INIT_CHECK` | String that must appear in the started agent's environment for the init to count as landed. Empty skips the check. |

Three keys override the defaults for a single spawn, on the command line:
`HERDR_WS_DESC` (labels the dev pane), `HERDR_WS_MODEL`, and `HERDR_WS_CONFIG_DIR`.

`.herdr/implementer.md` holds the implementer's instructions for this repo, the
part a shell file cannot carry: how to build, how to test, how to keep the
artifact up, how to isolate the repo's runtime state in a worktree if it keeps
any, the house rules, and the definition of done.

`.herdr/orchestrator.md` is optional. Add it only when the repo needs the
orchestrator to do something particular, like a house reporting style or a
special teardown step.

## The definition of done belongs to the repo

The plugin never decides how a stream ships. It runs the worktree, keeps the
artifact alive, commits as it goes, and reports back. But the sequence that turns
a finished change into a merged commit is the repo's to define in its
`.herdr/implementer.md`: how it tests, how it opens and merges a PR, whether it
waits for you first, and what it does to the worktree afterward. One repo squashes
and resets to `origin/main`; another does not. The implementer follows whatever
the repo says, then sends the orchestrator the one message the repo does not own:
that the merge landed and teardown is safe.

## Setup faults go back to the orchestrator

A stream that cannot build, or cannot isolate its state, does not patch its way
around the problem. It sends the orchestrator what it ran and what broke, and
waits. The orchestrator repairs the repo's spec, commits it, and tells the
stream. Where the report does not say enough to place the fault, or the fix is a
decision rather than a repair, it asks you instead.

A fix in the spec is one the next stream inherits. A stream that quietly pointed
at the machine-wide store, or skipped its isolation export, would get past its
own error and leave the fault there for every stream after it.

## Branch and workspace names

A stream's slug becomes the branch name, and its spaced form the workspace label.
There are no configurable prefixes. A repo that wants a naming convention
expresses it in its own tooling, not here.

## Telegram

Drive workstream orchestration from your phone: send requests, receive the
orchestrator's questions and blocked-agent alerts, and reply straight back. The
optional bridge connects one private Telegram chat to the orchestrator agent.
Telegram is a capability of the orchestrator only — streams are never wired to the
phone; the orchestrator relays to and from them through Herdr as usual.

The bridge uses Python 3.9+ with no third-party packages. Run it inside Herdr on
the same Mac as the agents. It uses Telegram long polling, so there is no public
server or inbound port to configure. Automated tests cover account restrictions,
routing, receipt reactions, queuing, and interrupted delivery.

### One-time setup

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

### Connecting the orchestrator

Once the bot is paired, ask the orchestrator to connect Telegram. It registers its
own pane as the default target and starts the bridge in a background Herdr pane
created with `--no-focus` (you can also run these two commands yourself):

```bash
python3 scripts/telegram_bridge.py register orchestrator --pane <its pane> --default
python3 scripts/telegram_bridge.py run
```

Keep `run` alive; stop it with Ctrl-C. No launch agent is installed, so the Mac
must stay awake and connected. Restarting `run` preserves pairing, registration,
and queued work.

### On your phone

- Send a message to instruct the orchestrator, including requests to spawn
  workstreams using its existing project setup and rules.
- Reply to a notification to continue that thread.
- `/status` lists the orchestrator's delivery state.

Each accepted message gets a 👀 reaction rather than an acknowledgement message.
There is no separate delivery notification; the orchestrator's answer replies to
your original message, and a failed reaction does not block the answer.

### Bridge instructions for the orchestrator

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

### Delivery semantics and limits

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

## Layout

- `agents/orchestrator.md`, `agents/implementer.md` — the two roles.
- `skills/spawn-workstream/` — the spawn skill and its `bootstrap.sh`.
- `.claude-plugin/plugin.json` — the plugin manifest.
- `scripts/telegram_bridge.py` — the optional local Telegram service and helpers.
- `tests/` — bridge authorization, routing, and delivery tests.
