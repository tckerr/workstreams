"""Tests for skills/spawn-workstream/resolve-agent.sh.

The resolver decides which agent a stream starts as — kind, model, and the argv
handed to `herdr agent start`. It reads only HERDR_WS_* from the environment and
sets WS_* globals, so it can be exercised hermetically: no herdr, git, or network.
Each case sources the helper in a fresh bash, calls resolve_agent, and prints the
resulting globals for the test to assert against.
"""

import os
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESOLVER = os.path.join(REPO_ROOT, "skills", "spawn-workstream", "resolve-agent.sh")

# Prints the resolved globals on stdout, one per line, plus the argv on its own
# line prefixed so it survives whitespace. Exits non-zero if resolve_agent does.
HARNESS = r"""
set -euo pipefail
source "$RESOLVER"
resolve_agent
printf 'kind=%s\n' "$WS_KIND"
printf 'model=%s\n' "$WS_MODEL"
printf 'argv0=%s\n' "$WS_AGENT_ARGV0"
printf 'profile_env=%s\n' "$WS_PROFILE_ENV"
printf 'brief=%s\n' "$WS_BRIEF"
printf 'args=%s\n' "${WS_AGENT_ARGS[*]}"
"""


def resolve(**env):
    """Run the resolver with the given HERDR_WS_* env and return parsed output."""
    child_env = {"RESOLVER": RESOLVER, "PATH": os.environ.get("PATH", "")}
    child_env.update({k: v for k, v in env.items() if v is not None})
    proc = subprocess.run(
        ["bash", "-c", HARNESS],
        env=child_env,
        capture_output=True,
        text=True,
    )
    return proc


def parse(proc):
    out = {}
    for line in proc.stdout.splitlines():
        key, _, value = line.partition("=")
        out[key] = value
    return out


class ResolveAgentTest(unittest.TestCase):
    def test_default_is_claude_unchanged(self):
        # No kind set, a Claude repo default model: the argv must match what the
        # script produced before agent-kind selection existed.
        proc = resolve(HERDR_WS_DEFAULT_MODEL="claude-opus-4-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        r = parse(proc)
        self.assertEqual(r["kind"], "claude")
        self.assertEqual(r["model"], "claude-opus-4-8")
        self.assertEqual(r["argv0"], "claude")
        self.assertEqual(r["profile_env"], "CLAUDE_CONFIG_DIR")
        self.assertEqual(r["brief"], "agent-flag")
        self.assertEqual(
            r["args"],
            "--dangerously-skip-permissions --effort high "
            "--agent workstreams:implementer --model claude-opus-4-8",
        )

    def test_codex_override_drops_claude_only_flags(self):
        # Overriding only the kind must not force the Claude default model onto
        # Codex, and must not carry the --agent / --effort Claude flags.
        proc = resolve(HERDR_WS_KIND="codex", HERDR_WS_DEFAULT_MODEL="claude-opus-4-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        r = parse(proc)
        self.assertEqual(r["kind"], "codex")
        self.assertEqual(r["model"], "")
        self.assertEqual(r["argv0"], "codex")
        self.assertEqual(r["profile_env"], "CODEX_HOME")
        self.assertEqual(r["brief"], "agents-md")
        self.assertEqual(
            r["args"],
            "--dangerously-bypass-approvals-and-sandbox "
            "-c model_reasoning_effort=high -c project_doc_max_bytes=1048576",
        )
        self.assertNotIn("--agent", r["args"])
        self.assertNotIn("--effort", r["args"])

    def test_codex_with_explicit_model(self):
        proc = resolve(HERDR_WS_KIND="codex", HERDR_WS_MODEL="gpt-5-codex")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        r = parse(proc)
        self.assertEqual(r["kind"], "codex")
        self.assertEqual(r["model"], "gpt-5-codex")
        self.assertTrue(r["args"].endswith("--model gpt-5-codex"))

    def test_repo_default_kind_codex(self):
        # A repo may set Codex as its default kind with a matching default model.
        proc = resolve(HERDR_WS_DEFAULT_KIND="codex", HERDR_WS_DEFAULT_MODEL="gpt-5")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        r = parse(proc)
        self.assertEqual(r["kind"], "codex")
        self.assertEqual(r["model"], "gpt-5")

    def test_explicit_model_wins_for_claude(self):
        proc = resolve(HERDR_WS_MODEL="claude-sonnet", HERDR_WS_DEFAULT_MODEL="claude-opus-4-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        r = parse(proc)
        self.assertEqual(r["kind"], "claude")
        self.assertEqual(r["model"], "claude-sonnet")

    def test_unsupported_kind_is_rejected(self):
        proc = resolve(HERDR_WS_KIND="gemini")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unsupported agent kind", proc.stderr)


# Prints the brief-delivery outcome after running deliver_codex_brief against a
# real (throwaway) git repo, so the AGENTS.md write, git-exclude, and prompt
# fallback are exercised as bootstrap.sh runs them.
DELIVER_HARNESS = r"""
set -euo pipefail
source "$RESOLVER"
deliver_codex_brief "$BRIEF" "$TREE"
printf 'via=%s\n' "$WS_BRIEF_VIA"
printf 'prefix_len=%s\n' "${#WS_BRIEF_PREFIX}"
"""


class DeliverCodexBriefTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tree = self.tmp.name
        subprocess.run(["git", "init", "-q", self.tree], check=True)
        self.brief = os.path.join(self.tree, "brief.md")
        with open(self.brief, "w") as f:
            f.write("---\nname: implementer\n---\n\nDo the work here.\n")

    def tearDown(self):
        self.tmp.cleanup()

    def deliver(self):
        env = {
            "RESOLVER": RESOLVER,
            "PATH": os.environ.get("PATH", ""),
            "BRIEF": self.brief,
            "TREE": self.tree,
        }
        return subprocess.run(
            ["bash", "-c", DELIVER_HARNESS], env=env, capture_output=True, text=True
        )

    def test_writes_agents_md_stripped_and_git_ignored(self):
        proc = self.deliver()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = parse(proc)
        self.assertEqual(out["via"], "AGENTS.md")
        self.assertEqual(out["prefix_len"], "0")

        agents_md = os.path.join(self.tree, "AGENTS.md")
        self.assertTrue(os.path.exists(agents_md))
        body = open(agents_md).read()
        self.assertIn("Do the work here.", body)
        self.assertNotIn("name: implementer", body)  # frontmatter stripped

        # Git must not see the generated brief, so no stream commits it.
        status = subprocess.run(
            ["git", "-C", self.tree, "status", "--short"],
            capture_output=True,
            text=True,
        )
        self.assertNotIn("AGENTS.md", status.stdout)
        ignored = subprocess.run(["git", "-C", self.tree, "check-ignore", "AGENTS.md"])
        self.assertEqual(ignored.returncode, 0)

    def test_existing_agents_md_falls_back_to_prompt(self):
        # A project that ships its own AGENTS.md must be left untouched.
        agents_md = os.path.join(self.tree, "AGENTS.md")
        with open(agents_md, "w") as f:
            f.write("PROJECT OWN GUIDANCE\n")

        proc = self.deliver()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = parse(proc)
        self.assertIn("opening prompt", out["via"])
        self.assertNotEqual(out["prefix_len"], "0")
        # The project's own file is unchanged.
        self.assertEqual(open(agents_md).read(), "PROJECT OWN GUIDANCE\n")


if __name__ == "__main__":
    unittest.main()
