from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


class EventStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL)")
        self.db.commit(); self.lock = threading.Lock()

    def add(self, payload: dict) -> bool:
        now = time.time()
        with self.lock:
            cur = self.db.execute("INSERT OR IGNORE INTO events(event_id,payload,created_at,updated_at) VALUES(?,?,?,?)", (payload["event_id"], json.dumps(payload), now, now))
            self.db.commit(); return cur.rowcount == 1

    def pending(self):
        with self.lock:
            return self.db.execute("SELECT event_id,payload,attempts FROM events WHERE status='pending' ORDER BY created_at").fetchall()

    def mark(self, event_id: str, status: str, attempts: int):
        with self.lock:
            self.db.execute("UPDATE events SET status=?,attempts=?,updated_at=? WHERE event_id=?", (status, attempts, time.time(), event_id)); self.db.commit()
