---
id: ADR-4
type: Decision
title: kleinanzeigen-bot does the form work
status: erledigt
tags: [architecture, dependency]
created: 2026-11-22
---

# ADR-4 - kleinanzeigen-bot does the form work

## Context

Publishing, updating, reserving and deleting an ad means driving long forms (categories, attributes,
photos, shipping) that change with the site. kleinanzeigen-bot maintains exactly that automation.

## Options

- Own form automation: full control, but a second copy of fragile work that breaks with every site
  change.
- **kleinanzeigen-bot** for the forms; kassist plans what to do and when.

## Decision

**kleinanzeigen-bot**, attached to kassist's browser and called with explicit ad ids. Its own republish
and price reduction are not used; kassist's planner decides. The dependency is pinned by commit, not
by the rolling tag, and raised only after reading the upstream diff (Dependabot ignores it).

## Consequences

- kassist is AGPL-3.0-or-later like the bot (ADR-6).
- `tools/fix_nodriver.py` carries the bot's nodriver post-install fix.
- Upstream changes need a deliberate pin raise and a test run against the real account.
