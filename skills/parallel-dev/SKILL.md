---
name: parallel-dev
description: Bootstrap an isolated stream of work in its own git worktree, herdr workspace and (where the project isolates one) store, start a fresh Claude Code implementer agent there, and stop. The agent waits for the user unless the invocation carried full instructions. Use when the user asks to work on something in parallel, to work in a worktree, or invokes the parallel-dev skill.
---

# Parallel development

Several agents work on one project at once without seeing each other's runs. Each
stream gets a git worktree, a herdr workspace, and, where the project keeps
per-worktree state, its own store.

The store is the part that is easy to get wrong. Two checkouts that share a
project's state directory drive each other's runs, and nothing in the output says
so. The project's `.herdr/parallel-dev.sh` names the isolation, and `bootstrap.sh`
applies it, so the problem cannot arise.

## Which agent are you

Two roles matter. Find yours before doing anything.

**Invoked as `/herdr:parallel-dev <task>`?** You are the orchestrator. This skill
is one procedure only: provision the workspace, hand the task to a fresh
implementer, and stop. Configuring the project and tearing streams down later are
separate jobs that live in the orchestrator brief — see "After the stream lands"
at the end.

A session started with `claude --agent herdr:orchestrator` already holds the full
role from that brief; this skill adds the spawn procedure it does not carry
inline.

**Running as `herdr:implementer` in a worktree under `~/.herdr/worktrees/`?** You
are the implementer, and your brief is already your system prompt. You do not need
this skill.

## Prerequisite

The target repo must have `.herdr/parallel-dev.sh`. `bootstrap.sh` fails without
it rather than guessing defaults. If it is missing, the orchestrator configures
the project first — that walkthrough is in the orchestrator brief, not here.

## Topology

herdr binds a worktree to a workspace, so one stream is one workspace. A project
with a live artifact gets a second pane; one without gets the dev pane only:

```
Workspace "<label prefix><feature>"   w7
└── tab "Primary"                     w7:t1
    ├── pane "dev"                     w7:p1   claude code, cwd = worktree
    └── pane "<artifact>"              w7:p2   the project's live artifact
```

## Naming

Pick a slug of two to four words for the feature, lowercase and hyphenated. The
project's prefixes turn it into the branch and workspace label.

# Bootstrap

For the orchestrator. One command.

Turn the user's one-liner into a slug of two to four words, lowercase and
hyphenated. Then decide whether they gave you a topic or a task.

## Topic or task

A topic names an area: `tui colors`, `daemon lifecycle`. A task says what to
build, in enough detail to act on without asking anything else.

Pass the task only for the second kind:

```bash
"${CLAUDE_PLUGIN_ROOT}/skills/parallel-dev/bootstrap.sh" <slug>                      # topic
"${CLAUDE_PLUGIN_ROOT}/skills/parallel-dev/bootstrap.sh" <slug> <the task, verbatim> # task
```

## Naming the dev pane

Always set `HPD_DESC`. It labels the dev pane with what is being built, so the
user scanning their workspaces reads the feature rather than the word "dev", which
tells them nothing they cannot already see.

A short phrase in the user's own terms, roughly three to six words, no trailing
punctuation. Not the branch name reworded: the slug is already the fallback.

```bash
HPD_DESC="a bump effect and a damage flash" \
  "${CLAUDE_PLUGIN_ROOT}/skills/parallel-dev/bootstrap.sh" <slug> <task>
```

## Which profile a stream runs under

Streams run under the project's `HPD_DEFAULT_CONFIG_DIR`, a Claude profile that is
a separate usage allowance from the one this orchestrator session spends, so a
field full of streams does not exhaust the quota the user is typing against.

`HPD_CONFIG_DIR` overrides it for one stream. The script fails rather than
starting if the profile directory is not there, because a stream that silently
falls back to the default profile spends the allowance the switch exists to
protect.

A different profile also means `ListAgents` cannot see the stream and you cannot
address it by name. Discovery is per-profile; the transport is not. Every
session's socket lands in `/tmp/cc-socks/<pid>.sock` regardless, so an explicit
`uds:` address reaches across. The script resolves it and prints it as `address`
in the summary — keep it alongside the workspace id.

The first time a profile meets a new worktree it may stop on its own first-run
prompts, folder trust among them, and the script cannot answer those. It reports
the agent did not start; read the pane before assuming the script failed.

## Choosing a model

Streams run on the project's `HPD_DEFAULT_MODEL`. Set `HPD_MODEL` only when the
user names a different model for this stream, and leave it out of the task text,
since a stream cannot act on an instruction about its own model:

```bash
HPD_MODEL=claude-opus-5 HPD_DESC="..." \
  "${CLAUDE_PLUGIN_ROOT}/skills/parallel-dev/bootstrap.sh" <slug> <task>
```

With no task the agent comes up primed and idle, having read its instructions and
readied its store, and waits for the user to say what to build. With a task it
starts immediately.

When it is unclear, ask the user rather than guessing. Starting a stream on a
guess spends a whole agent building the wrong thing, and the question costs one
message. When it is clear, do not ask. Pass a task through as the user wrote it;
reinterpreting it here is the other way a stream ends up building the wrong thing.

## What to report

The script checks `main` is current, creates the worktree and its workspace, names
the tab and panes, starts the agent and prompts it. It fails loudly rather than
half-provisioning, so read its error instead of retrying by hand.

Print its summary and say whether the agent is working or waiting. That is the
whole report. Two or three lines.

The user has done this many times. Do not explain how the artifact pane fills in
after the first build, that the stream stops at the open PR, or that it reports
back for teardown. They know. Repeating the mechanics every spawn buries the one
thing that varies, which is whether the agent is working or waiting on them.

Say something beyond the summary only when it is true this time and not every
time: a bootstrap warning, a task you had to interpret, a model you pinned, a
question the stream will open with.

Then stop. Do not check on the stream, do not read its files, and do not start a
second one unless the user asks.

# After the stream lands

The stream reports back when its merge lands, and its worktree, workspace and
branch then need tearing down. That is the orchestrator's job, not this skill's.
It lives in the orchestrator brief, `herdr:orchestrator`, together with the
messaging contract the report travels over.

A `claude --agent herdr:orchestrator` session already holds that brief. A plain
session that ran `/herdr:parallel-dev` does not, so read it when a stream reports
done.
