from __future__ import annotations

import json
import os
import sys
import time
import uuid
from urllib import request

from .config import token


def make(source: str, status: str, message: str, **extra) -> dict:
    event = {"event_id": uuid.uuid4().hex, "source": source, "status": status,
             "title": "Agent Bell", "message": message, "host": os.uname().nodename,
             "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    event.update({key: value for key, value in extra.items() if value is not None})
    return event


def post(config: dict, payload: dict) -> bool:
    headers = {"Content-Type": "application/json"}
    if value := token(config):
        headers["Authorization"] = f"Bearer {value}"
    try:
        req = request.Request(config["url"] + "/v1/events", data=json.dumps(payload).encode(), headers=headers, method="POST")
        with request.urlopen(req, timeout=2) as response:
            return response.status in (200, 202)
    except Exception as exc:
        print(f"abll: unable to send event: {exc}", file=sys.stderr)
        return False
