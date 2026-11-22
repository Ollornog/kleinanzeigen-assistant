# Sicherheitsrichtlinie

<a href="../SECURITY.md">English</a> · <b>Deutsch</b>
<br /><br />

## Eine Sicherheitslücke melden

Bitte vertraulich über GitHubs
[private Meldung von Sicherheitslücken](https://github.com/Ollornog/kleinanzeigen-assistant/security/advisories/new)
oder per Mail an kleinanzeigen-assistant-github@ollornog.de, nicht in einem öffentlichen Issue. Eine
erste Antwort kommt innerhalb von 14 Tagen. Korrekturen erscheinen in einem neuen Release; der Hinweis
wird veröffentlicht, sobald es eine korrigierte Version gibt.

## Unterstützte Versionen

Nur das jeweils neueste Release bekommt Korrekturen.

## Wissenswerte Entwurfsentscheidungen

- **Nur lokal.** Der Server der Oberfläche lauscht auf 127.0.0.1; ein Host/Origin-Wächter weist Anfragen
  ab, die andere Webseiten im selben Browser an ihn schicken könnten.
- **Geheimnisse bleiben aus der Config.** Kleinanzeigen-Login und Mistral-Schlüssel liegen in
  `~/.config/kassist/secrets.env` mit Rechten 0600. Die Oberfläche schreibt den Schlüssel, zeigt ihn
  aber nie wieder an.
- **Käufernachrichten sind fremder Text.** Die KI entwirft nur Text: Claude Code läuft als `claude -p`
  ohne Werkzeuge, MCP-Server, Hooks und CLAUDE.md, Mistral ist eine reine Chat-Anfrage. Automatische
  Antworten sind aus, bis man sie einschaltet, und je Unterhaltung begrenzt.
- **Das Browserprofil gehört dir.** kassist steuert ein sichtbares Chrome mit eigenem Profil über
  Remote-Debugging auf 127.0.0.1. Wer diesen Port auf deinem Rechner erreicht, kann die angemeldete
  Sitzung steuern; ihn also nicht nach außen öffnen.

## Nicht im Umfang

Die Plattform Kleinanzeigen selbst, die KI-Anbieter, kleinanzeigen-bot (dort melden) und alles, was ein
lokaler Benutzer mit Zugriff auf dein Konto und deine Dateien ohnehin tun kann.

<br /><br />
<p align="right"><img src="../docs/kassist.png" alt="kassist" width="60" height="60"></p>
