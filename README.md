<p align="center"><img src="docs/kassist.png" alt="kassist" width="250" height="250"></p>

<h1 align="center">kleinanzeigen-assistant (kassist)</h1>

<p align="center"><b>English</b> · <a href="i18n/README.de.md">Deutsch</a></p>

<p align="right">
<a href="https://github.com/Ollornog/kleinanzeigen-assistant/actions/workflows/ci.yml"><img src="https://github.com/Ollornog/kleinanzeigen-assistant/actions/workflows/ci.yml/badge.svg" alt="tests"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL--3.0--or--later-informational.svg" alt="License: AGPL-3.0-or-later"></a>
<img src="https://img.shields.io/badge/python-3.13%2B-blue.svg" alt="Python 3.13+">
</p>

Local assistant for **one private Kleinanzeigen account** with a handful of items:

- one folder per item (photos + `listing.yaml`) → ad
- price schedule: hold the start price, then lower it weekly down to a floor, then leave it
- after expiry: optionally publish again (rarely, limited count, after a waiting period)
- inbox: poll messages, desktop notification, AI reply draft; replies off / as draft / automatic
- price overview: asking prices of similar ads from a Kleinanzeigen search, optional AI cross-check
- key data per item (condition, own purchase price, market price new, Kleinanzeigen median, own rows):
  ticked ones go into the ad text, the others are context for the AI replies
- a folder with only a text file: take it as it is, or let the AI fill in only what the text says
- AI: Claude Code (local, your subscription) and/or Mistral (API), one active, the other as fallback
- reserve / activate / sold / withdraw from the app (tray icon, starts at login) or the CLI

## Risk

Kleinanzeigen's terms (§ 5 no. 1) forbid automated access without written permission, and
deleting and re-posting an ad counts as a duplicate. This tool lowers the risk (visible browser,
manual login, few actions per run, pauses, no captcha solving) but does not remove it. Use it for
your own account only and at your own risk.

## How it works

```
 ┌───────────── kassist browser ─────────────┐
 │ visible Chrome, own profile, login by hand │◄── remote debugging on 127.0.0.1
 └───────────────────────────────────────────┘
        ▲ publish/update/reserve/delete           ▲ own ads list, inbox, replies
        │ (kleinanzeigen-bot attaches)            │ (page's own fetch() in a tab)
 kassist tick / panel ────────────────────────────┘
        │
 folders ─► listing.yaml ─► planner (pure) ─► actions ─► SQLite state
```

