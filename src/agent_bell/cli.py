from __future__ import annotations

import argparse
import json
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import select
import sqlite3
import socket
import textwrap
import termios
import tty
import time
import unicodedata
from pathlib import Path

from . import daemon
from .config import default_config_path, load
from .events import make, post
from . import ssh
from .store import EventStore


def _codex_thread_name(metadata: dict) -> str | None:
    """Resolve Codex's thread id to its local display name when available."""
    for key in ("thread-name", "thread_name", "title", "name"):
        if metadata.get(key):
            return str(metadata[key]).strip()[:120]
    thread_id = metadata.get("thread-id")
    if thread_id:
        codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
        for database in sorted(codex_home.glob("state_*.sqlite"), reverse=True):
            try:
                with sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=.2) as db:
                    row = db.execute(
                        "SELECT COALESCE(NULLIF(name,''), NULLIF(title,''), NULLIF(preview,''), NULLIF(first_user_message,'')) "
                        "FROM threads WHERE id=? LIMIT 1", (thread_id,)).fetchone()
                if row and row[0]:
                    return str(row[0]).strip()[:120]
            except (OSError, sqlite3.Error):
                continue
    messages = metadata.get("input-messages")
    if isinstance(messages, list) and messages and str(messages[0]).strip():
        return " ".join(str(messages[0]).split())[:120]
    return f"thread-{str(thread_id)[:8]}" if thread_id else None


def init_config(config: dict, announce: bool = True):
    config["token_file"].parent.mkdir(parents=True, exist_ok=True)
    if not config["token_file"].exists():
        config["token_file"].write_text(secrets.token_urlsafe(32) + "\n"); os.chmod(config["token_file"], 0o600)
    if announce:
        print(f"Token: {config['token_file']}\nStart with: abll start")


def _codex_config_path() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser() / "config.toml"


def _notify_line(path: Path) -> tuple[int, str] | None:
    if not path.exists():
        return None
    for number, raw in enumerate(path.read_text().splitlines(), 1):
        if raw.lstrip().startswith("notify") and "=" in raw:
            return number, raw
    return None


def _notify_is_agent_bell(line: str) -> bool:
    lowered = line.lower()
    if "codex-hook" in lowered and ("abll" in lowered or "agent-bell" in lowered):
        return True
    # A user may already have a wrapper which forwards the payload to Agent Bell.
    # Inspect only the executable paths named in the notify assignment.
    for token in line.split('"')[1::2] + line.split("'")[1::2]:
        candidate = Path(token).expanduser()
        if candidate.is_file():
            try:
                content = candidate.read_text(errors="replace").lower()
            except OSError:
                continue
            if "codex-hook" in content or "agent-bell" in content:
                return True
    return False


def _agent_bell_command() -> list[str]:
    executable = shutil.which("abll") or shutil.which("agent-bell")
    if executable:
        return [executable, "codex-hook"]
    return [sys.executable, "-m", "agent_bell", "codex-hook"]


def codex_setup(config: dict, config_path: Path, check: bool = False, force: bool = False, start: bool = True) -> int:
    """Install Agent Bell's Codex notify command without clobbering user hooks."""
    codex_config = _codex_config_path()
    current = _notify_line(codex_config)
    command = _agent_bell_command()
    rendered = "notify = " + json.dumps(command, ensure_ascii=False)

    if current and _notify_is_agent_bell(current[1]):
        print(f"Codex Agent Bell is already configured ({codex_config}:{current[0]}).")
    elif current and not force:
        print(f"Codex already has a notify command ({codex_config}:{current[0]}):")
        print(f"  {current[1].strip()}")
        print("Refusing to replace it. Use --force to back it up and install Agent Bell.", file=sys.stderr)
        return 2
    elif not check:
        codex_config.parent.mkdir(parents=True, exist_ok=True)
        lines = codex_config.read_text().splitlines() if codex_config.exists() else []
        if current:
            backup = codex_config.with_name(codex_config.name + ".agent-bell.bak")
            shutil.copy2(codex_config, backup)
            lines[current[0] - 1] = rendered
            print(f"Backed up existing Codex config to {backup}")
        else:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(rendered)
        codex_config.write_text("\n".join(lines) + "\n")
        print(f"Configured Codex notify in {codex_config}")
    else:
        print(f"Codex notify would be set to: {rendered}")

    if check:
        print("Codex Agent Bell check passed.")
        return 0
    if start:
        init_config(config, announce=False)
        return daemon.start(config, config_path)
    return 0


def command_run(config: dict, command: list[str], note: str | None = None) -> int:
    started = time.time()
    try:
        result = subprocess.run(command); code = result.returncode; state = "success" if code == 0 else "failure"
    except KeyboardInterrupt: code, state = 130, "cancelled"
    post(config, make("command", state, "命令完成" if state == "success" else "命令失败", host_ip=config.get("host_ip"), command=shlex.join(command), note=note, cwd=os.getcwd(), exit_code=code, started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))))
    return code


