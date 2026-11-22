"""Tests never touch a real browser window, never start Chrome and never send desktop notifications.

A kassist browser may be running on the developer's machine (same CDP port); without this guard a
test could show or minimize it.
"""
import os

import pytest

from kassist import browser, config, engine, llm
from kassist.gui import desktop

SAFE_PROGRAMS = {"cat", "false", "true", "sh"}

# test_repo.py is the repo hygiene script from repokit (stdlib only, exits on its own);
# scripts/check.sh runs it directly, pytest must not import it as a test module.
collect_ignore = ["test_repo.py"]


@pytest.fixture(autouse=True)
def no_real_config_dir(tmp_path_factory, monkeypatch):
    """Secrets and config of the developer's machine stay out of reach (no key, no real secrets.env)."""
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path_factory.mktemp("kassist-config"))
    monkeypatch.setattr(desktop, "AUTOSTART_DIR", tmp_path_factory.mktemp("autostart"))
    monkeypatch.setattr(desktop, "APPLICATIONS", tmp_path_factory.mktemp("applications"))
    llm._MODELS.clear()
    # never start a real AI program (claude) from a test: only harmless helpers are "installed"
    real_find = llm.find_program
    def guarded(name):
        return real_find(name) if os.path.basename(name) in SAFE_PROGRAMS else None
    guarded.real = real_find
    monkeypatch.setattr(llm, "find_program", guarded)


@pytest.fixture(autouse=True)
def no_real_browser_or_notifications(monkeypatch):
    calls = []
    monkeypatch.setattr(browser, "show", lambda settings, url=None: calls.append(("show", url)))
    monkeypatch.setattr(browser, "hide", lambda settings: calls.append(("hide", None)))

    def refuse_start(*a, **k):
        raise AssertionError("a test tried to start Chrome")
    monkeypatch.setattr(browser, "start", refuse_start)
    monkeypatch.setattr(browser, "ensure", lambda settings: calls.append(("ensure", None)))
    monkeypatch.setattr(browser, "running", lambda port: False)
    monkeypatch.setattr(engine, "notify", lambda *a, **k: None)
    return calls
