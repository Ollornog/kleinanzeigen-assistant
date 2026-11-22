"""Language model access: a local command (prompt on stdin) or the Mistral API.

Both providers can be set up at once; `provider` picks the one asked first, `fallback` lets the other
answer when the first fails (not installed, quota used up, network down).

Settings (`llm` in config.yaml):
  provider: command | mistral                  # asked first
  fallback: true                               # then the other one, if it is set up
  command:  [claude, -p, --model, sonnet, …]   # provider command (prompt on stdin, answer on stdout)
  model:    mistral-medium-2604                # provider mistral, a pinned model name
  timeout:  180
The Mistral key lives in secrets.env as MISTRAL_API_KEY (never in config.yaml).
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import urllib.error
import urllib.request
from typing import Any, Sequence

from .config import Settings, load_secrets

MISTRAL_URL = "https://api.mistral.ai/v1"
MISTRAL_KEY = "MISTRAL_API_KEY"
PROVIDERS = ("command", "mistral")
# Where user-installed programs live when kassist starts from the desktop menu, whose PATH lacks the
# shell's additions (nvm, ~/.local/bin).
EXTRA_BIN = ("~/.local/bin", "~/.claude/local", "~/.nvm/versions/node/*/bin", "/usr/local/bin")


class LLMError(RuntimeError):
    pass


def find_program(name: str) -> str | None:
    """The program on PATH, or in the usual user install places (newest nvm node first)."""
    if os.sep in name:
        return name if os.access(name, os.X_OK) else None
    found = shutil.which(name)
    if found:
        return found
    for pattern in EXTRA_BIN:
        for folder in sorted(glob.glob(os.path.expanduser(pattern)), reverse=True):
            path = Path(folder) / name
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
    return None


def run_command(command: Sequence[str], prompt: str, timeout: int = 180) -> str:
    if not command:
        raise LLMError("no command set (Settings → AI)")
    program = find_program(command[0])
    if not program:
        raise LLMError(f"program not found: {command[0]}")
    env = dict(os.environ)
    # a node script (claude) needs its node next to it on PATH
    env["PATH"] = os.pathsep.join([str(Path(program).parent), env.get("PATH", "")])
    try:
        proc = subprocess.run([program, *command[1:]], input=prompt, capture_output=True, text=True,
                              timeout=timeout, env=env, cwd=tempfile.gettempdir())
    except subprocess.TimeoutExpired as exc:
        raise LLMError(f"{command[0]}: no answer within {timeout} s") from exc
    except OSError as exc:
        raise LLMError(f"{command[0]}: {exc}") from exc
    if proc.returncode != 0:
        raise LLMError(f"LLM command failed ({proc.returncode}): {(proc.stderr or proc.stdout).strip()[:300]}")
    text = proc.stdout.strip()
    if not text:
        raise LLMError("LLM returned no text")
    return text


def _request(path: str, key: str, body: dict[str, Any] | None = None, timeout: int = 60) -> dict[str, Any]:
    req = urllib.request.Request(f"{MISTRAL_URL}{path}", method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {key}", "Accept": "application/json",
                                          **({"Content-Type": "application/json"} if body is not None else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise LLMError(f"Mistral {path}: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"Mistral not reachable: {exc.reason}") from exc


def mistral_key() -> str:
    key = load_secrets().get(MISTRAL_KEY, "")
    if not key:
        raise LLMError(f"{MISTRAL_KEY} missing (Settings → AI, or ~/.config/kassist/secrets.env)")
    return key


_MODELS: dict[str, tuple[float, list[str]]] = {}
MODELS_TTL = 3600


def mistral_models(key: str | None = None) -> list[str]:
    """Chat models the key may use, pinned names only (no '-latest' aliases), newest first by name.
    Kept for an hour per key (the settings page asks for it on every load)."""
    import hashlib
    import time
    key = key or mistral_key()
    tag = hashlib.sha256(key.encode()).hexdigest()[:16]
    hit = _MODELS.get(tag)
    if hit and time.monotonic() - hit[0] < MODELS_TTL:
        return hit[1]
    models = _fetch_models(key)
    _MODELS[tag] = (time.monotonic(), models)
    return models


def _fetch_models(key: str) -> list[str]:
    data = _request("/models", key)
    names = [m.get("id", "") for m in data.get("data", [])
             if (m.get("capabilities") or {}).get("completion_chat", True)]
    return sorted({n for n in names if n and not n.endswith("-latest")}, reverse=True)


def mistral_chat(prompt: str, model: str, key: str | None = None, json_mode: bool = False, timeout: int = 120) -> str:
    body: dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    data = _request("/chat/completions", key or mistral_key(), body, timeout)
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Mistral: unexpected answer {str(data)[:200]}") from exc
    if isinstance(text, list):      # content parts
        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
    if not str(text).strip():
        raise LLMError("Mistral returned no text")
    return str(text).strip()


def ready(settings: Settings, provider: str) -> bool:
    """Is this provider set up (program found, or Mistral key and model saved)?"""
    cfg = settings["llm"]
    if provider == "mistral":
        return bool(cfg.get("model")) and bool(load_secrets().get(MISTRAL_KEY))
    command = cfg.get("command") or []
    return bool(command) and find_program(command[0]) is not None


def ask(settings: Settings, provider: str, prompt: str, json_mode: bool = False) -> str:
    """One provider, no fallback (used by the test buttons)."""
    cfg = settings["llm"]
    timeout = int(cfg.get("timeout", 180))
    if provider == "mistral":
        model = cfg.get("model")
        if not model:
            raise LLMError("no Mistral model chosen (Settings → AI)")
        return mistral_chat(prompt, model, json_mode=json_mode, timeout=timeout)
    return run_command(cfg.get("command") or [], prompt, timeout)


def order(settings: Settings) -> list[str]:
    cfg = settings["llm"]
    first = cfg.get("provider", "command") if cfg.get("provider") in PROVIDERS else "command"
    other = next(p for p in PROVIDERS if p != first)
    return [first] + ([other] if cfg.get("fallback", True) and ready(settings, other) else [])


def complete(settings: Settings, prompt: str, json_mode: bool = False) -> str:
    errors = []
    for provider in order(settings):
        try:
            return ask(settings, provider, prompt, json_mode)
        except LLMError as exc:
            errors.append(f"{provider}: {exc}")
    raise LLMError(" · ".join(errors))
