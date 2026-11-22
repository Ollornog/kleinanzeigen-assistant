"""Drive kleinanzeigen-bot: one ad file per listing in the bot workspace, explicit ids per run.

The bot attaches to the running kassist browser (remote debugging), so it never starts its
own browser and never needs a fresh login while the session is alive. Its own republish
logic is switched off (huge interval, explicit --ads selection only).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .catalog import Listing
from .config import Settings, load_secrets

USER_ENV = "KASSIST_KA_USER"
PASSWORD_ENV = "KASSIST_KA_PASSWORD"


def slug(key: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", key.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"))
    return s.strip("-") or "item"


def bot_config(settings: Settings) -> dict[str, Any]:
    port = settings.cdp_port
    return {
        "ad_files": ["./ads/*.yaml"],
        "ad_defaults": {"republication_interval": 100000, **settings["bot_ad_defaults"]},
        "publishing": {"delete_old_ads": "AFTER_PUBLISH", "delete_old_ads_by_title": False},
        "browser": {"arguments": ["--remote-debugging-host=127.0.0.1", f"--remote-debugging-port={port}"]},
        "login": {"username": "${%s}" % USER_ENV, "password": "${%s}" % PASSWORD_ENV},
        "captcha": {"auto_restart": False},
        "update_check": {"enabled": False},
    }


def ad_file(settings: Settings, key: str) -> Path:
    return settings.bot_dir / "ads" / f"{slug(key)}.yaml"


def read_ad(settings: Settings, key: str) -> dict[str, Any]:
    path = ad_file(settings, key)
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.is_file() else {}


def write_ad(settings: Settings, listing: Listing, price: int, active: bool) -> Path:
    """Write the bot ad file, keeping fields the bot wrote back (id, created_on, content_hash …)."""
    path = ad_file(settings, listing.key)
    path.parent.mkdir(parents=True, exist_ok=True)
    ad = read_ad(settings, listing.key)
    if listing.attributes:
        ad["special_attributes"] = {str(k): str(v) for k, v in listing.attributes.items()}
    if listing.contact:
        ad["contact"] = dict(listing.contact)
    ad.update({
        "active": active,
        "type": "OFFER",
        "title": listing.title,
        "description": listing.description,
        "price": int(price),
        "price_type": listing.price_type,
        "shipping_type": listing.shipping_type,
        "images": [str(p) for p in listing.images],
    })
    if listing.category:
        ad["category"] = listing.category
    if listing.shipping and listing.shipping_cost is not None:
        ad["shipping_costs"] = listing.shipping_cost
    else:
        ad.pop("shipping_costs", None)
    path.write_text(yaml.safe_dump(ad, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8")
    return path


def set_active_only(settings: Settings, keys: set[str]) -> None:
    """`publish --ads=new` takes every active ad without id; keep all others inactive for this run."""
    for path in (settings.bot_dir / "ads").glob("*.yaml"):
        ad = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        want = path.stem in {slug(k) for k in keys}
        if ad.get("active") != want:
            ad["active"] = want
            path.write_text(yaml.safe_dump(ad, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8")


@dataclass
class BotResult:
    ok: bool
    output: str

    @property
    def error(self) -> str:
        """The bot's ERROR lines (with their continuation lines), else the last lines of its output."""
        lines = self.output.splitlines()
        picked: list[str] = []
        for i, line in enumerate(lines):
            if "[ERROR]" in line or "[CRITICAL]" in line:
                picked.append(re.sub(r"^.*?\[(?:ERROR|CRITICAL)\]\s*", "", line).strip())
                picked.extend(nxt.strip() for nxt in lines[i + 1:i + 6] if nxt.startswith(("-", " ")) and nxt.strip())
        return "\n".join(dict.fromkeys(picked)) if picked else "\n".join(lines[-8:])


def categories() -> dict[str, str]:
    """Category names the bot knows (name -> id), for pickers."""
    from importlib.resources import files
    try:
        data = yaml.safe_load(files("kleinanzeigen_bot").joinpath("resources/categories.yaml").read_text(encoding="utf-8"))
    except (FileNotFoundError, ModuleNotFoundError):
        return {}
    return {str(k): str(v) for k, v in (data or {}).items()}


def prepare_workspace(settings: Settings) -> Path:
    settings.bot_dir.mkdir(parents=True, exist_ok=True)
    cfg = settings.bot_dir / "config.yaml"
    cfg.write_text(yaml.safe_dump(bot_config(settings), sort_keys=False), encoding="utf-8")
    return cfg


def run(settings: Settings, command: str, ads: str | None = None, timeout: int = 900) -> BotResult:
    cfg = prepare_workspace(settings)
    secrets = load_secrets()
    env = dict(os.environ)
    env[USER_ENV] = secrets.get(USER_ENV, "")
    env[PASSWORD_ENV] = secrets.get(PASSWORD_ENV, "")
    if not env[USER_ENV] or not env[PASSWORD_ENV]:
        return BotResult(False, f"{USER_ENV}/{PASSWORD_ENV} missing in ~/.config/kassist/secrets.env")
    log = settings.bot_dir / "bot.log"
    args = [sys.executable, "-m", "kleinanzeigen_bot", command, f"--config={cfg}", "--workspace-mode=portable",
            f"--logfile={log}", "--lang=en"]
    if ads:
        args.append(f"--ads={ads}")
    start = log.stat().st_size if log.is_file() else 0
    try:
        proc = subprocess.run(args, cwd=settings.bot_dir, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return BotResult(False, f"timeout after {timeout}s\n{exc.stdout or ''}")
    out = (proc.stdout or "") + (proc.stderr or "")
    if log.is_file():   # the bot writes some errors only to its log file
        with log.open(encoding="utf-8", errors="replace") as fh:
            fh.seek(start if log.stat().st_size >= start else 0)
            out += "\n" + fh.read()
    return BotResult(proc.returncode == 0, out[-12000:])
