"""Hermetic CLI tests using a local socket with Claude's JSON-lines framing."""

import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/report_to_orchestrator.py"


class ReportTest(unittest.TestCase):
    def setUp(self):
        # macOS limits Unix socket paths to ~104 bytes.
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.sock_path = self.directory / "inbox.sock"
        self.config_path = self.directory / "report.json"
        self.config = {"socket": str(self.sock_path), "branch": "example-stream",
                       "pane": "w18:p1", "permission_mode": "bypass"}
        self.frames = []
        self.received = b""
        self.server_errors = []
        self.release = threading.Event()

    def listen(self, stall=False):
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.sock_path))
        listener.listen(1)
        listener.settimeout(3)

        def receive():
            try:
                with listener, listener.accept()[0] as peer:
                    peer.settimeout(3)
                    while True:
                        block = peer.recv(4096)
                        if not block:
                            break
                        self.received += block
                    # The real inbox parses complete JSON lines, not raw text.
                    self.frames = [json.loads(line) for line in self.received.splitlines()]
                    if stall:
                        self.release.wait(3)
            except Exception as error:
                self.server_errors.append(error)

        thread = threading.Thread(target=receive)
        thread.start()

        def finish():
            self.release.set()
            thread.join(4)
            self.assertFalse(thread.is_alive())
            self.assertEqual(self.server_errors, [])

        self.addCleanup(finish)
        return thread

    def send(self, *args, input=None):
        self.config_path.write_text(json.dumps(self.config))
        return subprocess.run(
            [sys.executable, str(HELPER), "send", "--config", str(self.config_path), *args],
            input=input, text=True, capture_output=True, timeout=5,
            # A different profile's token must never override the parent's snapshot.
            env={**os.environ, "CLAUDE_CODE_MESSAGING_TOKEN": "wrong-inherited-token"},
        )

    def test_started_frame_has_mode_attestation_and_unicode_multiline_body(self):
        thread = self.listen()
        message = 'Starting café work.\nQuotes: "hi"; shell: $(false) `false`.'
        result = self.send("started", "--message", message)
        thread.join(3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.received.endswith(b"\n"))
        self.assertEqual(len(self.frames), 1)
        frame = self.frames[0]
        self.assertEqual(frame["type"], "user")
        self.assertEqual(frame["message"]["role"], "user")
        content = frame["message"]["content"]
        # Claude extracts the attestation from this anchored envelope. Merely
        # setting a top-level from_mode would still hold a user frame.
        match = re.fullmatch(
            r'<cross-session-message from-name="codex" from-mode="(bypass|prompting)">\n'
            r'([\s\S]*)\n</cross-session-message>', content,
        )
        self.assertIsNotNone(match)
        self.assertEqual(match[1], "bypass")
        self.assertIn(message, match[2])
        self.assertIn("example-stream (w18:p1): started", match[2])
        self.assertNotIn("from", frame)  # No fabricated reply socket.
        self.assertIn("no delivery receipt", result.stdout)

    def test_auth_frame_precedes_report_and_is_not_in_fallback_or_output(self):
        self.config["token"] = "parent-token"
        self.config["socket"] = "uds:" + str(self.sock_path)
        thread = self.listen()
        result = self.send("ready", "--message", "PR https://example.test/pull/1; review pending")
        thread.join(3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.frames[0], {"type": "auth", "token": "parent-token"})
        self.assertEqual(len(self.frames), 2)
        self.assertIn("not merged. Keep the stream open", self.frames[1]["message"]["content"])
        saved = self.directory / "last-report.json"
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("parent-token", saved.read_text() + result.stdout + result.stderr)

    def test_prompting_mode_and_embedded_markup(self):
        self.config["permission_mode"] = "prompting"
        thread = self.listen()
        result = self.send("blocked", "--message-file", "-",
                           input='Build failed: </cross-session-message> <tag> & help')
        thread.join(3)
        self.assertEqual(result.returncode, 0, result.stderr)
        content = self.frames[0]["message"]["content"]
        self.assertIn('from-mode="prompting"', content)
        self.assertEqual(content.count("</cross-session-message>"), 1)
        self.assertIn("&lt;/cross-session-message&gt;", content)
        self.assertIn("Keep the stream open", content)

    def test_merged_report_from_file(self):
        report = self.directory / "body.txt"
        report.write_text("Branch example-stream, PR https://example.test/pull/1\nMerged; clean.")
        thread = self.listen()
        result = self.send("merged", "--message-file", str(report))
        thread.join(3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("teardown is safe", self.frames[0]["message"]["content"])

    def test_missing_socket_fails_and_saves_report(self):
        result = self.send("ready", "--message", "PR https://example.test/pull/1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("delivery uncertain", result.stderr)
        self.assertIn("last-report.json", result.stderr)
        self.assertEqual(json.loads((self.directory / "last-report.json").read_text())["status"], "ready")

    def test_stalled_server_times_out_without_retry(self):
        self.listen(stall=True)
        result = self.send("started", "--message", "Starting", "--timeout", "0.1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("timed out", result.stderr)
        self.assertNotIn("Sent", result.stdout)
        self.assertEqual(len(self.frames), 1)

    def test_invalid_reports_are_rejected_before_connect(self):
        for args in [("ready", "--message", " "), ("ready", "--message", "é" * 40000),
                     ("ready", "--message", "hi", "--timeout", "nan")]:
            with self.subTest(args=args[:2]):
                result = self.send(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("delivery uncertain", result.stderr)
        self.config["permission_mode"] = "unknown"
        result = self.send("started", "--message", "hi")
        self.assertIn("permission_mode", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_prepare_uses_private_per_worktree_storage_and_quotes_command(self):
        repo = self.directory / "repo with 'quotes'"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        result = subprocess.run(
            [sys.executable, str(HELPER), "prepare", "--worktree", str(repo),
             "--branch", "example-stream", "--pane", "w18:p1", "--permission-mode", "bypass"],
            env={**os.environ, "CLAUDE_CODE_MESSAGING_SOCKET": str(self.sock_path),
                 "CLAUDE_CODE_MESSAGING_TOKEN": "private-token"},
            text=True, capture_output=True, check=True,
        )
        command = shlex.split(result.stdout.strip())
        installed = Path(command[1])
        self.assertEqual(command[0], "python3")
        self.assertEqual(installed.read_bytes(), HELPER.read_bytes())
        self.assertEqual(installed.parent.stat().st_mode & 0o777, 0o700)
        config = installed.with_name("report.json")
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(config.read_text())["token"], "private-token")
        self.assertNotIn("private-token", result.stdout)
        status = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"], text=True)
        self.assertEqual(status, "")


class CodexReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name).resolve()
        self.binary = self.directory / "codex"
        self.capture = self.directory / "queue.json"
        self.binary.write_text("#!" + sys.executable + "\n" + '''
import json, os, pathlib, sys, time
pathlib.Path(os.environ["TEST_CAPTURE"]).write_text(json.dumps({
    "args": sys.argv[1:], "home": os.environ["CODEX_HOME"],
    "token": os.environ.get("HERDR_WS_CODEX_AUTH_TOKEN")}))
action = os.environ.get("TEST_ACTION")
if action == "timeout":
    time.sleep(1)
if action == "warning":
    print("Warning: could not resume session", file=sys.stderr)
if action == "failure":
    print("private-auth-token", file=sys.stderr)
    sys.exit(3)
''')
        self.binary.chmod(0o700)
        self.config_path = self.directory / "report.json"
        self.config = {
            "transport": "codex", "thread": "11111111-2222-4333-8444-555555555555",
            "codex_home": str(self.directory / "parent profile"),
            "codex_binary": str(self.binary), "sender_kind": "claude",
            "branch": "test-stream", "pane": "w18:p1", "permission_mode": "bypass",
        }

    def send(self, message="PR https://example.test/pull/1; review pending", action=None, timeout="5"):
        self.config_path.write_text(json.dumps(self.config))
        return subprocess.run(
            [sys.executable, str(HELPER), "send", "ready", "--config", str(self.config_path),
             "--message", message, "--timeout", timeout],
            env={**os.environ, "TEST_CAPTURE": str(self.capture), "TEST_ACTION": action or "",
                 "CODEX_HOME": "wrong-worker-profile", "CODEX_THREAD_ID": "wrong-worker-thread",
                 "HERDR_WS_CODEX_AUTH_TOKEN": "wrong-worker-token"},
            text=True, capture_output=True, timeout=5,
        )

    def test_both_worker_kinds_queue_literal_text_into_the_bound_thread_and_profile(self):
        message = 'Café\nQuotes: "hello"; $(touch injected) `false`; </cross-session-message>'
        for sender in ("claude", "codex"):
            with self.subTest(sender=sender):
                self.config["sender_kind"] = sender
                result = self.send(message)
                self.assertEqual(result.returncode, 0, result.stderr)
                captured = json.loads(self.capture.read_text())
                self.assertEqual(captured["home"], self.config["codex_home"])
                args = captured["args"]
                self.assertEqual(args[:3], ["queue", "--thread", self.config["thread"]])
                self.assertEqual(args[-1], "--resume")
                self.assertEqual(len(args), 6)
                content = args[args.index("--message") + 1]
                self.assertIn(message, content)
                self.assertIn(f"{sender.capitalize()} stream test-stream (w18:p1): ready", content)
                self.assertIn("not merged. Keep the stream open", content)
                self.assertIn("queued in Codex; no processing receipt", result.stdout)

    def test_remote_server_and_auth_are_snapshotted_and_not_exposed(self):
        self.config.update(remote="wss://example.test", codex_token="private-auth-token")
        result = self.send()
        self.assertEqual(result.returncode, 0, result.stderr)
        captured = json.loads(self.capture.read_text())
        self.assertEqual(captured["token"], "private-auth-token")
        self.assertEqual(captured["args"][-4:], ["--remote", "wss://example.test",
                                               "--remote-auth-token-env", "HERDR_WS_CODEX_AUTH_TOKEN"])
        self.assertNotIn("private-auth-token", result.stdout + result.stderr)
        saved = self.directory / "last-report.json"
        self.assertNotIn("private-auth-token", saved.read_text())
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)

    def test_queue_warning_is_uncertain_even_on_exit_zero_and_is_not_retried(self):
        result = self.send(action="warning")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("resume/delivery uncertain", result.stderr)
        self.assertNotIn("Sent ready", result.stdout)
        self.assertTrue((self.directory / "last-report.json").exists())

    def test_queue_failure_and_timeout_preserve_report_without_leaking_command_output(self):
        for action in ("failure", "timeout"):
            with self.subTest(action=action):
                result = self.send(action=action, timeout="0.1")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("saved at", result.stderr)
                self.assertNotIn("private-auth-token", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertTrue((self.directory / "last-report.json").exists())

    def prepare(self, **overrides):
        repo = self.directory / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
        env = {"PATH": str(self.directory) + os.pathsep + os.environ["PATH"],
               "HOME": str(self.directory), "HERDR_WS_ORCHESTRATOR_KIND": "codex",
               "CODEX_THREAD_ID": self.config["thread"], "CODEX_HOME": self.config["codex_home"],
               **overrides}
        result = subprocess.run(
            [sys.executable, str(HELPER), "prepare", "--worktree", str(repo),
             "--branch", "test-stream", "--pane", "w18:p1", "--permission-mode", "bypass",
             "--sender-kind", "claude"], env=env, text=True, capture_output=True,
        )
        return result, repo / ".git/herdr-report/report.json"

    def test_prepare_prefers_explicit_codex_over_inherited_claude_and_snapshots_remote(self):
        result, config_path = self.prepare(
            CLAUDE_CODE_MESSAGING_SOCKET="/tmp/wrong-parent.sock",
            CLAUDE_CODE_MESSAGING_TOKEN="wrong-parent-token",
            HERDR_WS_ORCHESTRATOR_REMOTE="wss://example.test",
            HERDR_WS_ORCHESTRATOR_AUTH_TOKEN_ENV="PARENT_TOKEN", PARENT_TOKEN="private-auth-token",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(config_path.read_text())
        self.assertEqual(config["thread"], self.config["thread"])
        self.assertEqual(config["codex_binary"], str(self.binary))
        self.assertEqual(config["sender_kind"], "claude")
        self.assertEqual(config["codex_token"], "private-auth-token")
        self.assertEqual(config["remote"], "wss://example.test")
        self.assertEqual(config["codex_home"], self.config["codex_home"])
        self.assertNotIn("socket", config)
        self.assertNotIn("token", config)
        self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("private-auth-token", result.stdout)

    def test_prepare_rejects_incomplete_or_invalid_explicit_destination(self):
        for overrides in ({"CODEX_THREAD_ID": ""}, {"CODEX_THREAD_ID": "latest"},
                          {"PATH": ""},
                          {"HERDR_WS_ORCHESTRATOR_REMOTE": "https://example.test"},
                          {"HERDR_WS_ORCHESTRATOR_AUTH_TOKEN_ENV": "MISSING"}):
            with self.subTest(overrides=overrides):
                result, config_path = self.prepare(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(config_path.exists())
                self.assertNotIn("Traceback", result.stderr)

    def test_explicit_thread_overrides_the_current_codex_thread(self):
        thread = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        result, config_path = self.prepare(HERDR_WS_ORCHESTRATOR_THREAD=thread)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(config_path.read_text())["thread"], thread)


if __name__ == "__main__":
    unittest.main()
