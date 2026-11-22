---
id: ADR-2
type: Decision
title: Visible, persistent Chrome instead of headless
status: erledigt
tags: [architecture, browser]
created: 2026-11-22
---

# ADR-2 - Visible, persistent Chrome instead of headless

## Context

Kleinanzeigen sits behind a bot manager (Akamai) that recognizes headless browsers and fresh,
cookie-less profiles, and answers with captchas, blocks or account checks. The site's terms forbid
automated access, so every signal that looks like a script raises the risk for the account.

## Options

- Headless Chrome per run: simple, but detected.
- Fresh visible browser per run: login and captcha on every run.
- **One persistent visible Chrome with its own profile**, kept running and reached through remote
  debugging on 127.0.0.1.

## Decision

**Persistent visible Chrome.** The person logs in by hand once; kassist starts it only when there is
work, minimized, and brings it to the front for login, captcha, SMS/device check and errors.

## Consequences

- No captcha solving; the login stays a human step.
- A desktop session is required; a server without a display is out of scope (see T-1).
- Anything that can reach the debugging port can drive the logged-in session (SECURITY.md).
