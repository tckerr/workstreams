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

## Branch and workspace names

A stream's slug becomes the branch name, and its spaced form the workspace label.
There are no configurable prefixes. A repo that wants a naming convention
expresses it in its own tooling, not here.

## Layout

- `agents/orchestrator.md`, `agents/implementer.md` — the two roles.
- `skills/spawn-workstream/` — the spawn skill and its `bootstrap.sh`.
- `.claude-plugin/plugin.json` — the plugin manifest.
