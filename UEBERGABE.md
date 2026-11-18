# Übergabe: Stand des Stonks-Bots (18.11.2026)

Diese Datei ist für die Weiterarbeit auf einem anderen PC bzw. in einer neuen Claude-Sitzung.

## Aktueller Stand

- **Discord-Bot:** `Stonks#6102`, Server **Exekutor**, Channel **#stonks📈**
  (Channel-ID `1572580534545031188`). Public Bot ist ausgeschaltet.
- **Läuft gerade über GitHub Actions** (`.github/workflows/news.yml`): alle 15 Minuten
  startet `post_news.py`, postet bis zu 5 neue Finanz-News und speichert `state.json`
  (bereits gepostete Meldungen) zurück ins Repo.
- **Secrets im Repo:** `DISCORD_TOKEN`, `NEWS_CHANNEL_ID`.
- 2FA ist bei GitHub aktiv.

## Warum ein Umzug geplant ist

GitHubs Nutzungsbedingungen (Additional Product Terms, Abschnitt Actions) erlauben
GitHub-gehostete Runner nur für Aktivitäten rund um Entwicklung/Test/Veröffentlichung der
Software im Repo – ein dauerhafter News-Cronjob fällt nicht darunter. Ziel: **Oracle Cloud
Always Free** (kostenlos, legal, PC muss nicht an sein, `/`-Befehle funktionieren wieder).

## Nächste Schritte (Oracle-Umzug)

1. **Nutzer:** Oracle-Always-Free-Konto anlegen (<https://www.oracle.com/cloud/free/>),
   Home Region **Germany Central (Frankfurt)**, 2FA aktivieren.
2. SSH-Schlüssel auf dem PC erzeugen (`ssh-keygen -t ed25519 -f ~/.ssh/oracle_stonks`).
3. **Nutzer:** VM anlegen – Ubuntu 24.04, Shape **VM.Standard.E2.1.Micro** (Always Free)
   oder Ampere A1, öffentlichen SSH-Schlüssel hinterlegen, öffentliche IP notieren.
4. Server einrichten (per SSH):
   - `unattended-upgrades` aktivieren, nur Schlüssel-Login (kein Passwort)
   - eigener Benutzer `stonks` ohne Root-Rechte, Code nach `/opt/stonks-bot`
   - Python-venv + `pip install -r requirements.txt`
   - `.env` mit `DISCORD_TOKEN`, `NEWS_CHANNEL_NAME=stonks`, Rechte `600`
   - aktuelle `state.json` aus dem Repo übernehmen (sonst doppelte Posts)
   - systemd-Dienst für `bot.py` mit `Restart=always`
5. Prüfen: Bot online, postet in #stonks📈, `/news` funktioniert.
6. Danach GitHub-Workflow abschalten:
   `gh workflow disable news.yml -R KaynDooku/stonks-bot`

## Hinweise für die Einrichtung

- Der Discord-Token steht **nicht** im Repo (nur als verschlüsseltes Secret, das man nicht
  auslesen kann). Für den Server muss er neu eingetragen werden – notfalls im Discord
  Developer Portal unter *Bot → Reset Token* einen neuen erzeugen und danach auch das
  GitHub-Secret aktualisieren, solange der Workflow noch läuft.
- Unter Windows PowerShell 5.1 hängt das Pipen an Programme ein unsichtbares BOM an –
  vorher `$OutputEncoding = New-Object System.Text.UTF8Encoding $false` setzen.
- Der Nutzer ist kein Entwickler: Anleitungen bitte Schritt für Schritt auf Deutsch.