def codex_hook(config: dict, raw: str | None) -> int:
    raw = (raw if raw is not None else sys.stdin.read()).strip()
    try: data = json.loads(raw) if raw else {}
    except json.JSONDecodeError: data = {"message": raw}
    state = str(data.get("status", data.get("event", "success"))).lower(); state = state if state in {"success", "failure", "cancelled"} else "success"
    message = data.get("message") or data.get("summary") or data.get("last-assistant-message") or "Codex 执行完成"
    thread_name = _codex_thread_name(data)
    return 0 if post(config, make("codex", state, message, host_ip=config.get("host_ip"), metadata=data,
                                  thread_id=data.get("thread-id"), thread_name=thread_name)) else 1


def _queue_line(text: str, color: str = "") -> str:
    return f"{color}{text}\033[0m" if color else text


def _display_width(value: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in "WFA" else 1 for char in value)


def _fit(value: object, width: int) -> str:
    text = " ".join(str(value).replace("\x1b", "").split())
    if _display_width(text) <= width:
        return text
    result = ""
    for char in text:
        if _display_width(result + char + "…") > width:
            break
        result += char
    return result + "…"


def _event_thread_name(payload: dict) -> str:
    value = payload.get("thread_name")
    if not value and isinstance(payload.get("metadata"), dict):
        value = payload["metadata"].get("thread-name") or payload["metadata"].get("thread_name")
    return "-" if not value else " ".join(str(value).split())[:28]


def _event_project_dir(payload: dict) -> str:
    value = payload.get("cwd") or payload.get("project_dir")
    if not value and isinstance(payload.get("metadata"), dict):
        metadata = payload["metadata"]
        value = metadata.get("cwd") or metadata.get("project_dir") or metadata.get("working-directory")
    if not value:
        return "-"
    path = str(value).rstrip("/")
    return path.rsplit("/", 1)[-1] or "/"


def _event_project_path(payload: dict) -> str:
    value = payload.get("cwd") or payload.get("project_dir")
    if not value and isinstance(payload.get("metadata"), dict):
        metadata = payload["metadata"]
        value = metadata.get("cwd") or metadata.get("project_dir") or metadata.get("working-directory")
    return str(value) if value else "-"


def _checkpoint_ips(config: dict) -> list[str]:
    values = []
    for path in config.get("state_dir", Path()).glob("ssh-*.json"):
        try:
            checkpoint = json.loads(path.read_text())
            remote_ip = checkpoint.get("remote_ip")
            target = str(checkpoint.get("target_host") or checkpoint.get("host", ""))
            if not remote_ip and target:
                remote_ip = target.rsplit("@", 1)[-1].strip("[]")
                if ":" in remote_ip and remote_ip.count(":") == 1:
                    remote_ip = remote_ip.rsplit(":", 1)[0]
                try:
                    remote_ip = socket.gethostbyname(remote_ip)
                except OSError:
                    pass
            if remote_ip and remote_ip not in values:
                values.append(str(remote_ip))
        except (OSError, ValueError, TypeError):
            continue
    return values


def _event_ip(payload: dict, config: dict | None = None) -> str:
    value = payload.get("host_ip") or payload.get("remote_ip")
    if not value and isinstance(payload.get("metadata"), dict):
        value = payload["metadata"].get("host_ip") or payload["metadata"].get("remote_ip")
    if value and not str(value).startswith("127."):
        return str(value)
    host = str(payload.get("host", ""))
    if host and all(part.isdigit() for part in host.split(".")) and len(host.split(".")) == 4:
        return host
    try:
        resolved = socket.gethostbyname(host) if host else ""
        if resolved and not resolved.startswith("127."):
            return resolved
    except OSError:
        pass
    known = _checkpoint_ips(config or {})
    return known[0] if len(known) == 1 else "-"


def _queue_key() -> str:
    """Read one key, normalizing the common terminal arrow sequences."""
    key = sys.stdin.read(1)
    if key != "\x1b":
        return key
    # xterm sends CSI (ESC [ A/B), while some terminals use SS3 (ESC O A/B).
    sequence = ""
    for _ in range(2):
        if not select.select([sys.stdin], [], [], .1)[0]:
            break
        sequence += sys.stdin.read(1)
    return {"[A": "k", "[B": "j", "OA": "k", "OB": "j"}.get(sequence, "\x1b")


