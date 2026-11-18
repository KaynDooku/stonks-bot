# Übergabe: Stand des Stonks-Bots (18.11.2026)

Diese Datei ist für die Weiterarbeit auf einem anderen PC bzw. in einer neuen Claude-Sitzung.

## Aktueller Stand

- **Discord-Bot:** `Stonks#6102`, Server **Exekutor** (privat, Freunde/Bekannte), Channel **#stonks📈**
  (Channel-ID `1572580534545031188`). Public Bot ist ausgeschaltet.
- **Läuft über GitHub Actions** (`.github/workflows/news.yml`) – das bleibt so (Entscheidung
  des Nutzers, ein Umzug zu Oracle o. Ä. ist **nicht** mehr geplant).
- **Secrets im Repo:** `DISCORD_TOKEN`, `NEWS_CHANNEL_ID`.
- 2FA ist bei GitHub aktiv.

## Nachrichtenformat (gemeinsam mit dem Nutzer festgelegt)

- **Aktien-Überblick** statt einzelner Schlagzeilen: Mo–Fr 8:30 / 12:30 / 22:15 Uhr,
  Sa + So 10:00 Uhr. Nur posten, wenn mindestens 1 Aktie relevante Signale hat; max. 6 Aktien.
- **Keine KI** (bewusst entschieden, keine laufenden Kosten). Ampel 🟢🟡🔴 rein regelbasiert
  aus Schlagworten, **neutral formuliert** („Überwiegend positive Nachrichtenlage“ usw.),
  damit es nicht wie eine Anlageempfehlung wirkt.
- **Quellen sind dem Nutzer sehr wichtig:** pro Aktie bis zu 5 nummerierte Artikel aus
  möglichst verschiedenen Medien, jedes Signal mit Artikelnummer, Quellenblock mit Links.
- Kurse von Yahoo Finance (inoffiziell, verzögert); fällt die Abfrage aus, fehlt nur der Kurs.
- Keine Bilder/Autoren, Artikelseiten werden **nicht** abgerufen. Vorspann max. ca. 150 Zeichen
  (Leistungsschutzrecht der Presseverlage).
- Haftungsausschluss-Text vom Nutzer wörtlich vorgegeben (in `overview.py`, `DISCLAIMER`).
- Wichtig: Nutzer möchte bei Unklarheiten zu Design/Inhalt **vorher gefragt** werden.

## Mögliche nächste Schritte

- Nach Probeläufen prüfen, welche Feeds in `feeds.json` keine Meldungen liefern
  (steht im Log: „Keine Meldungen von: …“), und diese korrigieren oder entfernen.
- Schlagwort-Listen (`POSITIVE` / `NEGATIVE` in `overview.py`) anhand echter Ausgaben nachschärfen.

## Hinweise für die Einrichtung

- Der Discord-Token steht **nicht** im Repo (nur als verschlüsseltes Secret, das man nicht
  auslesen kann). Wird er woanders gebraucht, im Discord Developer Portal unter
  *Bot → Reset Token* einen neuen erzeugen und danach auch das GitHub-Secret aktualisieren.
- Unter Windows PowerShell 5.1 hängt das Pipen an Programme ein unsichtbares BOM an –
  vorher `$OutputEncoding = New-Object System.Text.UTF8Encoding $false` setzen.
- Der Nutzer ist kein Entwickler: Anleitungen bitte Schritt für Schritt auf Deutsch.
