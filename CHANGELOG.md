# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- CI: kit 0.73.0 checks unused dependencies in `scripts/check.sh`; `kleinanzeigen-bot` (run as a CLI, never imported)
  is allowed with a reason in `.github/abhaengigkeiten.json`.

### Security

- Vendored C22 (`js/c22.js`): a `data-href` on an explorer row or merged cell opens only a target on the GUI's own
  origin; a `javascript:` target ran as script on a double click (CodeQL `js/xss-through-dom`). Only this change of
  C22 PR #160 is applied, noted in `ORIGIN`. Test `tests/test_c22_vendor.py` runs the guard in node.

## [0.1.0] - 2026-11-22

First public release.

### Added

- **Item folders and listing structure.** One folder per item with photos and `listing.yaml`: pricing
  (start, reduce_to, min_sale, schedule), availability window (publish not before, withdraw after),
  location, shipping, seller profiles, standard text blocks, republish, price check, category
  attributes, photo order and skip. `kassist new` writes the commented template; empty values fall back
  to `listing_defaults`. Seller and text block names keep their case and match regardless of case.
  Category is required before approval (picker with the bot's category list, hierarchy columns, search).
- **Import.** `*_inserat_*.md` drafts become a full commented `listing.yaml` (title shortened at a word
  boundary, sections that equal a text block map to `texts`). A folder with only a `.txt`/`.md` file is
  taken as it is (short first line as title) or read by the AI (title, text, prices, price type and
  shipping only where the text says so; prices not in the text are dropped in code; hints go into the
  file header).
- **Price schedule and planner.** Hold the start price, then equal steps (default 3) or a fixed/percent
  step (per-item switch) down to a floor; pure planner for publish, price update, republish after
  expiry and caps per run; "run now" per item; price plan preview with dates.
- **State and sync.** SQLite state, sync with the account's ad list, kleinanzeigen-bot driven with
  explicit ad ids and pinned to commit 2687db6 instead of the rolling tag `latest`; bot errors reported
  from its ERROR lines. Sync on start and daily (setting in hours), messages every 10 minutes while
  ads are online; with nothing online and nothing due there is no sync, no browser and no traffic.
- **Staged changes.** Changes to an online ad (title, text, category, photos, shipping, seller …) are
  shown as pending and only sent after approval; kassist keeps a snapshot of what is online. Key data
  kept as AI context never stages anything.
- **Browser and portal access.** Persistent visible Chrome with its own profile and remote debugging;
  starts only when needed, minimized, and comes to the front for login, captcha, SMS/device check,
  errors and (setting) while ads change. Own ads, messages and price searches run as the page's own
  `fetch()` inside an open Kleinanzeigen tab; connections are closed per tab.
- **Inbox and replies.** Message polling, desktop notifications, reply modes off / draft / on. Drafts
  marked [SCAM?] or [OWNER] (sale or final price agreed, pickup time, address, unanswerable question)
  always wait for the owner; at most N automatic replies per conversation (default 3). The lowest price
  stays secret; the counter-offer is computed (two thirds of the way from the lowest to the asking
  price); the AI never proposes times or places.
- **Price overview.** `kassist prices` and the "Market" column: asking prices of similar ads from a
  Kleinanzeigen search, parsed by content, filtered (search words and model numbers, exclude words,
  own ads, IQR outliers), median and quartiles per item; the last 3 checks are kept in a `market` block
  at the end of `listing.yaml`. Also for listings not ready for approval.
- **AI market check.** `--ai` compares condition, contents and key specs of up to 60 offers with the
  item, treats other brands as comparable with a price adjustment, drops outliers among the adjusted
  prices and suggests start / reduce_to / min_sale.
- **AI providers.** Claude Code (local program, default) and Mistral (API, key via a write-only field,
  pinned model names from the account's list) set up side by side; "Active AI" picks the one asked
  first, the other answers when it fails; test button and status per provider. AI buttons in the editor
  improve the text, suggest titles (≤ 65 characters) and categories (known list only).
- **Key data per item.** Condition (seven levels, editable wording), own purchase price, market price
  new, Kleinanzeigen median and free rows; ticked rows go under the item text, the others are AI context.
- **GUI.** `kassist gui` as an app window (en/de): items list (skeleton first, live filter), full
  listing editor (fixed head with the same actions always in the same places, icons, help text under
  every field), photos (order by dragging, cover, leave out, crop and rotate as a new file, camera
  capture, thumbnails cached on disk), settings in tabs (general, profiles, templates with sortable
  standard texts, shipping, AI), messages with drafts, log, "New" and "Sync" buttons, readable success
  banners, delete to `.papierkorb`. Look from the vendored C22 design system, icons from Lucide.
  Caching of the folder scan, model list and static files.
- **Background mode.** Tray icon (Cinnamon XApp or Ayatana AppIndicator) with Open, Sync now, Show
  browser, Quit; closing the window keeps kassist running; optional start at login (XDG autostart);
  `kassist install-desktop` adds a menu entry.
- **Project.** Published under AGPL-3.0-or-later with English and German README, contributing guide,
  security policy, code of conduct, third-party notices, backlog with decisions (ADRs), CI on GitHub
  (tests, hygiene, packaging smoke test) and a release workflow with checksums.

### Security

- The GUI server listens on 127.0.0.1 only; a Host/Origin guard blocks other websites from driving it.
- Secrets live in `~/.config/kassist/secrets.env` (0600), never in `config.yaml`;
  `examples/config.yaml` keeps the locked-down Claude command (a test guards it).
- Claude Code runs without tools, MCP servers, hooks, CLAUDE.md or saved sessions, outside the project
  folder, because buyer messages are untrusted text.
- Tests run with a throw-away config directory and can never start Chrome, touch a real browser
  window or start an AI program.

[Unreleased]: https://github.com/Ollornog/kleinanzeigen-assistant/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Ollornog/kleinanzeigen-assistant/releases/tag/v0.1.0
