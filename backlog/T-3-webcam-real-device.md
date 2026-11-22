---
id: T-3
type: Task
title: Test camera capture with a real webcam
status: offen
milestone: M-2
tags: [gui, photos]
created: 2026-11-22
---

# T-3 - Camera capture with a real device

The camera tile in the item editor (`getUserMedia`, device choice, preview, keep/again, saved as
`cam-<date>-<time>.jpg`) was only tested with a fake video device.

**To do:** try it with at least one real USB webcam and a laptop camera: device list and choice,
resolution request (ideal 3840 wide), orientation, file size, behaviour when the device is busy or
permission is denied.

**Done when:** a real photo lands in the item folder in usable quality and the error cases show a
readable message.
