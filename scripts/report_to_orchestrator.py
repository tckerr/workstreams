#!/usr/bin/env python3
"""Send workstream reports to a Claude inbox or a Codex thread.

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
import uuid


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


def destination_config():
    kind = os.environ.get("HERDR_WS_ORCHESTRATOR_KIND", "")
    if not kind:
        if os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET"):
            kind = "claude"
        elif os.environ.get("HERDR_WS_ORCHESTRATOR_THREAD") or os.environ.get("CODEX_THREAD_ID"):
            kind = "codex"
        else:
            return {"transport": "none"}
    if kind == "claude":
        destination = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET", "")
        if not destination:
            raise ValueError("CLAUDE_CODE_MESSAGING_SOCKET is missing")
        config = {"transport": "claude", "socket": destination}
        token = os.environ.get("CLAUDE_CODE_MESSAGING_TOKEN")
        if token:
            config["token"] = token
        return config
    if kind != "codex":
        raise ValueError("HERDR_WS_ORCHESTRATOR_KIND must be claude or codex")
    thread = os.environ.get("HERDR_WS_ORCHESTRATOR_THREAD") or os.environ.get("CODEX_THREAD_ID", "")
    try:
        thread = str(uuid.UUID(thread))
    except ValueError as error:
        raise ValueError("Codex orchestration requires a thread UUID (HERDR_WS_ORCHESTRATOR_THREAD or CODEX_THREAD_ID)") from error
    binary = shutil.which("codex")
    if not binary:
        raise ValueError("codex is not available on the orchestrator's PATH")
    config = {
        "transport": "codex", "thread": thread,
        "codex_home": str(Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").resolve()),
        "codex_binary": binary,
    }
    remote = os.environ.get("HERDR_WS_ORCHESTRATOR_REMOTE")
    if remote:
        if not remote.startswith(("unix://", "ws://", "wss://")):
            raise ValueError("HERDR_WS_ORCHESTRATOR_REMOTE must be a Codex server address")
        config["remote"] = remote
    token_env = os.environ.get("HERDR_WS_ORCHESTRATOR_AUTH_TOKEN_ENV")
    if token_env:
        if not remote:
            raise ValueError("a remote server is required for orchestrator auth")
        token = os.environ.get(token_env)
        if not token:
            raise ValueError("the orchestrator auth token environment variable is empty")
        config["codex_token"] = token
    return config


def prepare(worktree, branch, pane, permission_mode, sender_kind):
    """Freeze the parent's destination/auth before starting a different agent."""
    config = destination_config()
    if config["transport"] == "none":
        raise ValueError("no orchestrator reporting destination is available")
    git_dir = subprocess.check_output(
        ["git", "-C", worktree, "rev-parse", "--absolute-git-dir"], text=True
    ).strip()
    directory = Path(git_dir) / "herdr-report"
    directory.mkdir(mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    config.update(branch=branch, pane=pane, permission_mode=permission_mode, sender_kind=sender_kind)
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
        f"{config.get('sender_kind', 'codex').capitalize()} stream {config['branch']} ({config['pane']}): {status}\n"
        f"{STATUSES[status]}\n{message}"
    )
    mode = config["permission_mode"]
    if mode not in ("bypass", "prompting"):
        raise ValueError("permission_mode must describe the sender as bypass or prompting")
    # User frames carry permission attestation inside the canonical text
    # envelope, not a top-level from_mode field (that belongs to control frames).
    # Escape markup so report text cannot close or impersonate the envelope.
    sender = config.get("sender_kind", "codex")
    if sender not in ("claude", "codex"):
        raise ValueError("sender_kind must be claude or codex")
    envelope = (
        f'<cross-session-message from-name="{sender}" from-mode="{mode}">\n'
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
    wire, content = encode_frames(config, status, message)
    # Leave a readable fallback even if the orchestrator exited or delivery is
    # uncertain. No credentials go in the saved report or terminal output.
    saved = Path(config_path).with_name("last-report.json")
    private_json(saved, {"status": status, "content": content})
    if config.get("transport", "claude") == "codex":
        send_codex(config, content, timeout, saved)
        return saved, "queued in Codex; no processing receipt"
    destination = config["socket"]
    if destination.startswith("uds:"):
        destination = destination[4:]
    if not os.path.isabs(destination):
        raise ValueError("orchestrator socket must be an absolute path")
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
    return saved, "socket write completed; no delivery receipt"


def send_codex(config, content, timeout, saved):
    uuid.UUID(config["thread"])
    command = [config["codex_binary"], "queue", "--thread", config["thread"],
               "--message", content, "--resume"]
    env = {**os.environ, "CODEX_HOME": config["codex_home"]}
    if config.get("remote"):
        command.extend(["--remote", config["remote"]])
    if config.get("codex_token"):
        env["HERDR_WS_CODEX_AUTH_TOKEN"] = config["codex_token"]
        command.extend(["--remote-auth-token-env", "HERDR_WS_CODEX_AUTH_TOKEN"])
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OSError(f"Codex report delivery uncertain; saved at {saved}") from error
    if result.returncode:
        raise OSError(f"Codex queue failed (exit {result.returncode}); saved at {saved}")
    if "warn" in (result.stdout + result.stderr).lower():
        raise OSError(f"Codex accepted the queue command with a warning; resume/delivery uncertain; saved at {saved}")


def positive_timeout(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("timeout must be finite and positive")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("destination", help="validate and print the orchestrator transport")
    setup = commands.add_parser("prepare", help="install a private per-stream helper")
    setup.add_argument("--worktree", required=True)
    setup.add_argument("--branch", required=True)
    setup.add_argument("--pane", required=True)
    setup.add_argument("--permission-mode", choices=("bypass", "prompting"), required=True,
                       help="sender's actual launch mode, not the recipient's policy")
    setup.add_argument("--sender-kind", choices=("claude", "codex"), default="codex")
    send = commands.add_parser("send", help="send one report; never retries automatically")
    send.add_argument("status", choices=STATUSES)
    send.add_argument("--config", type=Path, default=Path(__file__).with_name("report.json"))
    body = send.add_mutually_exclusive_group(required=True)
    body.add_argument("--message")
    body.add_argument("--message-file", help="UTF-8 file, or - to read stdin")
    send.add_argument("--timeout", type=positive_timeout, default=5.0)
    args = parser.parse_args(argv)
    try:
        if args.command == "destination":
            print(destination_config()["transport"])
        elif args.command == "prepare":
            print(prepare(args.worktree, args.branch, args.pane, args.permission_mode, args.sender_kind))
        else:
            message = args.message
            if args.message_file == "-":
                message = sys.stdin.read(MAX_FRAME_BYTES + 1)
            elif args.message_file:
                with open(args.message_file, encoding="utf-8") as source:
                    message = source.read(MAX_FRAME_BYTES + 1)
            saved, receipt = send_report(args.config, args.status, message, args.timeout)
            print(f"Sent {args.status} report ({receipt}). Saved at {saved}")
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        print(f"orchestrator-report: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
