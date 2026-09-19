"""SSH reverse tunnel and remote Agent Bell configuration helpers."""

from __future__ import annotations

import os
import json
import shlex
import signal
import subprocess
import time
import socket
from pathlib import Path

from .config import token


def pid_file(config: dict, host: str) -> Path:
    safe = "".join(char if char.isalnum() or char in ".-_" else "_" for char in host)
    return config["state_dir"] / f"ssh-{safe}.pid"


def checkpoint_file(config: dict, host: str) -> Path:
    """Return the durable progress file for an SSH setup."""
    path = pid_file(config, host)
    return path.with_suffix(".json")


def _save_checkpoint(config: dict, host: str, **values) -> None:
    path = checkpoint_file(config, host)
    path.parent.mkdir(parents=True, exist_ok=True)
    current = {}
    try:
        current = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        pass
    current.update(values)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(current, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _load_checkpoint(config: dict, host: str) -> dict:
    try:
        value = json.loads(checkpoint_file(config, host).read_text())
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


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


def setup(config: dict, host: str, remote_port: int, ssh_port: int | None = None,
          retries: int = 3) -> int:
    """Copy the local token and a minimal sender config to the remote host."""
    value = token(config)
    if not value:
        print("No Agent Bell token found. Run 'abll init' first.", flush=True)
        return 1
    destination = host.rsplit("@", 1)[-1].strip().strip("[]")
    try:
        remote_ip = socket.gethostbyname(destination)
    except OSError:
        remote_ip = destination
    config_text = (f"URL: http://127.0.0.1:{remote_port}\n"
                   f"HOST_IP: {remote_ip}\n"
                   "TOKEN_FILE: ~/.config/agent-bell/token\n")
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
    for attempt in range(1, retries + 1):
        result = subprocess.run(command + ["-T", host, "sh", "-s"], input=remote_script, text=True)
        if result.returncode == 0:
            _save_checkpoint(config, host, setup="done", remote_port=remote_port, remote_ip=remote_ip)
            print(f"Remote Agent Bell config installed on {host}")
            print("The remote host must have 'abll' installed to run wrapped commands.")
            return 0
        if attempt < retries:
            delay = min(2 ** (attempt - 1), 8)
            print(f"SSH setup attempt {attempt} failed; retrying in {delay}s...", flush=True)
            time.sleep(delay)
    _save_checkpoint(config, host, setup="failed")
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
    # Keep a small supervisor around the SSH client.  A dropped connection is
    # retried with backoff, while the pid still represents one manageable job.
    ssh_command = shlex.join(command)
    supervisor = ("while :; do " + ssh_command + "; rc=$?; "
                  "[ $rc -eq 0 ] && exit 0; "
                  "sleep 5; done")
    child = subprocess.Popen(["sh", "-c", supervisor], stdin=subprocess.DEVNULL,
                             stdout=log, stderr=log, start_new_session=True)
    log.close()
    path.write_text(f"{child.pid}\n")
    time.sleep(0.4)
    if not _alive(child.pid):
        path.unlink(missing_ok=True)
        print(f"SSH tunnel failed; see {log_path}", flush=True)
        return 1
    print(f"SSH tunnel connected: {host} (remote 127.0.0.1:{remote_port}, pid {child.pid})")
    _save_checkpoint(config, host, tunnel="connected", remote_port=remote_port, pid=child.pid)
    return 0


def configure(config: dict, host: str, local_port: int, remote_port: int,
               ssh_args: list[str], ssh_port: int | None = None) -> int:
    """Idempotently configure and connect a remote host with resumable steps."""
    checkpoint = _load_checkpoint(config, host)
    # The destination is already passed positionally to _save_checkpoint;
    # store it under a distinct key to avoid passing ``host`` twice.
    _save_checkpoint(config, host, target_host=host, remote_port=remote_port, step="setup")
    setup_done = (checkpoint.get("setup") == "done"
                  and checkpoint.get("remote_port") == remote_port
                  and checkpoint.get("remote_ip"))
    if not setup_done and setup(config, host, remote_port, ssh_port) != 0:
        print(f"SSH configuration paused after setup failure; rerun to resume: {host}", flush=True)
        return 1
    if setup_done:
        print(f"Remote Agent Bell config already installed on {host}; resuming connection")
    _save_checkpoint(config, host, step="connect")
    result = connect(config, host, local_port, remote_port, ssh_args, ssh_port)
    if result == 0:
        _save_checkpoint(config, host, step="complete")
    return result


def disconnect(config: dict, host: str) -> int:
    path = pid_file(config, host)
    pid = _read_pid(path)
    if not _alive(pid):
        path.unlink(missing_ok=True)
        _save_checkpoint(config, host, tunnel="disconnected", step="complete")
        print(f"SSH tunnel is not running for {host}")
        return 0
    # The supervisor and its current ssh child share a process group.
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    for _ in range(20):
        if not _alive(pid):
            path.unlink(missing_ok=True)
            _save_checkpoint(config, host, tunnel="disconnected", step="complete")
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
