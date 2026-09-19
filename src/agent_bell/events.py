from __future__ import annotations

import json
import os
import socket
import sys
import time
import uuid
from urllib import request

from .config import token


def make(source: str, status: str, message: str, **extra) -> dict:
    host = os.uname().nodename
    host_ip = _host_ip()
    event = {"event_id": uuid.uuid4().hex, "source": source, "status": status,
             "title": "Agent Bell", "message": message, "host": host, "host_ip": host_ip,
             "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    event.update({key: value for key, value in extra.items() if value is not None})
    return event


def _host_ip() -> str | None:
    """Find a usable IPv4 address to identify a remote sender in the queue."""
    try:
        addresses = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        for item in addresses:
            address = item[4][0]
            if not address.startswith("127."):
                return address
    except OSError:
        pass
    return None


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