- [kleinanzeigen-bot](https://github.com/Second-Hand-Friends/kleinanzeigen-bot) (AGPL-3.0) does the
  form work. Its own republish and price reduction are not used.
- One browser lock: bot runs, sync and inbox polls never overlap.

## Setup

Python ≥ 3.13 (Linux Mint ships 3.12: uv installs a managed Python; Raspberry Pi OS trixie has 3.13).

```bash
uv venv --python 3.15 .venv && uv pip install --python .venv/bin/python -e ".[test]"
.venv/bin/python tools/fix_nodriver.py          # nodriver patch the bot expects
mkdir -p ~/.config/kassist && cp examples/config.yaml ~/.config/kassist/config.yaml
install -m 600 /dev/null ~/.config/kassist/secrets.env   # then add KASSIST_KA_USER / KASSIST_KA_PASSWORD
.venv/bin/kassist browser                       # log in once in the window that opens
```

## Usage

```bash
kassist status            # folders, state, next action
kassist new <folder>      # write the listing.yaml template into a folder
kassist import            # listing.yaml from existing *_inserat_*.md drafts (approved: false)
kassist tick --dry-run    # what would happen today
kassist tick              # sync with the account, then execute due actions
kassist inbox             # poll messages, notify, draft replies
kassist prices <folder>   # asking prices of similar ads (median, quartiles); --query "...", --ai
kassist reserve <folder>  # also: activate, sold, withdraw
kassist gui               # GUI as an app window (kassist panel: server only, http://127.0.0.1:8765)
kassist install-desktop   # Linux: menu entry "Kleinanzeigen-Assistent"
```

### listing.yaml

Every item has the same structure; `kassist new <folder>` writes the commented template
([src/kassist/listing_template.yaml](src/kassist/listing_template.yaml)). Empty values fall back to
`listing_defaults` in the config.

```yaml
approved: true
title: DJI Mini 2 Fly More Combo wie neu
description: |            # item text only
  ...
seller: alex            # profile from config "sellers": name and location shown in the ad
texts: [condition, payment, legal]   # standard text blocks from config, appended
pricing:
  start: 330              # start price
  reduce_to: 290          # weekly automatic reduction stops here
  min_sale: 270           # lowest price the reply drafts may accept
  type: NEGOTIABLE
  schedule: {hold_days: 14, steps: 3, every_days: 7}   # 3 equal reductions; or step: "5" / "10%"
availability: {from: 2026-12-01, until: 2027-03-31}   # publish not before / withdraw after
location: {zipcode: "20095", city: Hamburg}       # overrides the seller's location
shipping: {enabled: true, cost: 6.19}
republish: {enabled: true, after_days: 14, max: 2}
price_check: {query: DJI Mini 2 Fly More, exclude: [akku]}
category: Elektronik > PC-Zubehör & Software   # required before approval
photos: {order: [b.jpg, a.jpg], skip: [c.jpg]}    # first = cover
attributes: {condition_s: like_new}   # optional category fields
facts:                    # key data; show: true = line under the item text, false = AI context only
  condition: {value: like_new}                 # sentence from config "condition_texts"
  purchase_price: {value: 459, show: false}    # "Kaufpreis: 459 €"
  market_new: {value: 399, show: false}        # "Marktneupreis: 399 €"
  market_used: {value: 310, show: false}       # Kleinanzeigen median, button in the editor
  extra: [{label: Lieferumfang, value: "3 Akkus, Tasche", show: true}]
```

Config side (`~/.config/kassist/config.yaml`): `sellers` (name, location), `text_blocks`
(standard texts), `condition_texts` (wording per condition level), `listing_defaults` (same structure as
above), `llm`, `replies`, `auto`. See
[examples/config.yaml](examples/config.yaml).

The price overview reads one or two pages of a normal Kleinanzeigen search in the same browser and
summarizes **asking prices** (not sale prices): results with words like "defekt", "suche", "tausch"
and IQR outliers are dropped, your own ads are skipped. The last 3 checks are kept in a `market` block
at the end of listing.yaml (written by kassist; your own values above stay untouched). With `--ai` the
LLM compares condition and contents of each offer with your item, adjusts for extras or missing parts
and suggests start / reduce_to / min_sale.

## GUI

`kassist gui` starts kassist in the background with a tray icon and opens a local app window
(Chrome/Chromium in app mode with its own profile; without them the default browser). Closing the
window keeps kassist running; "Quit" in the tray or the menu ends it. `kassist gui --background` starts
with the tray icon only; the setting "start at login" writes an XDG autostart entry for that.

Pages: items (state, price, market), item editor (every field of the listing structure, key data,
photos with camera, AI help for text/titles/categories, actions, price check), settings (folder,
profiles, standard and condition texts, defaults, limits, AI), messages with reply drafts, log.

- Sync: on start and daily; messages every 10 minutes while ads are online. With nothing online and
  nothing due there is no sync, no browser and no traffic. "Sync now" always syncs.
- The Kleinanzeigen browser starts only when needed, runs minimized and comes up for login, captcha,
  SMS check, errors and (by default) while ads are changed. Headless is avoided on purpose: the site
  detects it.
- Changes to an online ad are staged: "upload changes" sends them; key data kept as AI context never
  triggers an upload.
- AI: Claude Code runs as `claude -p` without tools, MCP servers, hooks or CLAUDE.md (buyer messages are
  untrusted text); Mistral needs `MISTRAL_API_KEY` in `~/.config/kassist/secrets.env` (0600).
- Server on 127.0.0.1 only; a Host/Origin guard blocks other websites from driving it.
- Look: the [C22](https://github.com/Ollornog/C22) design system, vendored under
  `src/kassist/gui/static/c22` by `scripts/vendor-c22.sh` (origin in `ORIGIN`); icons: Lucide (ISC),
  inline. The app's own CSS is layout only.
- Structure: view models (`gui/viewmodels.py`), form mapping (`gui/forms.py`), templates, i18n (en base,
  de); the tray helper (`tray.py`) runs on the system Python with GTK bindings.
- Platforms: Linux Mint now; Raspberry Pi OS uses the same code; Windows: `kassist gui` without tray and
  menu entry.

## Tests

```bash
.venv/bin/python -m pytest -q     # unit tests in the dev venv
./scripts/check.sh                # what CI runs: repo hygiene, fresh venv, tests, packaging smoke test
```

The tests never start Chrome, never reach Kleinanzeigen or an AI provider and never read the real
`~/.config/kassist` (see `tests/conftest.py`).

## Contributing

Bug reports and pull requests are welcome, see [CONTRIBUTING.md](CONTRIBUTING.md). Security issues go
through the private channel in [SECURITY.md](SECURITY.md). Everyone involved follows the
[Code of Conduct](CODE_OF_CONDUCT.md). Planned work and past decisions live in [backlog/](backlog/README.md).

## Licence

kassist is free software under the **GNU Affero General Public License v3.0 or later**
(`AGPL-3.0-or-later`), see [LICENSE](LICENSE). It builds on kleinanzeigen-bot, which is AGPL-3.0 as well.
Copyright © 2026 Ollornog.

## Third-party

Shipped in this repository under their own licences; the full notices are in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

- [kleinanzeigen-bot](https://github.com/Second-Hand-Friends/kleinanzeigen-bot): a dependency (git, pinned
  by commit), not vendored; `tools/fix_nodriver.py` is its nodriver post-install fix, copied unchanged
  (AGPL-3.0-or-later, header kept).
- [C22](https://github.com/Ollornog/C22) design system under `src/kassist/gui/static/c22` (MIT), which
  contains Basecoat (MIT), Phosphor icons (MIT) and the Inter font (SIL Open Font License 1.1, text in
  `fonts/OFL.txt`). Origin and version: `src/kassist/gui/static/c22/ORIGIN`.
- [Lucide](https://lucide.dev) icons (ISC), inlined in `src/kassist/gui/templates/_icons.html`.
