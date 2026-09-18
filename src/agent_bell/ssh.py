"""SSH reverse tunnel and remote Agent Bell configuration helpers."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from pathlib import Path

from .config import token


def pid_file(config: dict, host: str) -> Path:
    safe = "".join(char if char.isalnum() or char in ".-_" else "_" for char in host)
    return config["state_dir"] / f"ssh-{safe}.pid"


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def setup(config: dict, host: str, remote_port: int, ssh_port: int | None = None) -> int:
    """Copy the local token and a minimal sender config to the remote host."""
    value = token(config)
    if not value:
        print("No Agent Bell token found. Run 'abll init' first.", flush=True)
        return 1
    config_text = f"URL: http://127.0.0.1:{remote_port}\nTOKEN_FILE: ~/.config/agent-bell/token\n"
    # Send the token over SSH stdin instead of placing it in the remote
    # command line where it could be visible through process listings.
    remote_script = (
        "set -eu\n"
        "mkdir -p ~/.config/agent-bell\n"
        "cat > ~/.config/agent-bell/token <<'AGENT_BELL_TOKEN'\n"
        f"{value}\n"
        "AGENT_BELL_TOKEN\n"
        "chmod 600 ~/.config/agent-bell/token\n"
        "cat > ~/.config/agent-bell/config.yaml <<'AGENT_BELL_CONFIG'\n"
        f"{config_text}"
        "AGENT_BELL_CONFIG\n"
    )
    command = ["ssh"]
    if ssh_port:
        command += ["-p", str(ssh_port)]
    result = subprocess.run(command + ["-T", host, "sh", "-s"], input=remote_script, text=True)
    if result.returncode == 0:
        print(f"Remote Agent Bell config installed on {host}")
        print("The remote host must have 'abll' installed to run wrapped commands.")
    return result.returncode


def connect(config: dict, host: str, local_port: int, remote_port: int, ssh_args: list[str], ssh_port: int | None = None) -> int:
    path = pid_file(config, host)
    existing = _read_pid(path)
    if _alive(existing):
        print(f"SSH tunnel is already running for {host} (pid {existing})")
        return 0
    config["state_dir"].mkdir(parents=True, exist_ok=True)
    log_path = config["state_dir"] / f"ssh-{path.stem[4:]}.log"
    log = log_path.open("a")
    command = ["ssh"]
    if ssh_port:
        command += ["-p", str(ssh_port)]
    command += ["-N", "-T", "-o", "ExitOnForwardFailure=yes",
               "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
               "-R", f"{remote_port}:127.0.0.1:{local_port}"] + ssh_args + [host]
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    log.close()
    path.write_text(f"{child.pid}\n")
    time.sleep(0.4)
    if not _alive(child.pid):
        path.unlink(missing_ok=True)
        print(f"SSH tunnel failed; see {log_path}", flush=True)
        return 1
    print(f"SSH tunnel connected: {host} (remote 127.0.0.1:{remote_port}, pid {child.pid})")
    return 0


def disconnect(config: dict, host: str) -> int:
    path = pid_file(config, host)
    pid = _read_pid(path)
    if not _alive(pid):
        path.unlink(missing_ok=True)
        print(f"SSH tunnel is not running for {host}")
        return 0
    os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        if not _alive(pid):
            path.unlink(missing_ok=True)
            print(f"SSH tunnel disconnected: {host}")
            return 0
        time.sleep(0.1)
    print(f"SSH tunnel did not stop (pid {pid})")
    return 1


def status(config: dict, host: str) -> int:
    pid = _read_pid(pid_file(config, host))
    if _alive(pid):
        print(f"SSH tunnel is running for {host} (pid {pid})")
        return 0
    print(f"SSH tunnel is not running for {host}")
    return 1
