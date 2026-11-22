---
id: T-2
type: Task
title: Check the messagebox field names against real conversations
status: offen
milestone: M-2
tags: [inbox, portal]
created: 2026-11-22
---

# T-2 - Messagebox field names

`src/kassist/inbox.py` normalizes the JSON of the Kleinanzeigen messagebox (conversation list and
single conversation) into rows. The field names come from the web app, which is not a contract, and
were not yet checked against a broad set of real conversations (buyer and seller role, offers,
attachments, deleted ads, system messages).

**To do:** collect anonymized samples of each kind from a real account (never commit real names or
message text), compare them with the mapping, extend the tolerant parsing and the tests with
neutral fixtures.

**Done when:** every kind above maps to a row without missing partner, ad or last message, and a
test covers it.
