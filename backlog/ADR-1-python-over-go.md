---
id: ADR-1
type: Decision
title: Python instead of Go
status: erledigt
tags: [architecture, language]
created: 2026-11-22
---

# ADR-1 - Python instead of Go

## Context

New projects of this owner prefer Go or Rust; the language is decided per case. kassist drives a
browser, talks to kleinanzeigen-bot and renders a small local GUI.

## Options

- **Go:** single binary, but the browser automation (CDP) and the bot would be a separate Python
  process anyway, glued over a CLI or files.
- **Python:** the same runtime as kleinanzeigen-bot and its browser library nodriver.

## Decision

**Python** (≥ 3.13), FastAPI + Jinja for the GUI, SQLite for state.

## Consequences

- One environment: the bot is installed into kassist's venv and run with the same interpreter
  (`python -m kleinanzeigen_bot`), and kassist uses the bot's browser library nodriver for its own
  portal access to the same browser.
- Packaging is a wheel plus a venv (uv), not a single binary. Raspberry Pi OS (Python 3.13) runs the
  same code.
