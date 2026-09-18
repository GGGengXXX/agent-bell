from __future__ import annotations

import json
import subprocess
import threading
import time

from .store import EventStore


class NotificationWorker(threading.Thread):
    def __init__(self, store: EventStore, config: dict):
        super().__init__(daemon=True); self.store, self.config = store, config
        self.wakeup, self.stopping = threading.Event(), False

    def run(self):
        while not self.stopping:
            rows = self.store.pending()
            if not rows:
                self.wakeup.wait(.5); self.wakeup.clear(); continue
            for event_id, raw, attempts in rows:
                try:
                    self.notify(json.loads(raw)); self.store.mark(event_id, "notified", attempts)
                except Exception as exc:
                    attempts += 1; self.store.mark(event_id, "failed" if attempts >= 3 else "pending", attempts)
                    print(f"ab: notification failed: {exc}")
                    if attempts < 3: time.sleep(min(2 ** attempts, 8))

    def notify(self, payload: dict):
        status = payload.get("status", "unknown")
        message = payload.get("message") or ("任务完成" if status == "success" else "任务结束")
        if self.config["sound"]:
            subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if self.config["popup"]:
            script = f'display dialog {json.dumps(message)} with title "Agent Bell" buttons {{"OK"}} default button "OK" with icon note'
            subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
