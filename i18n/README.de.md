<p align="center"><img src="../docs/kassist.png" alt="kassist" width="250" height="250"></p>

<h1 align="center">kleinanzeigen-assistant (kassist)</h1>

<p align="center"><a href="../README.md">English</a> · <b>Deutsch</b></p>

<p align="right">
<a href="https://github.com/Ollornog/kleinanzeigen-assistant/actions/workflows/ci.yml"><img src="https://github.com/Ollornog/kleinanzeigen-assistant/actions/workflows/ci.yml/badge.svg" alt="Tests"></a>
<a href="../LICENSE"><img src="https://img.shields.io/badge/License-AGPL--3.0--or--later-informational.svg" alt="Lizenz: AGPL-3.0-or-later"></a>
<img src="https://img.shields.io/badge/python-3.13%2B-blue.svg" alt="Python 3.13+">
</p>

Lokaler Assistent für **ein privates Kleinanzeigen-Konto** mit einer Handvoll Artikeln:

- ein Ordner je Artikel (Fotos + `listing.yaml`) → Anzeige
- Preisplan: Startpreis halten, dann wöchentlich bis zum Mindestpreis senken, dann stehen lassen
- nach Ablauf: optional neu einstellen (selten, begrenzt, nach einer Wartezeit)
- Nachrichten: abrufen, Desktop-Benachrichtigung, KI-Antwortentwurf; Antworten aus / als Entwurf / automatisch
- Preisüberblick: Angebotspreise ähnlicher Anzeigen aus der Kleinanzeigen-Suche, optional mit KI-Gegencheck
- Eckdaten je Artikel (Zustand, eigener Kaufpreis, Marktneupreis, Kleinanzeigen-Median, eigene Zeilen):
  angehakte stehen im Anzeigentext, die übrigen sind Kontext für die KI-Antworten
- Ordner mit nur einer Textdatei: stumpf übernehmen oder von der KI nur das übernehmen lassen, was im Text steht
- KI: Claude Code (lokal, eigenes Abo) und/oder Mistral (API), eine aktiv, die andere als Ausweich-KI
- reservieren / aktivieren / verkauft / zurückziehen in der App (Tray-Symbol, Start beim Anmelden) oder per CLI

## Risiko

Die Nutzungsbedingungen von Kleinanzeigen (§ 5 Nr. 1) verbieten automatisierten Zugriff ohne
schriftliche Zustimmung, und Löschen plus Neueinstellen gilt als Duplikat. Das Werkzeug senkt das
Risiko (sichtbarer Browser, Login von Hand, wenige Aktionen je Lauf, Pausen, menschliches Tipptempo,
kein Captcha-Lösen), beseitigt es aber nicht. Nur für das eigene Konto und auf eigenes Risiko.

## So funktioniert es

```
 ┌───────────── kassist-Browser ─────────────┐
 │ sichtbares Chrome, eigenes Profil, Login   │◄── Remote-Debugging auf 127.0.0.1
 │ von Hand                                   │
 └───────────────────────────────────────────┘
        ▲ einstellen/ändern/reservieren/löschen   ▲ eigene Anzeigen, Nachrichten, Antworten
        │ (kleinanzeigen-bot dockt an)            │ (fetch() der Seite in einem Tab)
 kassist tick / panel ────────────────────────────┘
        │
 Ordner ─► listing.yaml ─► Planer (rein) ─► Aktionen ─► SQLite-Zustand
```

