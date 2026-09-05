#!/usr/bin/env python3
"""Send Codex stream reports using Claude Code's newline-delimited JSON inbox.

The wire format was checked against Claude Code 2.1.261. The server accepts an
optional auth line followed by a user line, and closes after the client sends
EOF. It does not return an application-level delivery receipt.

User message.content must be wrapped in the canonical cross-session-message
envelope with from-name and from-mode (bypass or prompting). A top-level
from_mode only applies to control messages. Without the envelope, a recipient
that bypasses prompts holds the message for manual review. The sender's mode
comes from bootstrap's launch configuration; recipient hold/refuse policies
still apply. No reply address is advertised because Codex has no UDS inbox.
"""

import argparse
import html
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys


MAX_FRAME_BYTES = 65536
STATUSES = {
    "started": "Task acknowledged; work is starting. Keep the stream open.",
    "ready": "Work is ready for review; not merged. Keep the stream open.",
    "blocked": "The stream needs help. Keep the stream open.",
    "merged": "Merge has landed and the worktree is clean; teardown is safe.",
}


def private_json(path, value):
    # These files live in a private directory under the per-worktree git dir.
    with open(path, "w", encoding="utf-8", opener=lambda p, f: os.open(p, f, 0o600)) as out:
        json.dump(value, out, ensure_ascii=False)
        out.write("\n")


def prepare(worktree, branch, pane, permission_mode):
    """Freeze the parent's destination/auth before starting a different agent."""
    destination = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET", "")
    if not destination:
        raise ValueError("CLAUDE_CODE_MESSAGING_SOCKET is missing")
    git_dir = subprocess.check_output(
        ["git", "-C", worktree, "rev-parse", "--absolute-git-dir"], text=True
    ).strip()
    directory = Path(git_dir) / "herdr-report"
    directory.mkdir(mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    config = {"socket": destination, "branch": branch, "pane": pane,
              "permission_mode": permission_mode}
    token = os.environ.get("CLAUDE_CODE_MESSAGING_TOKEN")
    if token:
        config["token"] = token
    private_json(directory / "report.json", config)
    # Keep the helper usable even if the plugin cache is refreshed mid-stream.
    helper = directory / "report.py"
    shutil.copyfile(__file__, helper)
    os.chmod(helper, 0o700)
    return "python3 " + shlex.quote(str(helper))


def encode_frames(config, status, message):
    if not message.strip():
        raise ValueError("report message must not be empty")
    content = (
        f"Codex stream {config['branch']} ({config['pane']}): {status}\n"
        f"{STATUSES[status]}\n{message}"
    )
    mode = config["permission_mode"]
    if mode not in ("bypass", "prompting"):
        raise ValueError("permission_mode must describe the sender as bypass or prompting")
    # User frames carry permission attestation inside the canonical text
    # envelope, not a top-level from_mode field (that belongs to control frames).
    # Escape markup so report text cannot close or impersonate the envelope.
    envelope = (
        f'<cross-session-message from-name="codex" from-mode="{mode}">\n'
        f'{html.escape(content, quote=False)}\n</cross-session-message>'
    )
    frames = []
    if config.get("token"):
        frames.append({"type": "auth", "token": config["token"]})
    # A Codex stream has no reply socket. Do not invent a uds: sender address;
    # include its branch and herdr pane in the content instead.
    frames.append({"type": "user", "message": {"role": "user", "content": envelope}})
    wire = "".join(json.dumps(frame, ensure_ascii=False) + "\n" for frame in frames).encode("utf-8")
    if len(wire) > MAX_FRAME_BYTES:
        raise ValueError(f"report exceeds {MAX_FRAME_BYTES} bytes; shorten the message")
    return wire, content


def send_report(config_path, status, message, timeout):
    with open(config_path, encoding="utf-8") as source:
        config = json.load(source)
    destination = config["socket"]
    if destination.startswith("uds:"):
        destination = destination[4:]
    if not os.path.isabs(destination):
        raise ValueError("orchestrator socket must be an absolute path")
    wire, content = encode_frames(config, status, message)
    # Leave a readable fallback even if the orchestrator exited or delivery is
    # uncertain. No credentials go in the saved report or terminal output.
    saved = Path(config_path).with_name("last-report.json")
    private_json(saved, {"status": status, "content": content})
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(destination)
            client.sendall(wire)
            client.shutdown(socket.SHUT_WR)
            # Drain until the server closes. This detects connection errors
            # but is not a delivery acknowledgement.
            while client.recv(4096):
                pass
    except OSError as error:
        raise OSError(f"report delivery uncertain: {error}; saved at {saved}") from error
    return saved


def positive_timeout(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("timeout must be finite and positive")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("prepare", help="install a private per-stream helper")
    setup.add_argument("--worktree", required=True)
    setup.add_argument("--branch", required=True)
    setup.add_argument("--pane", required=True)
    setup.add_argument("--permission-mode", choices=("bypass", "prompting"), required=True,
                       help="sender's actual launch mode, not the recipient's policy")
    send = commands.add_parser("send", help="send one report; never retries automatically")
    send.add_argument("status", choices=STATUSES)
    send.add_argument("--config", type=Path, default=Path(__file__).with_name("report.json"))
    body = send.add_mutually_exclusive_group(required=True)
    body.add_argument("--message")
    body.add_argument("--message-file", help="UTF-8 file, or - to read stdin")
    send.add_argument("--timeout", type=positive_timeout, default=5.0)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            print(prepare(args.worktree, args.branch, args.pane, args.permission_mode))
        else:
            message = args.message
            if args.message_file == "-":
                message = sys.stdin.read(MAX_FRAME_BYTES + 1)
            elif args.message_file:
                with open(args.message_file, encoding="utf-8") as source:
                    message = source.read(MAX_FRAME_BYTES + 1)
            saved = send_report(args.config, args.status, message, args.timeout)
            print(f"Sent {args.status} report (socket write completed; no delivery receipt). Saved at {saved}")
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        print(f"orchestrator-report: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
