---
id: T-1
type: Task
title: Live view of the Kleinanzeigen browser inside the GUI
status: verworfen
milestone: M-2
tags: [gui, browser]
created: 2026-11-22
---

# T-1 - Live view of the browser in the GUI

**Idea:** mirror the Kleinanzeigen browser tab into the GUI (screencast over the debugging
connection), so login, captcha or an SMS check could be handled without switching windows.

**Not needed:** the browser itself comes to the front exactly when it needs a person (login,
captcha, SMS/device check, errors) and "Show browser" in the navigation brings it up on demand.
A mirror would add a second, more fragile way to the same window and more traffic on the debugging
connection. Reopen only if kassist runs on a machine without a visible desktop.