- [kleinanzeigen-bot](https://github.com/Second-Hand-Friends/kleinanzeigen-bot) (AGPL-3.0) erledigt die
  Formulararbeit. Sein eigenes Neueinstellen und seine Preissenkung werden nicht benutzt.
- Eine Browser-Sperre: Bot-Läufe, Sync und Nachrichtenabruf überschneiden sich nie.

## Einrichtung

Python ≥ 3.13 (Linux Mint bringt 3.12 mit: uv installiert ein verwaltetes Python; Raspberry Pi OS trixie hat 3.13).

```bash
uv venv --python 3.15 .venv && uv pip install --python .venv/bin/python -e ".[test]"
.venv/bin/python tools/fix_nodriver.py          # nodriver-Patch, den der Bot erwartet
mkdir -p ~/.config/kassist && cp examples/config.yaml ~/.config/kassist/config.yaml
install -m 600 /dev/null ~/.config/kassist/secrets.env   # dann KASSIST_KA_USER / KASSIST_KA_PASSWORD eintragen
.venv/bin/kassist install-desktop               # Menüeintrag „Kleinanzeigen-Assistent“
.venv/bin/kassist browser                       # einmal im geöffneten Fenster anmelden
```

## Bedienung

```bash
kassist status            # Ordner, Zustand, nächste Aktion
kassist new <ordner>      # die listing.yaml-Vorlage in einen Ordner schreiben
kassist import            # listing.yaml aus vorhandenen *_inserat_*.md-Entwürfen (approved: false)
kassist tick --dry-run    # was heute passieren würde
kassist tick              # mit dem Konto abgleichen, dann fällige Aktionen ausführen
kassist inbox             # Nachrichten abrufen, benachrichtigen, Antworten entwerfen
kassist prices <ordner>   # Angebotspreise ähnlicher Anzeigen (Median, Quartile); --query "...", --ai
kassist reserve <ordner>  # ebenso: activate, sold, withdraw
kassist gui               # Oberfläche als App-Fenster (kassist panel: nur Server, http://127.0.0.1:8765)
kassist install-desktop   # Linux: Menüeintrag „Kleinanzeigen-Assistent“
```

### listing.yaml

Jeder Artikel hat denselben Aufbau; `kassist new <ordner>` schreibt die kommentierte Vorlage
([src/kassist/listing_template.yaml](../src/kassist/listing_template.yaml)). Leere Werte fallen auf
`listing_defaults` aus der Config zurück.

```yaml
approved: true
title: DJI Mini 2 Fly More Combo wie neu
description: |            # nur der Artikeltext
  ...
seller: alex            # Profil aus der Config "sellers": Name und Ort in der Anzeige
texts: [condition, payment, legal]   # Standardtexte aus der Config, angehängt
pricing:
  start: 330              # Startpreis
  reduce_to: 290          # bis hierhin senkt der Preisplan automatisch
  min_sale: 270           # tiefster Preis, den die Antwortentwürfe annehmen dürfen
  type: NEGOTIABLE
  schedule: {hold_days: 14, steps: 3, every_days: 7}   # 3 gleiche Senkungen; oder step: "5" / "10%"
availability: {from: 2026-12-01, until: 2027-03-31}   # nicht vor ... einstellen / nach ... zurückziehen
location: {zipcode: "20095", city: Hamburg}       # überschreibt den Ort des Verkäufers
shipping: {enabled: true, cost: 6.19}
republish: {enabled: true, after_days: 14, max: 2}
price_check: {query: DJI Mini 2 Fly More, exclude: [akku]}
category: Elektronik > PC-Zubehör & Software   # vor der Freigabe Pflicht
photos: {order: [b.jpg, a.jpg], skip: [c.jpg]}    # erstes = Titelbild
attributes: {condition_s: like_new}   # optionale Kategoriefelder
facts:                    # Eckdaten; show: true = Zeile unter dem Artikeltext, false = nur KI-Kontext
  condition: {value: like_new}                 # Satz aus der Config "condition_texts"
  purchase_price: {value: 459, show: false}    # "Kaufpreis: 459 €"
  market_new: {value: 399, show: false}        # "Marktneupreis: 399 €"
  market_used: {value: 310, show: false}       # Kleinanzeigen-Median, Knopf im Editor
  extra: [{label: Lieferumfang, value: "3 Akkus, Tasche", show: true}]
```

Config-Seite (`~/.config/kassist/config.yaml`): `sellers` (Name, Ort), `text_blocks`
(Standardtexte), `condition_texts` (Wortlaut je Zustandsstufe), `listing_defaults` (derselbe Aufbau wie
oben), `llm`, `replies`, `auto`. Siehe [examples/config.yaml](../examples/config.yaml).

Der Preisüberblick liest eine oder zwei Seiten einer normalen Kleinanzeigen-Suche im selben Browser und
fasst **Angebotspreise** zusammen (keine Verkaufspreise): Treffer mit Wörtern wie „defekt“, „suche“,
„tausch“ und IQR-Ausreißer fallen weg, eigene Anzeigen werden übersprungen. Die letzten 3 Prüfungen
stehen in einem `market`-Block am Ende der listing.yaml (schreibt kassist; die eigenen Werte darüber
bleiben unberührt). Mit `--ai` vergleicht die KI Zustand und Lieferumfang jedes Angebots mit dem eigenen
Artikel, rechnet Zubehör oder Fehlendes heraus und schlägt start / reduce_to / min_sale vor.

## Oberfläche

`kassist gui` startet kassist im Hintergrund mit Symbol in der Leiste und öffnet das App-Fenster
(Chrome/Chromium im App-Modus mit eigenem Profil; ohne beide der Standardbrowser). Fenster schließen
lässt kassist weiterlaufen, „Beenden“ im Tray oder in der Seitenleiste beendet es. `kassist gui
--background` startet nur mit dem Tray-Symbol; unter Einstellungen → Allgemein startet es auf Wunsch
beim Anmelden im Hintergrund (XDG-Autostart).

- **Artikel:** Ordner unter dem eingestellten Artikelordner. Ohne `listing.yaml`: Vorlage, Textdatei
  übernehmen (stumpf oder mit KI) oder `*_inserat_*.md`-Entwurf importieren.
- **Editor:** alle Felder, Eckdaten mit Haken „im Text“, Fotos (Reihenfolge, Zuschnitt, Kamera),
  KI-Hilfe für Text, Titel und Kategorie, Preisplan, Preis-Check.
- **Einstellungen:** Ordner, Profile, Standard- und Zustandstexte, Vorgaben, Grenzen, KI. Dazu die
  Seiten Nachrichten (mit Antwortentwürfen) und Protokoll.
- **Freigabe:** Erst freigegebene Artikel werden eingestellt. Änderungen an Online-Anzeigen werden
  vorgemerkt und mit „Änderungen übertragen“ hochgeladen; Eckdaten, die nur KI-Kontext sind, lösen nie
  einen Upload aus.
- **Sync:** beim Start und täglich, Nachrichten alle 10 Minuten, solange Anzeigen online sind. Ist nichts
  online und nichts fällig, gibt es keinen Sync, keinen Browser und keinen Verkehr. „Jetzt abgleichen“
  gleicht immer ab.
- **Browser:** Der Kleinanzeigen-Browser startet nur bei Bedarf, läuft minimiert und kommt nach vorn bei
  Login, Captcha, SMS-Prüfung, Fehlern und (Vorgabe) beim Ändern von Anzeigen. Headless wird bewusst
  vermieden: Die Seite erkennt es.
- **KI:** Claude Code läuft als `claude -p` ohne Werkzeuge, MCP-Server, Hooks und CLAUDE.md
  (Käufernachrichten sind fremder Text). Mistral braucht `MISTRAL_API_KEY` in
  `~/.config/kassist/secrets.env` (0600).
- **Sicherheit:** Server nur auf 127.0.0.1; ein Host/Origin-Wächter hindert fremde Webseiten daran, ihn
  fernzusteuern.
- **Aussehen:** das Designsystem [C22](https://github.com/Ollornog/C22), mit `scripts/vendor-c22.sh` nach
  `src/kassist/gui/static/c22` kopiert (Herkunft in `ORIGIN`); Symbole: Lucide (ISC), eingebettet. Das
  eigene CSS ist nur Layout.
- **Aufbau:** View-Models (`gui/viewmodels.py`), Formular-Abbildung (`gui/forms.py`), Templates, i18n
  (Grundsprache Englisch, dazu Deutsch); der Tray-Helfer (`tray.py`) läuft auf dem System-Python mit
  GTK-Bindings.
- **Plattformen:** derzeit Linux Mint; Raspberry Pi OS nutzt denselben Code; Windows: `kassist gui` ohne
  Tray und Menüeintrag.

## Tests

```bash
.venv/bin/python -m pytest -q     # Fachtests in der Entwicklungs-venv
./scripts/check.sh                # was die CI fährt: Repo-Hygiene, frische venv, Tests, Paket-Rauchtest
```

Die Tests starten nie Chrome, erreichen weder Kleinanzeigen noch einen KI-Anbieter und lesen nie das
echte `~/.config/kassist` (siehe `tests/conftest.py`).

## Mitmachen

Fehlermeldungen und Pull Requests sind willkommen, siehe [CONTRIBUTING.de.md](CONTRIBUTING.de.md).
Sicherheitslücken bitte vertraulich melden, siehe [SECURITY.de.md](SECURITY.de.md). Für alle Beteiligten
gilt der [Verhaltenskodex](CODE_OF_CONDUCT.de.md). Geplantes und frühere Entscheidungen stehen in
[backlog/](../backlog/README.md).

## Lizenz

kassist ist freie Software unter der **GNU Affero General Public License v3.0 oder später**
(`AGPL-3.0-or-later`), siehe [LICENSE](../LICENSE). Es baut auf kleinanzeigen-bot auf, das ebenfalls unter
der AGPL-3.0 steht. Copyright © 2026 Ollornog.

## Fremdbestandteile

In diesem Repository unter ihren eigenen Lizenzen enthalten; die vollständigen Hinweise stehen in
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

- [kleinanzeigen-bot](https://github.com/Second-Hand-Friends/kleinanzeigen-bot): Abhängigkeit (git, per
  Commit gepinnt), nicht kopiert; `tools/fix_nodriver.py` ist sein nodriver-Nachinstallations-Fix,
  unverändert übernommen (AGPL-3.0-or-later, Kopf erhalten).
- Das Designsystem [C22](https://github.com/Ollornog/C22) unter `src/kassist/gui/static/c22` (MIT), darin
  Basecoat (MIT), Phosphor-Symbole (MIT) und die Schrift Inter (SIL Open Font License 1.1, Text in
  `fonts/OFL.txt`). Herkunft und Version: `src/kassist/gui/static/c22/ORIGIN`.
- [Lucide](https://lucide.dev)-Symbole (ISC), eingebettet in `src/kassist/gui/templates/_icons.html`.
