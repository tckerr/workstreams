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
elif a[:3] == ["pane", "run", "w18:p1"]:
    subprocess.Popen(["bash", "-c", a[3]], cwd=tree, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
elif a[:2] == ["agent", "start"]:
    pathlib.Path(os.environ["TEST_AT_START"]).write_text(json.dumps({
        "brief": (tree / "AGENTS.md").read_text() if (tree / "AGENTS.md").exists() else None,
        "prepared": (tree / "prepared").exists(),
        "kind": option("--kind")}))
elif a[:2] == ["pane", "process-info"]:
    kind = json.loads(pathlib.Path(os.environ["TEST_AT_START"]).read_text())["kind"]
    result = {"process_info": {"foreground_processes": [{"argv0": kind, "pid": 999999999}]}}
elif a[:2] == ["agent", "prompt"]:
    stall = os.environ.get("TEST_STALL")
    stalled = pathlib.Path(os.environ["TEST_PROMPT"] + ".stalled")
    if stall and not stalled.exists():
        stalled.touch()
        if stall == "typed":
            pathlib.Path(os.environ["TEST_PROMPT"]).write_text(a[3])
        sys.exit(1)
    pathlib.Path(os.environ["TEST_PROMPT"]).write_text(a[3])
elif a[:2] == ["agent", "read"]:
    if os.environ.get("TEST_STALL") == "typed":
        print("> Your worktree is " + str(tree))
    sys.exit(0)
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

    def run_bootstrap(self, kind, with_socket=True, task="Implement the requested fix.",
                      stall=None, extra_env=None):
        env = {**self.env, "HERDR_WS_KIND": kind, **(extra_env or {})}
        if stall:
            env["TEST_STALL"] = stall
        if with_socket:
            env["CLAUDE_CODE_MESSAGING_SOCKET"] = "/tmp/test-orchestrator.sock"
            env["CLAUDE_CODE_MESSAGING_TOKEN"] = "test-private-token"
        return subprocess.run(["bash", str(BOOTSTRAP), "test-stream", task],
                              cwd=self.repo, env=env, capture_output=True, text=True, timeout=20)

    def bootstrap(self, kind, **options):
        result = self.run_bootstrap(kind, **options)
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
                          "workstreams:implementer"])

    def test_files_tab_yazi_uses_the_client_id_browse_derives(self):
        # The Files-tab yazi must launch with the same --client-id browse.sh
        # derives for the workspace, or the browse skill's `show`/`dir` cannot
        # reach it. bootstrap inlines the formula; assert it stays in step with
        # browse.sh's own ws_client_id for the workspace fake herdr returns (w18).
        _, _, _ = self.bootstrap("claude")
        calls = [json.loads(line) for line in (self.directory / "calls.jsonl").read_text().splitlines()]
        launch = next(c[3] for c in calls if c[:2] == ["pane", "run"] and "yazi" in c[3])
        browse = ROOT / "scripts/browse.sh"
        expected = subprocess.run(["bash", "-c", f"source '{browse}'; ws_client_id w18"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        self.assertIn(f"yazi --client-id {expected} ", launch)
        self.assertIn(str(self.tree), launch)

    def calls(self):
        return [json.loads(line) for line in (self.directory / "calls.jsonl").read_text().splitlines()]

    def test_opening_prompt_waits_for_the_turn_to_begin(self):
        self.bootstrap("claude")
        prompt = next(call for call in self.calls() if call[:2] == ["agent", "prompt"])
        self.assertIn("--wait", prompt)
        self.assertIn("working", prompt)

    def test_prompt_left_in_the_input_is_submitted_with_enter(self):
        _, prompt, _ = self.bootstrap("claude", stall="typed")
        calls = self.calls()
        self.assertIn(["agent", "send-keys", "test-stream", "Enter"], calls)
        self.assertEqual(sum(call[:2] == ["agent", "prompt"] for call in calls), 1)
        self.assertIn("Your task: Implement the requested fix.", prompt)

    def test_lost_prompt_is_sent_again(self):
        _, prompt, _ = self.bootstrap("claude", stall="lost")
        calls = self.calls()
        self.assertFalse(any(call[:2] == ["agent", "send-keys"] for call in calls))
        self.assertEqual(sum(call[:2] == ["agent", "prompt"] for call in calls), 2)
        self.assertIn("Your task: Implement the requested fix.", prompt)

    def test_no_prepare_step_by_default(self):
        result, _, at_start = self.bootstrap("claude")
        self.assertFalse(at_start["prepared"])
        self.assertIn("prepare    (none)", result.stdout)

    def test_agent_starts_only_after_the_prepare_step_finishes(self):
        result, _, at_start = self.bootstrap(
            "claude", extra_env={"HERDR_WS_PREPARE": "sleep 3; touch prepared"})
        self.assertTrue(at_start["prepared"])
        self.assertRegex(result.stdout, r"prepare    done in \d+s")

    def test_failed_prepare_step_warns_and_still_starts_the_agent(self):
        result, prompt, _ = self.bootstrap("claude", extra_env={"HERDR_WS_PREPARE": "exit 3"})
        self.assertIn("prepare    FAILED (exit 3); read w18:p1", result.stdout)
        self.assertIn("Your task: Implement the requested fix.", prompt)

    def test_prepare_step_that_overruns_its_timeout_fails_the_spawn(self):
        result = self.run_bootstrap("claude", extra_env={
            "HERDR_WS_PREPARE": "sleep 30", "HERDR_WS_PREPARE_TIMEOUT": "2"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not finish within 2s", result.stderr)
        self.assertFalse(any(call[:2] == ["agent", "start"] for call in self.calls()))


if __name__ == "__main__":
    unittest.main()
