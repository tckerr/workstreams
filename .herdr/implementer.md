# Implementer instructions — herdr-orchestrator plugin

This repository is the herdr workstreams/orchestrator plugin itself: a Python 3
(standard library only) and Bash codebase with Markdown briefs and skills. There
is no compiled build step.

## Build

Nothing to build. Do not add a build system or third-party dependencies — the
bridge and its tests are deliberately stdlib-only, Python 3.9+.

## Test

Run the full suite from the repo root and make it pass before you open a PR:

    python3 -m unittest discover -s tests -v

If you touch `scripts/telegram_bridge.py`, extend `tests/test_telegram_bridge.py`
to cover the new behavior. Keep tests hermetic — they must not read or write the
real bridge state or hit the network.

## Runtime state and isolation

The project keeps no per-repo runtime state in the worktree, so there is nothing
to isolate for an ordinary change. The Telegram bridge's state (token, pairing,
queues) lives under `~/.config/workstreams/telegram/`, which is global and shared
with the running orchestrator. Never run the live bridge, and never read or mutate
that shared state, from your worktree. If you need to exercise bridge code, point
it at a throwaway directory inside your worktree with `--state-dir` (or the
`WORKSTREAMS_TELEGRAM_STATE` override) so the paired account and its queue are
untouched.

## House rules

Git author and committer must be `tckerr <tckerr@gmail.com>`. The `origin` remote
uses the `github-tckerr` SSH alias (`git@github-tckerr:tckerr/workstreams.git`) —
keep it; the plain `github.com` host authenticates as a different account and will
fail to push. Never commit `HANDOFF.md`. Match documentation style already in the
repo (flowing prose, sparing Markdown).

## Definition of done

Get the suite green, commit your work on your branch, and push it to origin. Then
open a pull request against `main` with `gh pr create --base main`. Do NOT merge —
the user reviews and merges. If `gh` is authenticated as a different GitHub
account and cannot open the PR against `tckerr/workstreams`, push the branch anyway
and report the branch name so the user can open the PR by hand.

When the PR is open (or the branch is pushed and the PR is blocked on auth), report
back to the orchestrator with the branch name and the PR URL. Leave your worktree
in place; the user has not merged yet, so it is not ready for teardown.
