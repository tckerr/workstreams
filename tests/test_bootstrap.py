"""Run bootstrap against local git worktrees and a fake herdr; no live panes."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "skills/spawn-workstream/bootstrap.sh"
FAKE_HERDR = r'''
import json, os, pathlib, subprocess, sys
a = sys.argv[1:]
tree = pathlib.Path(os.environ["TEST_TREE"])
def option(name):
    return a[a.index(name) + 1]
with open(os.environ["TEST_CALLS"], "a") as out:
    out.write(json.dumps(a) + "\n")
result = {}
if a[:2] == ["worktree", "create"]:
    subprocess.run(["git", "worktree", "add", "-q", "-b", option("--branch"),
                    str(tree), option("--base")], check=True)
    result = {"workspace": {"workspace_id": "w18"}, "tab": {"tab_id": "w18:t1"},
              "root_pane": {"pane_id": "w18:p1"}, "worktree": {"path": str(tree)}}
elif a[:2] == ["tab", "create"]:
    result = {"root_pane": {"pane_id": "w18:p2"}}
elif a[:2] == ["agent", "start"]:
    pathlib.Path(os.environ["TEST_AT_START"]).write_text(json.dumps({
        "brief": (tree / "AGENTS.md").read_text() if (tree / "AGENTS.md").exists() else None,
        "kind": option("--kind")}))
elif a[:2] == ["pane", "process-info"]:
    kind = json.loads(pathlib.Path(os.environ["TEST_AT_START"]).read_text())["kind"]
    result = {"process_info": {"foreground_processes": [{"argv0": kind, "pid": 999999999}]}}
elif a[:2] == ["agent", "prompt"]:
    pathlib.Path(os.environ["TEST_PROMPT"]).write_text(a[3])
print(json.dumps({"result": result}))
'''


class BootstrapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.repo = self.directory / "repo"
        self.tree = self.directory / "stream with 'quotes'"
        self.origin = self.directory / "origin.git"
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        # All spawned commands see isolated git configuration and identities.
        self.env = {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                    "HOME": str(self.directory), "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.test",
                    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.test",
                    "HERDR_ENV": "1", "TEST_TREE": str(self.tree),
                    "TEST_CALLS": str(self.directory / "calls.jsonl"),
                    "TEST_AT_START": str(self.directory / "at-start.json"),
                    "TEST_PROMPT": str(self.directory / "prompt.txt")}
        self.run_git("init", "-q", "--bare", str(self.origin))
        self.run_git("init", "-q", "-b", "main", str(self.repo))
        (self.repo / ".herdr").mkdir()
        (self.repo / ".herdr/workstreams.sh").write_text("HERDR_WS_SECOND_PANE_LABEL=''\n")
        self.run_git("-C", str(self.repo), "add", ".")
        self.run_git("-C", str(self.repo), "commit", "-qm", "Initial")
        self.run_git("-C", str(self.repo), "remote", "add", "origin", str(self.origin))
        self.run_git("-C", str(self.repo), "push", "-q", "origin", "main")
        self.executable("herdr", "#!" + sys.executable + "\n" + FAKE_HERDR)
        # These tools are never launched for real. Their presence only enables
        # the existing best-effort tabs, whose calls are captured by fake herdr.
        self.executable("yazi", "#!/bin/sh\nexit 0\n")
        self.executable("lazygit", "#!/bin/sh\nexit 0\n")

    def executable(self, name, content):
        file = self.bin / name
        file.write_text(content)
        file.chmod(0o700)

    def run_git(self, *args):
        return subprocess.run(["git", *args], env=self.env, check=True,
                              capture_output=True, text=True).stdout

    def bootstrap(self, kind, with_socket=True, task="Implement the requested fix."):
        env = {**self.env, "HERDR_WS_KIND": kind}
        if with_socket:
            env["CLAUDE_CODE_MESSAGING_SOCKET"] = "/tmp/test-orchestrator.sock"
            env["CLAUDE_CODE_MESSAGING_TOKEN"] = "test-private-token"
        result = subprocess.run(["bash", str(BOOTSTRAP), "test-stream", task],
                                cwd=self.repo, env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        prompt = (self.directory / "prompt.txt").read_text()
        at_start = json.loads((self.directory / "at-start.json").read_text())
        return result, prompt, at_start

    def test_codex_has_brief_before_start_and_explicit_report_command(self):
        result, prompt, at_start = self.bootstrap("codex")
        self.assertIn("## Reporting to the orchestrator", at_start["brief"])
        self.assertNotIn("with `SendMessage`", at_start["brief"])
        self.assertIn("send started --message", prompt)
        self.assertIn("ready (PR open, not merged)", prompt)
        self.assertIn("merged (merge landed and tree clean)", prompt)
        self.assertNotIn("The orchestrator is uds:", prompt)
        self.assertNotIn("test-private-token", prompt + result.stdout)
        self.assertIn("address    herdr agent prompt test-stream <message>", result.stdout)
        git_dir = Path(self.run_git("-C", str(self.tree), "rev-parse", "--absolute-git-dir").strip())
        config = json.loads((git_dir / "herdr-report/report.json").read_text())
        self.assertEqual(config["socket"], "/tmp/test-orchestrator.sock")
        self.assertEqual(config["token"], "test-private-token")
        self.assertEqual(config["permission_mode"], "bypass")
        self.assertFalse((self.repo / ".git/herdr-report").exists())
        self.assertEqual(self.run_git("-C", str(self.tree), "status", "--porcelain"), "")

    def test_codex_existing_agents_md_uses_adapted_prompt_fallback(self):
        (self.repo / "AGENTS.md").write_text("Project guidance\n")
        self.run_git("-C", str(self.repo), "add", "AGENTS.md")
        self.run_git("-C", str(self.repo), "commit", "-qm", "Project guidance")
        self.run_git("-C", str(self.repo), "push", "-q", "origin", "main")
        _, prompt, at_start = self.bootstrap("codex")
        self.assertEqual(at_start["brief"], "Project guidance\n")
        self.assertEqual((self.tree / "AGENTS.md").read_text(), "Project guidance\n")
        self.assertIn("## Reporting to the orchestrator", prompt)
        self.assertNotIn("with `SendMessage`", prompt)
        self.assertIn("send started --message", prompt)

    def test_codex_missing_socket_and_no_task_have_visible_fallback(self):
        _, prompt, _ = self.bootstrap("codex", with_socket=False, task="")
        self.assertIn("transport is unavailable", prompt)
        self.assertIn("PR URL and summary", prompt)
        self.assertIn("You were given no task", prompt)
        self.assertNotIn("send started --message", prompt)

    def test_claude_retains_original_priming_and_launch(self):
        result, prompt, at_start = self.bootstrap("claude")
        self.assertIsNone(at_start["brief"])
        self.assertIn("The orchestrator is uds:/tmp/test-orchestrator.sock. Report there when you are\n"
                      "done, as your brief describes.", prompt)
        self.assertNotIn("report.py", prompt)
        self.assertIn("address    (not resolved; find it with ListAgents)", result.stdout)
        calls = [json.loads(line) for line in (self.directory / "calls.jsonl").read_text().splitlines()]
        start = next(call for call in calls if call[:2] == ["agent", "start"])
        self.assertEqual(start[start.index("--") + 1:],
                         ["--dangerously-skip-permissions", "--effort", "high", "--agent",
                          "workstreams:implementer", "--model", "claude-opus-4-8"])


if __name__ == "__main__":
    unittest.main()
