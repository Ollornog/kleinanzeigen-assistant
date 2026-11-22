---
id: ADR-5
type: Decision
title: "AI: Claude Code locked down, Mistral as API, one falls back to the other"
status: erledigt
tags: [architecture, ai, security]
created: 2026-11-22
---

# ADR-5 - AI providers

## Context

Reply drafts, text help and the market check need a language model. Buyer messages are untrusted
text and may try to steer it (prompt injection).

## Decision

- **Claude Code** as a local program (`claude -p`) with the owner's subscription, run **without tools,
  MCP servers, hooks, CLAUDE.md or saved sessions**, outside the project folder: the model can only
  answer with text.
- **Mistral** through its chat API (key in `secrets.env`, pinned model names only).
- Both can be set up side by side; one is active, the other answers when the first fails.
- The AI never decides alone where it matters: the lowest price stays out of the reply, counter-offers
  are computed in code, drafts marked [SCAM?] or [OWNER] always wait for the person, automatic replies
  are limited per conversation.

## Consequences

- No provider lock-in, and a failure of one provider does not stop the inbox.
- `examples/config.yaml` must never override the locked-down Claude command; a test guards it.
