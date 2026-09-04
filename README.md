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

## Telegram POC

**Experimental: under development on
[`telegram-bridge-poc`](https://github.com/tckerr/workstreams/tree/telegram-bridge-poc).
Not part of the stable release.**

The live phone → Codex → phone path has been exercised, and automated tests cover
account restrictions, routing, receipt reactions, queuing, and interrupted
delivery. Before merging this feature, we still need to validate the full
workstream spawn/report/teardown flow, improve recovery from network failures and
usage limits, and provide an unattended service lifecycle. For now, failed or
ambiguous deliveries can require manual intervention.

The optional bridge connects one private Telegram chat to an orchestrator and
its registered streams. It uses Python 3.9+ with no third-party packages. Run it
inside Herdr on the same Mac as the agents. It uses Telegram long polling, so
there is no public server or inbound port to configure.

The bridge uses Herdr's generic agent commands, so it can target Claude or Codex
sessions. Register a Codex pane with `register codex-test --pane <pane-id>` and
send `/to codex-test MESSAGE` on Telegram. This does not port the workstream
launcher: `bootstrap.sh` still starts Claude implementers.

From this plugin's directory:

Each user creates their own bot with [BotFather](https://t.me/BotFather), supplies
their own token during `setup`, and pairs their own Telegram account. There is
no shared project bot, token, or hardcoded account allowlist. Credentials,
pairing, agent registrations, and message history belong to the local instance,
not the plugin or the project repository.

```bash
python3 scripts/telegram_bridge.py setup
# Enter the bot token at the hidden prompt. Send the printed /pair command
# to your bot in a private Telegram chat.
python3 scripts/telegram_bridge.py register orchestrator --pane <pane-id> --default
python3 scripts/telegram_bridge.py run
```

Keep `run` alive in a background pane created with `--no-focus`. Stop it with
Ctrl-C. This POC does not install a launch agent; the Mac must stay awake and
connected. Restarting `run` preserves pairing, registrations, and queued work.

The one-time pairing code binds a numeric Telegram user ID and private chat ID.
Every incoming command checks both. Other accounts and groups are ignored, and
the code cannot be reused after pairing. The token and state live under
`~/.config/workstreams/telegram/`, outside the repo, with owner-only permissions.
Use `--state-dir PATH` before the subcommand to select another state directory;
`WORKSTREAMS_TELEGRAM_STATE` provides the same override for bootstrap.

For another bot or an independent configuration, use a separate state directory
consistently for setup, registration, the running bridge, and bootstrap:

```bash
export WORKSTREAMS_TELEGRAM_STATE="$HOME/.config/workstreams/another-telegram"
python3 scripts/telegram_bridge.py setup
python3 scripts/telegram_bridge.py register orchestrator --pane <pane-id> --default
python3 scripts/telegram_bridge.py run
```

Use one running bridge per bot token. This POC pairs one account per instance;
shared team access and additional chat transports are future extensions, not
assumptions built into the workstream roles.

On your phone:

- Send a new message to instruct the default orchestrator, including requests to
  spawn workstreams using its existing project setup and rules.
- Reply to an agent notification to address that agent directly.
- `/to NAME MESSAGE` addresses another registered agent.
- `/use NAME` chooses which agent receives new messages; replies still follow
  their original agent route.
- `/status` lists registered agents and delivery states.

Each accepted message gets a 👀 reaction instead of an acknowledgement message.
There is no delivery notification; the agent's answer replies to your original
message. A failed reaction does not prevent the answer from being sent.

When Telegram mode is enabled, newly bootstrapped streams register automatically.
Already-running agents can be registered manually using the same `register`
command without `--default`. Their installed briefs do not update automatically;
the bridge sends a setup brief once when each registered session is idle. That
brief explains how to reply, notify, and register new streams, so older role
briefs work too. Later prompts contain only `Telegram request <id>:` and your
message text, with no message files. Setup delivery is remembered per agent
process and is not repeated when the bridge restarts.

```bash
# Send a question or alert; replies route back to this registered agent.
python3 scripts/telegram_bridge.py notify --target orchestrator --text 'Which project should I use?'
# The helper is explained once during setup; each message supplies a request ID.
python3 scripts/telegram_bridge.py reply <request-id> --text 'Started the stream.'
# Use --file PATH instead of --text for multiline responses.
python3 scripts/telegram_bridge.py status
python3 scripts/telegram_bridge.py unregister <agent-name>
```

Delivery waits for an idle/done agent and checks its terminal and foreground
process identity. Replaced or missing agents never receive queued commands.
Unregister and register again after restarting an agent. Unknown reply routes
are rejected rather than sent to the default orchestrator.

The bridge alerts once when a registered agent enters `blocked`. These dialogs
must be handled in Herdr; the POC does not forward approval keystrokes. Questions
sent through `notify` can be answered from your phone normally. It does not try
to infer stalls from silence or scrape final answers from terminal output.
Herdr may report a usage-limited session as `done`; this POC does not detect that
screen separately. “Delivered” means the prompt was submitted, not that the
agent has answered or completed the work.

Incoming updates are deduplicated and stored before forwarding. A crash or
timeout during delivery is marked `uncertain` and is not automatically retried,
because the agent may already have acted. Check Herdr before resending. Outbound
Telegram messages with ambiguous delivery are also marked `uncertain`; inspect
local `status` for those counts. This POC favors avoiding duplicate actions over
automatic retries and does not promise exactly-once delivery.

```bash
python3 -m unittest discover -s tests -v
```

## Layout

- `agents/orchestrator.md`, `agents/implementer.md` — the two roles.
- `skills/spawn-workstream/` — the spawn skill and its `bootstrap.sh`.
- `.claude-plugin/plugin.json` — the plugin manifest.
- `scripts/telegram_bridge.py` — the optional local Telegram service and helpers.
- `tests/` — bridge authorization, routing, and delivery tests.
