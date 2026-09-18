from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request

from .config import token
from .events import make
from .notify import NotificationWorker
from .store import EventStore


class Handler(BaseHTTPRequestHandler):
    store: EventStore; worker: NotificationWorker; config: dict

    def reply(self, status: int, body: dict):
        raw = json.dumps(body).encode(); self.send_response(status)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def authorized(self):
        expected = token(self.config)
        return not expected or self.headers.get("Authorization") == f"Bearer {expected}"

    def do_GET(self):
        self.reply(200, {"status": "ok"}) if self.path in ("/healthz", "/readyz") else self.reply(404, {"error": "not_found"})

    def do_POST(self):
        if self.path == "/ding":
            payload = make("custom", "success", "Agent finished")
        elif self.path in ("/v1/test", "/v1/events"):
            if not self.authorized(): self.reply(401, {"error": "unauthorized"}); return
            if self.path == "/v1/test":
                payload = make("custom", "success", "Agent Bell test")
            else:
                try:
                    length = min(int(self.headers.get("Content-Length", "0")), 65536); payload = json.loads(self.rfile.read(length))
                    if not payload.get("event_id") or not payload.get("source"): raise ValueError("event_id and source are required")
                except (ValueError, json.JSONDecodeError) as exc:
                    self.reply(400, {"error": str(exc)}); return
        else: self.reply(404, {"error": "not_found"}); return
        inserted = self.store.add(payload); self.worker.wakeup.set(); self.reply(202 if inserted else 200, {"accepted": True, "event_id": payload["event_id"]})

    def log_message(self, *_): pass


def run(config: dict):
    Handler.store = EventStore(config["db"]); Handler.config = config; Handler.worker = NotificationWorker(Handler.store, config); Handler.worker.start()
    server = ThreadingHTTPServer((config["host"], config["port"]), Handler)
    print(f"Agent Bell listening on http://{config['host']}:{config['port']}", flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        server.server_close(); Handler.worker.stopping = True; Handler.worker.wakeup.set(); Handler.worker.join(2)


def pid_file(config: dict) -> Path:
    suffix = "" if config["port"] == 18765 else f"-{config['port']}"
    return config["state_dir"] / f"agent-bell{suffix}.pid"


def ready(config: dict) -> bool:
    try:
        with request.urlopen(config["url"] + "/healthz", timeout=.5) as response: return response.status == 200
    except Exception: return False


def start(config: dict, config_path: Path) -> int:
    pid_path = pid_file(config)
    try: existing = int(pid_path.read_text().strip())
    except (FileNotFoundError, ValueError): existing = None
    if existing and ready(config): print(f"Agent Bell is already running (pid {existing})"); return 0
    pid_path.unlink(missing_ok=True); config["state_dir"].mkdir(parents=True, exist_ok=True)
    log = (config["state_dir"] / "agent-bell.log").open("a")
    child = subprocess.Popen([sys.executable, "-m", "agent_bell", "--config", str(config_path.resolve()), "daemon"], stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True); log.close()
    pid_path.write_text(f"{child.pid}\n")
    for _ in range(20):
        if ready(config): print(f"Agent Bell started (pid {child.pid})"); return 0
        time.sleep(.1)
    print(f"Agent Bell did not become ready; see {config['state_dir'] / 'agent-bell.log'}", file=sys.stderr); return 1


def stop(config: dict) -> int:
    path, pid = pid_file(config), None
    try: pid = int(path.read_text().strip())
    except (FileNotFoundError, ValueError): pass
    if not pid or not ready(config): path.unlink(missing_ok=True); print("Agent Bell is not running"); return 0
    os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        if not ready(config): path.unlink(missing_ok=True); print("Agent Bell stopped"); return 0
        time.sleep(.1)
    print(f"Agent Bell did not stop (pid {pid})", file=sys.stderr); return 1


def status(config: dict) -> int:
    path = pid_file(config)
    try: pid = int(path.read_text().strip())
    except (FileNotFoundError, ValueError): pid = None
    if pid and ready(config): print(f"Agent Bell is running (pid {pid}, {config['url']})"); return 0
    print("Agent Bell is not running"); return 1
