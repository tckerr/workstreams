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


if __name__ == "__main__":
    unittest.main()
