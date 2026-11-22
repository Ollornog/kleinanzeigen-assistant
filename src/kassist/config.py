"""Settings from ~/.config/kassist/config.yaml (all optional) and secrets from secrets.env (0600)."""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(os.environ.get("KASSIST_CONFIG_DIR", Path.home() / ".config" / "kassist"))

DEFAULTS: dict[str, Any] = {
    "source_dir": str(Path.home() / "Kleinanzeigen"),
    "data_dir": str(Path.home() / ".local" / "share" / "kassist"),
    "cdp_port": 9333,
    "chrome": "google-chrome",
    "listing_defaults": {           # same structure as listing.yaml; the item file wins
        "pricing": {"type": "NEGOTIABLE", "schedule": {"hold_days": 14, "steps": 3, "every_days": 7}},
        "shipping": {"enabled": False},
        "republish": {"enabled": True, "after_days": 14, "max": 2},
    },
    "sellers": {},                  # name -> {name, location: {zipcode, city, street}}
    "text_blocks": {},              # name -> standard text appended to the description
    # condition of an item -> sentence placed under the description (editable in the settings)
    "condition_texts": {
        "new_sealed": "Zustand: neu und originalverpackt (OVP).",
        "new": "Zustand: neu und unbenutzt, ohne Originalverpackung.",
        "like_new": "Zustand: gebraucht, wie neu – kaum benutzt, keine Gebrauchsspuren.",
        "very_good": "Zustand: gebraucht, sehr gut – minimale Gebrauchsspuren.",
        "good": "Zustand: gebraucht, gut – normale Gebrauchsspuren, alles funktioniert.",
        "used": "Zustand: gebraucht, deutliche Gebrauchsspuren, voll funktionsfähig.",
        "defect": "Zustand: defekt – für Bastler oder als Ersatzteilspender.",
    },
    "bot_ad_defaults": {},          # passed to the bot's ad_defaults
    "limits": {"max_publish": 3, "max_actions": 8, "pause_seconds": [45, 150]},
    "inbox": {"poll_minutes": 15},
    # Claude Code without tools, MCP servers, hooks, CLAUDE.md or saved sessions: buyer messages are
    # untrusted text and must not be able to make the model act on this computer.
    "llm": {"provider": "command", "fallback": True, "timeout": 180, "model": "",
            "command": ["claude", "-p", "--model", "sonnet", "--tools", "", "--strict-mcp-config",
                        "--no-session-persistence", "--setting-sources", ""]},
    "panel": {"host": "127.0.0.1", "port": 8765},
    "browser": {"start_minimized": True, "show_during_actions": True},
    "auto": {"enabled": True, "on_start": True, "sync_minutes": 1440, "inbox_minutes": 10},   # sync daily
    "replies": {"mode": "draft", "max_auto": 3},   # off | draft | auto
}


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        out[key] = _merge(base[key], value) if isinstance(value, dict) and isinstance(base.get(key), dict) else value
    return out


@dataclass
class Settings:
    raw: dict[str, Any] = field(default_factory=lambda: dict(DEFAULTS))

    @property
    def source_dir(self) -> Path:
        return Path(self.raw["source_dir"]).expanduser()

    @property
    def data_dir(self) -> Path:
        return Path(self.raw["data_dir"]).expanduser()

    @property
    def db_path(self) -> Path:
        return self.data_dir / "state.sqlite"

    @property
    def chrome_profile(self) -> Path:
        return self.data_dir / "chrome-profile"

    @property
    def bot_dir(self) -> Path:
        return self.data_dir / "bot"

    @property
    def cdp_port(self) -> int:
        return int(self.raw["cdp_port"])

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def catalog(self) -> "Catalog":
        from .catalog import Catalog
        return Catalog(defaults=self.raw["listing_defaults"], sellers=self.raw["sellers"] or {},
                       text_blocks=self.raw["text_blocks"] or {},
                       condition_texts=self.raw.get("condition_texts") or {})


def load(path: Path | None = None) -> Settings:
    path = path or CONFIG_DIR / "config.yaml"
    over = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return Settings(_merge(DEFAULTS, over or {}))


def save(raw: dict[str, Any], path: Path | None = None) -> Path:
    """Write the settings the GUI manages. Only keys that differ from DEFAULTS are kept."""
    path = path or CONFIG_DIR / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)

    def diff(value: Any, base: Any) -> Any:
        if isinstance(value, dict) and isinstance(base, dict):
            out = {k: d for k, v in value.items() if (d := diff(v, base.get(k, _NONE))) is not _NONE}
            return out if out else _NONE
        return _NONE if value == base else value

    data = diff(raw, DEFAULTS)
    header = "# kassist settings — written by the kassist GUI; comments here are not kept.\n"
    path.write_text(header + yaml.safe_dump({} if data is _NONE else data, allow_unicode=True, sort_keys=False,
                                            width=110), encoding="utf-8")
    return path


_NONE = object()


def set_secret(name: str, value: str, path: Path | None = None) -> None:
    """Add or replace one KEY=value line in secrets.env (mode 0600); other lines stay."""
    path = path or CONFIG_DIR / "secrets.env"
    if not name.replace("_", "").isalnum() or "\n" in value or not value.strip():
        raise ValueError("invalid secret")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    lines = [ln for ln in lines if not ln.split("=", 1)[0].strip() == name] + [f"{name}={value.strip()}"]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.chmod(path, 0o600)


def load_secrets(path: Path | None = None) -> dict[str, str]:
    """KEY=VALUE lines. Refuses a file that others can read."""
    path = path or CONFIG_DIR / "secrets.env"
    if not path.is_file():
        return {}
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PermissionError(f"{path} must be mode 0600")
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out
