from __future__ import annotations

import argparse
import json
import os
import secrets
import shlex
import subprocess
import sys
import time
from pathlib import Path

from . import daemon
from .config import default_config_path, load
from .events import make, post
from . import ssh


def init_config(config: dict):
    config["token_file"].parent.mkdir(parents=True, exist_ok=True)
    if not config["token_file"].exists():
        config["token_file"].write_text(secrets.token_urlsafe(32) + "\n"); os.chmod(config["token_file"], 0o600)
    print(f"Token: {config['token_file']}\nStart with: abll start")


def command_run(config: dict, command: list[str]) -> int:
    started = time.time()
    try:
        result = subprocess.run(command); code = result.returncode; state = "success" if code == 0 else "failure"
    except KeyboardInterrupt: code, state = 130, "cancelled"
    post(config, make("command", state, "命令完成" if state == "success" else "命令失败", command=shlex.join(command), cwd=os.getcwd(), exit_code=code, started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))))
    return code


def codex_hook(config: dict, raw: str | None) -> int:
    raw = (raw if raw is not None else sys.stdin.read()).strip()
    try: data = json.loads(raw) if raw else {}
    except json.JSONDecodeError: data = {"message": raw}
    state = str(data.get("status", data.get("event", "success"))).lower(); state = state if state in {"success", "failure", "cancelled"} else "success"
    message = data.get("message") or data.get("summary") or data.get("last-assistant-message") or "Codex 执行完成"
    return 0 if post(config, make("codex", state, message, metadata=data)) else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="abll"); parser.add_argument("--config", type=Path, default=default_config_path())
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("daemon", "start", "stop", "status", "init", "test"): sub.add_parser(action)
    ssh_parser = sub.add_parser("ssh")
    ssh_sub = ssh_parser.add_subparsers(dest="ssh_action", required=True)
    for action in ("setup", "connect", "disconnect", "status"):
        command = ssh_sub.add_parser(action)
        command.add_argument("host")
    ssh_sub.choices["connect"].add_argument("--remote-port", type=int, default=None)
    ssh_sub.choices["connect"].add_argument("--local-port", type=int, default=None)
    ssh_sub.choices["connect"].add_argument("--ssh-port", type=int, default=None, help="SSH server port")
    ssh_sub.choices["connect"].add_argument("ssh_args", nargs=argparse.REMAINDER)
    ssh_sub.choices["setup"].add_argument("--remote-port", type=int, default=None)
    ssh_sub.choices["setup"].add_argument("--ssh-port", type=int, default=None, help="SSH server port")
    run = sub.add_parser("run"); run.add_argument("command", nargs=argparse.REMAINDER)
    hook = sub.add_parser("codex-hook"); hook.add_argument("payload", nargs="?")
    args = parser.parse_args(); config = load(args.config)
    if args.action == "daemon": daemon.run(config)
    elif args.action == "start": return daemon.start(config, args.config)
    elif args.action == "stop": return daemon.stop(config)
    elif args.action == "status": return daemon.status(config)
    elif args.action == "init": init_config(config)
    elif args.action == "test": return 0 if post(config, make("custom", "success", "Agent Bell test")) else 1
    elif args.action == "codex-hook": return codex_hook(config, args.payload)
    elif args.action == "ssh":
        remote_port = getattr(args, "remote_port", None) or config["port"]
        if args.ssh_action == "setup": return ssh.setup(config, args.host, remote_port, args.ssh_port)
        if args.ssh_action == "connect":
            return ssh.connect(config, args.host, args.local_port or config["port"], remote_port, args.ssh_args, args.ssh_port)
        if args.ssh_action == "disconnect": return ssh.disconnect(config, args.host)
        if args.ssh_action == "status": return ssh.status(config, args.host)
    elif args.action == "run":
        command = args.command[1:] if args.command and args.command[0] == "--" else args.command
        if not command: parser.error("run requires a command")
        return command_run(config, command)
    return 0
