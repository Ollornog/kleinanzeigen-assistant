---
id: ADR-7
type: Decision
title: Fresh public history instead of the development history
status: erledigt
tags: [repo, privacy]
created: 2026-11-22
---

# ADR-7 - Fresh public history

## Context

kassist was developed privately against one real account. Tests and docs of that history contained
personal data (a real location and real first names as sample data).

## Options

- Rewrite the old history (filter every commit): error-prone, and one missed blob is public forever.
- **Publish one new initial commit** with the cleaned tree and keep the old history private.

## Decision

**One new initial commit.** The private history is kept as a git bundle outside the repository and
is never pushed. Sample data is neutral (`10115 Berlin`, `20095 Hamburg`, sellers `alex`/`sam`).

## Consequences

- `git log` starts at v0.1.0; earlier changes are summarized in `CHANGELOG.md`.
- The hygiene test (`tests/test_repo.py`) and the contributing rules keep personal data out from now on.
