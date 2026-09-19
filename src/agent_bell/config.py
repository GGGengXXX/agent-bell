from __future__ import annotations

import os
from pathlib import Path

USER_CONFIG = Path.home() / ".config" / "agent-bell" / "config.yaml"
DEFAULT_DB = Path.home() / ".local" / "state" / "agent-bell" / "events.db"
DEFAULT_TOKEN = Path.home() / ".config" / "agent-bell" / "token"


def default_config_path() -> Path:
    return Path(os.environ.get("AGENT_BELL_CONFIG", str(USER_CONFIG))).expanduser()


def load(path: Path) -> dict:
    values = {}
    if path.exists():
        for raw in path.read_text().splitlines():
            line = raw.split("#", 1)[0].strip()
            if line and ":" in line:
                key, value = (part.strip() for part in line.split(":", 1))
                values[key.upper()] = value.strip('"\'')
    host, port = values.get("HOST", "127.0.0.1"), int(values.get("PORT", "18765"))
    return {
        "host": host, "host_ip": values.get("HOST_IP") or None, "port": port,
        "db": Path(os.path.expanduser(values.get("DB", str(DEFAULT_DB)))),
        "token_file": Path(os.path.expanduser(values.get("TOKEN_FILE", str(DEFAULT_TOKEN)))),
        "sound": values.get("SOUND", "true").lower() not in {"0", "false", "no"},
        "popup": values.get("POPUP", "true").lower() not in {"0", "false", "no"},
        "url": values.get("URL", f"http://{host}:{port}"),
        "state_dir": Path(os.path.expanduser(values.get("STATE_DIR", str(DEFAULT_DB.parent)))),
    }


def token(config: dict) -> str | None:
    try:
        return config["token_file"].read_text().strip() or None
    except FileNotFoundError:
        return None