def _queue_render(store: EventStore, config: dict, selected: int, detail: bool, unread_only: bool) -> tuple[list[dict], int]:
    items = store.events(unread_only)
    if items:
        selected = min(selected, len(items) - 1)
    else:
        selected = 0
    print("\033[2J\033[H", end="")
    print(_queue_line("  █████╗  ██████╗ ██╗     ██╗     ", "\033[96m"))
    print(_queue_line(" ██╔══██╗██╔═══██╗██║     ██║     ", "\033[96m"))
    print(_queue_line(" ███████║██║   ██║██║     ██║     ", "\033[95m"))
    print(_queue_line(" ██╔══██║██║   ██║██║     ██║     ", "\033[95m"))
    print(_queue_line(" ██║  ██║╚██████╔╝███████╗███████╗", "\033[96m"))
    print(_queue_line(" ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚══════╝", "\033[96m"))
    unread = sum(item["read_at"] is None for item in items)
    heading = _queue_line("MESSAGE QUEUE", "\033[1;97m")
    print(f"\n  {heading}  {len(items)} messages · {unread} unread")
    print("  " + "─" * 130)
    if not items:
        print("\n  Queue is empty. Completed responses will appear here automatically.")
    else:
        print("  # | SRC    | REMOTE IP       | TYPE    | THREAD NAME       | PROJECT DIR       | CONTENT")
        for index, item in enumerate(items):
            payload = item["payload"]; marker = "*" if item["read_at"] is None else " "
            color = "\033[1;97m" if index == selected else ("\033[93m" if item["read_at"] is None else "\033[90m")
            state = payload.get("status", item["status"]).upper()
            source = payload.get("source", "unknown")
            origin = payload.get("origin") or ("local" if payload.get("host") == os.uname().nodename else "remote")
            origin = "LOCAL" if origin == "local" else "REMOTE"
            thread = _fit(_event_thread_name(payload), 17)
            project = _fit(_event_project_dir(payload), 18)
            message = _fit(payload.get("message", "(no message)"), 32)
            line = f" {marker}{index + 1:>2} | {_fit(origin, 6):<6} | {_fit(_event_ip(payload, config), 15):<15} | {_fit(source, 7):<7} | {thread:<17} | {project:<18} | {message}"
            print(_queue_line(line, color))
        if detail:
            item = items[selected]; payload = item["payload"]
            print("\n  " + "─" * 72)
            print(_queue_line(f"  DETAILS · {selected + 1}/{len(items)}", "\033[1;96m"))
            fields = [("message", payload.get("message", "")), ("source", payload.get("source", "")),
                      ("thread", _event_thread_name(payload)), ("project dir", _event_project_path(payload)),
                      ("remote ip", _event_ip(payload, config)),
                      ("status", payload.get("status", "")), ("finished", payload.get("finished_at", "")),
                      ("event id", item["event_id"]), ("attempts", item["attempts"])]
            fields.extend((key, value) for key, value in payload.items()
                          if key not in {"event_id", "message", "source", "status", "finished_at", "metadata"})
            for key, value in fields:
                value = str(value).replace("\n", " ")
                print(textwrap.fill(f"  {key:<10} {value}", width=78, subsequent_indent="  "))
            metadata = payload.get("metadata")
            if metadata: print(textwrap.indent(json.dumps(metadata, ensure_ascii=False, indent=2), "  "))
    print("\n  ↑/↓ or j/k select   Enter/d details   r read   u unread   a all read   n next unread   q quit")
    return items, selected


