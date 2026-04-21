"""
JSON configuration persistence for the Modbus Tester.
Handles save, load, auto-save on exit, and auto-load on startup.
"""
import json
import os
from pathlib import Path
from dataclasses import dataclass, field, asdict


# Config lives next to the script so it's easy to find
CONFIG_DIR = Path(__file__).resolve().parent
AUTO_SAVE_PATH = CONFIG_DIR / "last_session.json"


@dataclass
class RegisterEntry:
    label: str = ""
    reg_type: str = "holding"
    offset: int = 0
    data_type: str = "UINT16"
    enabled: bool = True


@dataclass
class ConnectionConfig:
    host: str = "192.168.1.1"
    port: int = 502
    unit_id: int = 1
    byte_order: str = "big"
    scan_interval_ms: int = 500


@dataclass
class AppConfig:
    connection: ConnectionConfig = field(default_factory=ConnectionConfig)
    entries: list[RegisterEntry] = field(default_factory=list)


def ensure_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def save_config(config: AppConfig, path: str | Path | None = None) -> Path:
    """Save config to JSON. Returns the path written."""
    ensure_config_dir()
    target = Path(path) if path else AUTO_SAVE_PATH
    data = {
        "connection": asdict(config.connection),
        "entries": [asdict(e) for e in config.entries],
    }
    with open(target, "w") as f:
        json.dump(data, f, indent=2)
    return target


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load config from JSON. Returns default config if file missing."""
    target = Path(path) if path else AUTO_SAVE_PATH
    if not target.exists():
        return AppConfig()
    try:
        with open(target) as f:
            data = json.load(f)
        conn = ConnectionConfig(**data.get("connection", {}))
        entries = [RegisterEntry(**e) for e in data.get("entries", [])]
        return AppConfig(connection=conn, entries=entries)
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        print(f"[config] Failed to load {target}: {exc}")
        return AppConfig()
