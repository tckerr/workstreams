# herdr-orchestration

Run several Claude sessions on one project at once, each in its own git worktree,
without them tripping over each other. One session — the **orchestrator** — starts
the streams and cleans them up. Each stream is an **implementer** working in
isolation and reporting back when its branch lands.

The plugin holds no knowledge of any particular project. What to build with, how
to test, whether there is a live artifact to keep running, and what "done" means
are all read from the project itself. So the same orchestrator drives a Rust game
and a web app without changing.

## Install

```bash
claude plugin marketplace add tckerr/herdr-orchestration
claude plugin install herdr@herdr
```

Then run a session as the orchestrator:

```bash
claude --agent herdr:orchestrator
```

Spawning also works from any session with `/herdr:parallel-dev <task>`, but a
dedicated orchestrator session is what receives the done-reports and tears streams
down.

## What each project supplies

A project becomes parallel-dev-ready when it has a `.herdr/parallel-dev.sh`. On
the first spawn in a repo, the orchestrator notices its absence and walks you
through writing it (including whether to commit it or keep it local), so you
rarely write it by hand. The pieces:

The shell file `.herdr/parallel-dev.sh` holds the mechanical values
`bootstrap.sh` sources. `bootstrap.sh` is the authoritative list; the keys are:

| Key | What it sets |
| --- | --- |
| `HPD_BRANCH_PREFIX` / `HPD_LABEL_PREFIX` | how branches and workspaces are named |
| `HPD_STORE_SUBDIR` / `HPD_STORE_ENV` | the per-worktree store, if the project needs one to isolate state |
| `HPD_SECOND_PANE_LABEL` | the live-artifact pane, if there is one |
| `HPD_SURVIVOR_GLOB` | the process pattern the teardown check looks for |
| `HPD_DEFAULT_CONFIG_DIR` / `HPD_DEFAULT_MODEL` | the Claude profile and model streams run under |

The rest is prose the implementer reads from `CLAUDE.md`, since a shell file
cannot carry it: how to build, how to test, how to keep the artifact up, the
house rules, and the project's definition of done. The last one is the one that
matters.

## The definition of done belongs to the project

The plugin never decides how a stream ships. It runs the worktree, keeps the
artifact alive, commits as it goes, and reports back — but the sequence that turns
a finished change into a merged commit is the project's to define in its
`CLAUDE.md`: how it tests, how it opens and merges a PR, whether it waits for you
first, and what it does to the worktree afterward. One project squashes and resets
to `origin/main`; another does not. The implementer follows whatever the project
says, then sends the orchestrator the one message the project does not own — that
the merge landed and teardown is safe.

## Layout

- `agents/orchestrator.md`, `agents/implementer.md` — the two roles.
- `skills/parallel-dev/` — the spawn skill and its `bootstrap.sh`.
- `.claude-plugin/` — the plugin and marketplace manifests.
