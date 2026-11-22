---
id: ADR-6
type: Decision
title: Licence AGPL-3.0-or-later
status: erledigt
tags: [licence, repo]
created: 2026-11-22
---

# ADR-6 - Licence AGPL-3.0-or-later

## Context

The owner's public repositories are usually MIT. kassist, however, is built on kleinanzeigen-bot
(AGPL-3.0-or-later), imports it and ships a file copied from it (`tools/fix_nodriver.py`).

## Decision

**AGPL-3.0-or-later** for kassist, with the official licence text in `LICENSE`.

## Consequences

- Licence-compatible with the bot; no question whether kassist is a derivative work.
- Third-party parts keep their own licences (C22, Basecoat, Phosphor: MIT; Inter: OFL 1.1; Lucide:
  ISC), listed in `THIRD_PARTY_NOTICES.md`.
