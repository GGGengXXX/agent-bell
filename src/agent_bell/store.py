from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


class EventStore:
    MAX_EVENTS = 20

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL, read_at REAL)")
        # Existing installations predate the inbox.  SQLite has no IF NOT EXISTS
        # form for columns, so make the tiny migration idempotent.
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(events)")}
        if "read_at" not in columns:
            self.db.execute("ALTER TABLE events ADD COLUMN read_at REAL")
        self.db.commit(); self.lock = threading.Lock()
        self.prune()

    def add(self, payload: dict) -> bool:
        now = time.time()
        with self.lock:
            cur = self.db.execute("INSERT OR IGNORE INTO events(event_id,payload,created_at,updated_at) VALUES(?,?,?,?)", (payload["event_id"], json.dumps(payload), now, now))
            self.db.execute("DELETE FROM events WHERE event_id NOT IN (SELECT event_id FROM events ORDER BY created_at DESC, rowid DESC LIMIT ?)", (self.MAX_EVENTS,))
            self.db.commit(); return cur.rowcount == 1

    def prune(self):
        """Keep only the newest queue entries, including after an upgrade."""
        with self.lock:
            self.db.execute("DELETE FROM events WHERE event_id NOT IN (SELECT event_id FROM events ORDER BY created_at DESC, rowid DESC LIMIT ?)", (self.MAX_EVENTS,))
            self.db.commit()

    def pending(self):
        with self.lock:
            return self.db.execute("SELECT event_id,payload,attempts FROM events WHERE status='pending' ORDER BY created_at").fetchall()

    def mark(self, event_id: str, status: str, attempts: int):
        with self.lock:
            self.db.execute("UPDATE events SET status=?,attempts=?,updated_at=? WHERE event_id=?", (status, attempts, time.time(), event_id)); self.db.commit()

    def events(self, unread_only: bool = False):
        """Return inbox events, newest first, with decoded payloads."""
        query = "SELECT event_id,payload,status,attempts,created_at,updated_at,read_at FROM events"
        if unread_only:
            query += " WHERE read_at IS NULL"
        query += " ORDER BY created_at DESC"
        with self.lock:
            rows = self.db.execute(query).fetchall()
        result = []
        for event_id, raw, status, attempts, created_at, updated_at, read_at in rows:
            payload = json.loads(raw)
            result.append({"event_id": event_id, "payload": payload, "status": status,
                           "attempts": attempts, "created_at": created_at,
                           "updated_at": updated_at, "read_at": read_at})
        return result

    def mark_read(self, event_id: str, read: bool = True):
        with self.lock:
            self.db.execute("UPDATE events SET read_at=?,updated_at=? WHERE event_id=?",
                            (time.time() if read else None, time.time(), event_id))
            self.db.commit()

    def mark_all_read(self, read: bool = True):
        with self.lock:
            self.db.execute("UPDATE events SET read_at=?,updated_at=? WHERE read_at IS NULL" if read else
                            "UPDATE events SET read_at=?,updated_at=?",
                            (time.time() if read else None, time.time()))
            self.db.commit()
