---
id: ADR-3
type: Decision
title: Portal data through the page's own fetch()
status: erledigt
tags: [architecture, portal]
created: 2026-11-22
---

# ADR-3 - Portal data through the page's own fetch()

## Context

kassist needs the account's own ads, the messagebox and search results. Opening JSON endpoints in
tabs showed raw JSON to the person and looked unlike any normal use; a separate HTTP client would
need the session cookies outside the browser and a different fingerprint.

## Decision

**Run `fetch()` inside an open Kleinanzeigen tab** (via CDP `Runtime.evaluate`), with the page's own
cookies, short-lived access token and headers. Without such a tab, a background tab on the start page
is opened and closed again. The tab is never navigated.

## Consequences

- Requests come from the real page context; no cookies leave the browser.
- The endpoints and field names are the web app's, not a contract: parsing is tolerant and covered by
  tests with neutral fixtures (see T-2).
