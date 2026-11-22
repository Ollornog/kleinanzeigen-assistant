# Security Policy

<b>English</b> · <a href="i18n/SECURITY.de.md">Deutsch</a>
<br /><br />

## Reporting a vulnerability

Please report privately through GitHub's
[private vulnerability reporting](https://github.com/Ollornog/kleinanzeigen-assistant/security/advisories/new)
or by mail to kleinanzeigen-assistant-github@ollornog.de, not in a public issue. You get a first reply
within 14 days. Fixes go into a new release; the advisory is published once a fixed version exists.

## Supported versions

Only the latest release gets fixes.

## Design decisions worth knowing

- **Local only.** The GUI server listens on 127.0.0.1; a Host/Origin guard rejects requests that other
  websites in the same browser could send to it.
- **Secrets stay out of the config.** The Kleinanzeigen login and the Mistral key live in
  `~/.config/kassist/secrets.env`, mode 0600. The GUI writes the key but never shows it again.
- **Buyer messages are untrusted text.** The AI only drafts text: Claude Code runs as `claude -p` without
  tools, MCP servers, hooks or CLAUDE.md, Mistral is a plain chat completion. Automatic replies are off
  unless you switch them on, and limited per conversation.
- **The browser profile is yours.** kassist drives a visible Chrome with its own profile through remote
  debugging on 127.0.0.1. Anything that can reach that port on your machine can drive the logged-in
  session; do not expose it.

## Not in scope

The Kleinanzeigen platform itself, the AI providers, kleinanzeigen-bot (report there), and anything a
local user with access to your account and files can do by design.

<br /><br />
<p align="right"><img src="docs/kassist.png" alt="kassist" width="60" height="60"></p>
