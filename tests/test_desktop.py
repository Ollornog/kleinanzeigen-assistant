import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from kassist import engine
from kassist.config import DEFAULTS, Settings, _merge
from kassist.gui import create_app, desktop, launcher
from kassist.gui.scheduler import Scheduler

DE = {"accept-language": "de"}


@pytest.fixture
def client(tmp_path):
    settings = Settings(_merge(DEFAULTS, {"source_dir": str(tmp_path), "data_dir": str(tmp_path / "data")}))
    app = create_app(settings, scheduler=Scheduler(settings))
    return TestClient(app), app, settings


def test_ping_window_and_quit_routes(client):
    c, app, _ = client
    assert c.get("/ping").json()["app"] == "kassist"
    assert c.post("/window").status_code == 409 and c.post("/quit").status_code == 409   # `kassist panel`
    opened, stopped = [], []
    app.state.open_window = lambda: opened.append(1)
    app.state.quit = lambda: stopped.append(1)
    assert c.post("/window").json() == {"ok": True} and opened == [1]
    r = c.post("/quit", headers={"accept": "text/html", **DE})
    assert stopped == [1] and "kassist ist beendet" in r.text
    assert 'action="/quit"' in c.get("/", headers=DE).text and "kassist beenden" in c.get("/", headers=DE).text


def test_quit_refused_from_a_foreign_page(client):
    c, app, _ = client
    stopped = []
    app.state.quit = lambda: stopped.append(1)
    assert c.post("/quit", headers={"origin": "https://evil.example"}).status_code == 403 and stopped == []


def test_autostart_switch_writes_and_removes_the_entry(client):
    c, _, settings = client
    base = {"source_dir": settings["source_dir"], "seller.key": ["alex"], "seller.name": ["Alex"],
            "seller.zipcode": ["10115"], "seller.city": ["Berlin"], "block.key": ["legal"], "block.text": ["x"],
            "d.seller": "alex", "d.texts": ["legal"], "d.type": "NEGOTIABLE", "l.max_publish": "3",
            "l.max_actions": "8", "l.pause_min": "45", "l.pause_max": "150", "llm.command": "cat"}
    c.post("/settings", data=dict(base, **{"a.login_start": "on"}))
    entry = desktop.autostart_path().read_text()
    assert " gui --background" in entry and "X-GNOME-Autostart-Delay=" in entry
    assert 'name="a.login_start" checked' in c.get("/settings").text
    c.post("/settings", data=base)
    assert not desktop.autostart_path().exists()


def test_menu_entry_opens_the_window(tmp_path):
    text = desktop.install_desktop_entry().read_text()
    assert "Exec=" in text and text.split("Exec=")[1].split("\n")[0].endswith(" gui")


def test_tray_labels_in_both_languages(monkeypatch):
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    assert launcher.tray_labels()["quit"] == "Beenden"
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert launcher.tray_labels()["open"] == "Open"


def test_second_start_only_recognises_kassist(tmp_path):
    answers = {"/ping": {"app": "kassist"}}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(answers.get(self.path, {"app": "other"})).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/"
    try:
        assert launcher.already_running(url)
        answers["/ping"] = {"app": "something-else"}
        assert not launcher.already_running(url)
    finally:
        srv.shutdown()
    assert not launcher.already_running(url)                          # nothing listening


def test_second_start_opens_a_window_on_the_running_one(monkeypatch, tmp_path):
    settings = Settings(_merge(DEFAULTS, {"source_dir": str(tmp_path), "data_dir": str(tmp_path / "data")}))
    monkeypatch.setattr(launcher, "already_running", lambda url: True)
    opened = []
    monkeypatch.setattr(launcher, "window_opener", lambda s, url: lambda: opened.append(url))
    monkeypatch.setattr(launcher, "Scheduler", lambda s: pytest.fail("a second server was started"))
    launcher.run(settings, window=True)
    launcher.run(settings, window=True, background=True)              # autostart while running: nothing
    assert opened == ["http://127.0.0.1:8765/"]


def test_portal_starts_chrome_on_demand(tmp_path, no_real_browser_or_notifications):
    settings = Settings(_merge(DEFAULTS, {"source_dir": str(tmp_path), "data_dir": str(tmp_path / "data")}))
    engine._portal(settings, require_login=False)
    assert no_real_browser_or_notifications == [("ensure", None)]


def test_tray_helper_needs_nothing_from_kassist():
    import ast
    tree = ast.parse((desktop.HERE.parent / "tray.py").read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
        {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    levels = {n.level for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(m and m.startswith("kassist") for m in mods) and levels == {0}   # runs with the system Python
