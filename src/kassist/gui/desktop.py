"""Linux desktop integration: menu entry, start at login (XDG autostart) and the tray icon helper."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
ICON = HERE / "static" / "icon.svg"
APPLICATIONS = Path.home() / ".local" / "share" / "applications"
AUTOSTART_DIR = Path.home() / ".config" / "autostart"
# the tray needs GTK bindings, which the system Python has (python3-gi), the venv does not
TRAY_PYTHONS = ("/usr/bin/python3", "python3")

DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=Kleinanzeigen-Assistent
Comment=Manage Kleinanzeigen listings from folders
Exec={exe} gui{args}
Icon={icon}
Terminal=false
Categories=Office;Network;
{extra}"""


def _exe() -> Path:
    argv0 = Path(sys.argv[0])
    return argv0.resolve() if argv0.name == "kassist" else Path(sys.executable).with_name("kassist")


def _entry(args: str = "", extra: str = "") -> str:
    return DESKTOP_ENTRY.format(exe=_exe(), args=args, icon=ICON, extra=extra)


def install_desktop_entry() -> Path:
    """Menu entry in ~/.local/share/applications (Mint, Raspberry Pi OS)."""
    if not sys.platform.startswith("linux"):
        raise RuntimeError("desktop entry is only for Linux; on Windows start `kassist gui`")
    target = APPLICATIONS / "kassist.desktop"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_entry(), encoding="utf-8")
    return target


def autostart_path() -> Path:
    return AUTOSTART_DIR / "kassist.desktop"


def autostart_enabled() -> bool:
    return autostart_path().is_file()


def set_autostart(enabled: bool) -> None:
    """Start in the background (tray only) when the user logs in. The delay lets the panel come up
    first, otherwise the tray icon can get lost."""
    path = autostart_path()
    if not enabled:
        path.unlink(missing_ok=True)
        return
    if not sys.platform.startswith("linux"):
        raise RuntimeError("start at login is only available on Linux")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_entry(" --background", "X-GNOME-Autostart-enabled=true\nX-GNOME-Autostart-Delay=15\n"),
                    encoding="utf-8")


def tray_python() -> str | None:
    for candidate in TRAY_PYTHONS:
        exe = shutil.which(candidate)
        if exe and subprocess.run([exe, "-c", "import gi"], capture_output=True, timeout=10).returncode == 0:
            return exe
    return None


def start_tray(base_url: str, labels: dict[str, str]) -> subprocess.Popen | None:
    """The tray icon as its own process; None when this desktop has no GTK bindings for it."""
    if not sys.platform.startswith("linux"):
        return None
    exe = tray_python()
    if not exe:
        return None
    return subprocess.Popen([exe, str(HERE.parent / "tray.py"), base_url, str(ICON), json.dumps(labels)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
