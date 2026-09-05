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
| `HERDR_WS_DEFAULT_CONFIG_DIR` | The profile directory streams run under (`CLAUDE_CONFIG_DIR` for Claude, `CODEX_HOME` for Codex), so they can spend a separate usage allowance. Leave it empty to run them under the orchestrator's own profile. |
| `HERDR_WS_DEFAULT_KIND` | Which agent implementer streams start as: `claude` (the default) or `codex`. |
| `HERDR_WS_DEFAULT_MODEL` | The model streams run on. It belongs to the default kind; overriding only the kind for one spawn falls back to that kind's own default model. |
| `HERDR_WS_PANE_INIT` | Command run in the pane before the agent starts, to select a toolchain the project pins. Empty leaves the pane as the machine leaves it. |
| `HERDR_WS_PANE_INIT_CHECK` | String that must appear in the started agent's environment for the init to count as landed. Empty skips the check. |

Four keys override the defaults for a single spawn, on the command line:
`HERDR_WS_DESC` (labels the dev pane), `HERDR_WS_KIND`, `HERDR_WS_MODEL`, and
`HERDR_WS_CONFIG_DIR`.

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

Optionally drive orchestration from your phone: the orchestrator connects a paired
private Telegram chat on request, so you can send requests and answer its questions
away from the keyboard. Telegram is a capability of the orchestrator only — streams
are never wired to the phone. See [TELEGRAM.md](TELEGRAM.md) for setup, connecting
the orchestrator, the phone commands, and delivery semantics.

## Layout

- `agents/orchestrator.md`, `agents/implementer.md` — the two roles.
- `skills/spawn-workstream/` — the spawn skill, its `bootstrap.sh`, and `resolve-agent.sh` (the testable agent-kind/model decision and Codex brief delivery).
- `.claude-plugin/plugin.json` — the plugin manifest.
- `scripts/telegram_bridge.py` — the optional local Telegram service and helpers.
- `TELEGRAM.md` — the Telegram bridge guide.
- `tests/` — bridge authorization, routing, and delivery tests, plus the agent-kind resolver tests.