def queue_command(config: dict, once: bool = False, unread_only: bool = False, interval: float = 2.0) -> int:
    store = EventStore(config["db"])
    if once or not sys.stdin.isatty() or not sys.stdout.isatty():
        items = store.events(unread_only)
        for item in items:
            payload = item["payload"]
            origin = payload.get("origin") or ("local" if payload.get("host") == os.uname().nodename else "remote")
            origin = "LOCAL" if origin == "local" else "REMOTE"
            thread = _event_thread_name(payload)
            project = _fit(_event_project_dir(payload), 18)
            message = _fit(payload.get("message", "(no message)"), 48)
            print(f"{'UNREAD' if item['read_at'] is None else 'READ':<6} | {_fit(origin, 6):<6} | {_fit(_event_ip(payload, config), 15):<15} | {_fit(payload.get('source','unknown'), 7):<7} | {_fit(thread, 17):<17} | {project:<18} | {message}")
        return 0
    selected, detail = 0, False
    old = termios.tcgetattr(sys.stdin); tty.setcbreak(sys.stdin.fileno())
    try:
        while True:
            items, selected = _queue_render(store, config, selected, detail, unread_only)
            ready, _, _ = select.select([sys.stdin], [], [], interval)
            if not ready: continue
            key = _queue_key()
            if key in ("q", "\x03"): break
            if items and key == "j": selected = min(selected + 1, len(items) - 1)
            elif items and key == "k": selected = max(selected - 1, 0)
            elif key in ("\r", "d"): detail = not detail
            elif items and key == "r": store.mark_read(items[selected]["event_id"], True)
            elif items and key == "u": store.mark_read(items[selected]["event_id"], False)
            elif key == "a": store.mark_all_read(True)
            elif key == "n" and items:
                unread = [i for i, item in enumerate(items) if item["read_at"] is None]
                if unread: selected = next((i for i in unread if i > selected), unread[0])
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old); print("\033[0m\033[2J\033[H", end="")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="abll"); parser.add_argument("--config", type=Path, default=default_config_path())
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("daemon", "start", "stop", "status", "init", "test"): sub.add_parser(action)
    queue = sub.add_parser("queue", aliases=["inbox"], help="open the message queue")
    queue.add_argument("--once", action="store_true", help="print messages and exit")
    queue.add_argument("--unread", action="store_true", help="show unread messages only")
    queue.add_argument("--interval", type=float, default=2.0, help="refresh interval in seconds")
    ssh_parser = sub.add_parser("ssh")
    ssh_sub = ssh_parser.add_subparsers(dest="ssh_action", required=True)
    for action in ("setup", "configure", "connect", "disconnect", "status"):
        command = ssh_sub.add_parser(action)
        command.add_argument("host")
    ssh_sub.choices["connect"].add_argument("--remote-port", type=int, default=None)
    ssh_sub.choices["connect"].add_argument("--local-port", type=int, default=None)
    ssh_sub.choices["connect"].add_argument("--ssh-port", type=int, default=None, help="SSH server port")
    ssh_sub.choices["connect"].add_argument("ssh_args", nargs=argparse.REMAINDER)
    ssh_sub.choices["setup"].add_argument("--remote-port", type=int, default=None)
    ssh_sub.choices["setup"].add_argument("--ssh-port", type=int, default=None, help="SSH server port")
    ssh_sub.choices["configure"].add_argument("--remote-port", type=int, default=None)
    ssh_sub.choices["configure"].add_argument("--local-port", type=int, default=None)
    ssh_sub.choices["configure"].add_argument("--ssh-port", type=int, default=None, help="SSH server port")
    ssh_sub.choices["configure"].add_argument("ssh_args", nargs=argparse.REMAINDER)
    run = sub.add_parser("run"); run.add_argument("command", nargs=argparse.REMAINDER)
    hook = sub.add_parser("codex-hook"); hook.add_argument("payload", nargs="?")
    codex = sub.add_parser("codex", help="configure Codex integrations")
    codex_sub = codex.add_subparsers(dest="codex_action", required=True)
    setup = codex_sub.add_parser("setup", help="configure Codex notifications and start Agent Bell")
    setup.add_argument("--check", action="store_true", help="only inspect the current Codex configuration")
    setup.add_argument("--force", action="store_true", help="back up and replace an existing notify command")
    setup.add_argument("--no-start", action="store_true", help="do not start the Agent Bell daemon")
    args = parser.parse_args(); config = load(args.config)
    if args.action == "daemon": daemon.run(config)
    elif args.action == "start": return daemon.start(config, args.config)
    elif args.action == "stop": return daemon.stop(config)
    elif args.action == "status": return daemon.status(config)
    elif args.action == "init": init_config(config)
    elif args.action == "test": return 0 if post(config, make("custom", "success", "Agent Bell test", host_ip=config.get("host_ip"))) else 1
    elif args.action in ("queue", "inbox"): return queue_command(config, args.once, args.unread, args.interval)
    elif args.action == "codex-hook": return codex_hook(config, args.payload)
    elif args.action == "codex" and args.codex_action == "setup":
        return codex_setup(config, args.config, args.check, args.force, not args.no_start)
    elif args.action == "ssh":
        remote_port = getattr(args, "remote_port", None) or config["port"]
        if args.ssh_action == "setup": return ssh.setup(config, args.host, remote_port, args.ssh_port)
        if args.ssh_action == "configure":
            return ssh.configure(config, args.host, args.local_port or config["port"], remote_port,
                                 args.ssh_args, args.ssh_port)
        if args.ssh_action == "connect":
            return ssh.connect(config, args.host, args.local_port or config["port"], remote_port, args.ssh_args, args.ssh_port)
        if args.ssh_action == "disconnect": return ssh.disconnect(config, args.host)
        if args.ssh_action == "status": return ssh.status(config, args.host)
    elif args.action == "run":
        note = None
        if args.command and args.command[0] == "--":
            command = args.command[1:]
        elif "--" in args.command:
            separator = args.command.index("--")
            note = " ".join(args.command[:separator]).strip() or None
            command = args.command[separator + 1:]
        else:
            command = args.command
        if not command: parser.error("run requires a command")
        return command_run(config, command, note)
    return 0
