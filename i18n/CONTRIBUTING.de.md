# Mitwirken

<a href="../CONTRIBUTING.md">English</a> · <b>Deutsch</b>
<br /><br />

Danke fürs Mitmachen. kassist ist ein kleines Werkzeug für ein privates Konto; eine Änderung, die es
klein hält und das Konto schont, ist meist die bessere. Fragen oder größere Ideen: erst ein Issue
eröffnen oder an kleinanzeigen-assistant-github@ollornog.de schreiben.

## Grundregeln

1. **Das Konto schonen.** Kein Headless-Browser, kein Captcha-Lösen, keine Aktions-Salven. Was kassist
   weniger wie einen Menschen auf der Seite aussehen lässt, braucht einen sehr guten Grund.
2. **Tests gehören zur Änderung.** Wer Verhalten ändert, ändert im selben Commit einen Test. Doku (beide
   READMEs) und `CHANGELOG.md` gehen mit.
3. **Die Suite bleibt offline und wiederholbar.** Tests starten nie Chrome, erreichen weder
   Kleinanzeigen noch einen KI-Anbieter und lesen nie das echte `~/.config/kassist`.
   `./scripts/check.sh` zweimal fahren; beide Läufe müssen grün sein.
4. **Keine persönlichen Daten im Repository.** Keine echten Namen, Adressen, Postleitzahlen,
   Kontodaten oder Nachrichteninhalte, weder in Code, Beispieldaten, Tests, Doku noch Commit-Texten.
   Neutrale Beispiele verwenden (`10115 Berlin`, Verkäufer `alex`). `tests/test_repo.py` prüft auf
   private Infrastruktur und Geheimnisse.
5. **Das Aussehen gehört C22.** `src/kassist/gui/static/c22` ist eine Kopie; Änderungen gehören nach
   C22, danach `scripts/vendor-c22.sh` erneut fahren.

## Ablauf

Auf einem Feature-Branch arbeiten. Dort läuft keine CI, `./scripts/check.sh` ist das Sicherheitsnetz.
Einen Pull Request öffnen; die CI läuft am PR und auf `main`.

```bash
git switch -c meine-aenderung
git config core.hooksPath .githooks    # einmal je Klon: der pre-push-Hook fährt die Suite
# ... ändern, dann:
./scripts/check.sh
git commit -am "Die Änderung beschreiben, nicht den Diff"
git push -u origin meine-aenderung
```

## Stil

- Code, Bezeichner, Kommentare, Log- und Fehlertexte sind Englisch; die Oberfläche ist Englisch mit
  deutscher Übersetzung (`src/kassist/gui/i18n.py`).
- Kommentare erklären das **Warum**, nicht was die nächste Zeile tut.
- Dateien sind UTF-8 ohne BOM.
- Keine neue Abhängigkeit ohne einen Grund, der in einen Satz passt. `kleinanzeigen-bot` bleibt per
  Commit gepinnt; den Pin erst nach dem Lesen des Upstream-Diffs anheben.

## Fehler melden

Bitte angeben, was erwartet war, was passiert ist, die kassist-Version (`CHANGELOG.md`) und die
kleinste Nachstellung. Persönliche Daten weglassen: keine Kontonamen, keine Käufernachrichten, keine
Adressen.

Sicherheitslücken nicht öffentlich melden, sondern wie in [SECURITY.de.md](SECURITY.de.md) beschrieben.

<br /><br />
<p align="right"><img src="../docs/kassist.png" alt="kassist" width="60" height="60"></p>
