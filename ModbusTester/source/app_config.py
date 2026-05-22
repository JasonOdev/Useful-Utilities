"""
JSON configuration persistence for the Modbus Tester.
Handles save, load, auto-save on exit, and auto-load on startup.
"""
import json
import os
from pathlib import Path
from dataclasses import dataclass, field, asdict


import sys

# When frozen by PyInstaller, __file__ points inside the temp extraction dir.
# Use the actual executable's directory so config survives across runs.
if getattr(sys, "frozen", False):
    CONFIG_DIR = Path(sys.executable).resolve().parent
else:
    CONFIG_DIR = Path(__file__).resolve().parent
AUTO_SAVE_PATH = CONFIG_DIR / "last_session.json"


@dataclass
class RegisterEntry:
    label: str = ""
    reg_type: str = "holding"
    offset: int = 0
    data_type: str = "UINT16"
    enabled: bool = True
    reg_count: int = 1   # used only when data_type == "ASCII"


@dataclass
class ConnectionConfig:
    host: str = "192.168.1.1"
    port: int = 502
    unit_id: int = 1
    byte_order: str = "big"
    scan_interval_ms: int = 500
    # "offset" = zero-based wire offset; "register" = 1-based register number
    offset_mode: str = "register"

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

        # Filter to only known fields so old/new JSON doesn't break dataclass init
        import dataclasses
        conn_fields = {f.name for f in dataclasses.fields(ConnectionConfig)}
        entry_fields = {f.name for f in dataclasses.fields(RegisterEntry)}

        conn_data = {k: v for k, v in data.get("connection", {}).items() if k in conn_fields}
        conn = ConnectionConfig(**conn_data)

        entries = []
        for e in data.get("entries", []):
            filtered = {k: v for k, v in e.items() if k in entry_fields}
            entries.append(RegisterEntry(**filtered))

        return AppConfig(connection=conn, entries=entries)
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        print(f"[config] Failed to load {target}: {exc}")
        return AppConfig()
