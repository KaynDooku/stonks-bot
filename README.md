# 📈 Finanz-News Discord Bot

Postet zu festen Zeiten einen **Aktien-Überblick** in einen Discord-Channel:

- Mo–Fr um **8:30**, **12:30** und **22:15 Uhr**, Sa + So um **10:00 Uhr** (deutsche Zeit)
- bis zu **8 Aktien** mit relevanten Neuigkeiten, jeweils mit
  - Kurs und Tagesveränderung (Yahoo Finance, ca. 15 Min. verzögert)
  - bis zu 5 nummerierten Meldungen, möglichst aus verschiedenen Medien, mit kurzem Vorspann
    (englische Meldungen werden automatisch ins Deutsche übersetzt, Google Übersetzer, inoffiziell)
  - erkannten positiven Signalen und Risiken (mit Verweis auf den jeweiligen Artikel)
  - einer automatischen Einstufung 🟢🟡🔴 der **Nachrichtenlage**
  - allen Quellen mit Datum, Uhrzeit und Link zum Nachlesen
- verbindlicher Hinweis „Keine Anlageberatung“ in jedem Überblick
- gibt es keine Aktie mit erkannten Signalen, wird **nichts** gepostet

Es wird **keine KI** verwendet. Die Einstufung ist rein regelbasiert:
🟢 mindestens 2 verschiedene positive und keine negativen Signale,
🔴 mehr negative als positive Signale, sonst 🟡.

| Datei | Inhalt |
|---|---|
| `feeds.json` | Nachrichtenquellen (RSS-Feeds) |
| `aktien.json` | beobachtete Aktien (ca. 280: DAX, MDAX, SDAX, TecDAX, große US- und EU-Werte) mit den Namen, unter denen sie in Meldungen vorkommen |
| `overview.py` | Erkennung von Firmen und Schlagworten, Ampel, Aufbau der Discord-Nachrichten |
| `post_news.py` | ein Durchlauf für GitHub Actions |
| `bot.py` | Dauerbetrieb-Bot mit `/news`, `/quellen` usw. (unverändert) |

**Quellen** (in `feeds.json`, beliebig erweiterbar): Tagesschau (Wirtschaft + Börse), Handelsblatt,
WirtschaftsWoche, Spiegel, FAZ, n-tv, Welt, Manager Magazin, finanznachrichten.de, onvista,
CNBC (4 Feeds), MarketWatch (2 Feeds), Business Insider Markets, Investing.com.
Werbe- und „Hot-Stock“-Artikel werden herausgefiltert.

### Aktie hinzufügen

In `aktien.json` einen Eintrag ergänzen. `ticker` ist das Kürzel bei Yahoo Finance
(deutsche Aktien mit `.DE`), `aliases` sind die Namen in den Meldungen (Groß-/Kleinschreibung zählt):

```json
{"name": "Siemens", "ticker": "SIE.DE", "aliases": ["Siemens"]}
```

Optional: `aliases_de` / `aliases_en` (nur in deutschen bzw. englischen Quellen) und `exclude`
(Wörter, die nicht gemeint sind, z. B. `"Bayer Leverkusen"`).

### Probelauf (nichts wird gepostet)

**Actions → Finanz-News posten → Run workflow** → Zweig auswählen → Häkchen bei
„Probelauf“ lassen → **Run workflow**. Im Protokoll des Laufs (Schritt „Aktien-Ueberblick
erstellen und posten“) steht dann, was gepostet würde. Ohne Häkchen wird sofort gepostet.

---

## ☁️ Kostenlos rund um die Uhr über GitHub Actions

`post_news.py` holt einmal neue News, postet sie und beendet sich. GitHub startet es über
`.github/workflows/news.yml` zu den oben genannten Zeiten. `state.json` (bereits verwendete News) wird
nach jedem Lauf automatisch im Repository gespeichert.

1. Auf <https://github.com/new> ein Repository anlegen, **Public** (dann sind die
   Actions-Minuten unbegrenzt kostenlos).
2. „uploading an existing file" → alle Dateien aus dem Upload-Ordner hineinziehen
   (**niemals `.env` hochladen!**) → „Commit changes".
3. **Settings → Secrets and variables → Actions → New repository secret**:
   - `DISCORD_TOKEN` = dein Bot-Token
   - `NEWS_CHANNEL_ID` = ID des News-Channels
4. **Actions** → „Finanz-News posten" → **Run workflow** für einen ersten Probelauf.

Hinweise: Die /-Befehle gibt es in diesem Modus nicht (dafür bräuchte es einen Dauerprozess).
GitHub startet geplante Läufe manchmal einige Minuten verspätet.

---

## 1. Bot bei Discord anlegen

1. Öffne <https://discord.com/developers/applications> → **New Application** → Namen vergeben.
2. Links **Bot** → **Reset Token** → Token kopieren (geheim halten!).
3. Links **OAuth2 → URL Generator**:
   - Scopes: `bot` und `applications.commands`
   - Bot Permissions: `View Channels`, `Send Messages`, `Embed Links`
4. Die erzeugte URL öffnen und den Bot auf deinen Server einladen.

## 2. Python installieren

Python **3.10 oder neuer** von <https://www.python.org/downloads/> installieren
(bei der Installation **„Add Python to PATH"** anhaken).

## 3. Bot einrichten

Im Ordner des Bots:

```bash
py -m venv .venv
```

```bash
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

```bash
copy .env.example .env
```

Dann `.env` öffnen und mindestens `DISCORD_TOKEN` eintragen. Optional `NEWS_CHANNEL_ID`
(in Discord: Einstellungen → Erweitert → Entwicklermodus an, dann Rechtsklick auf den
Channel → „Kanal-ID kopieren").

## 4. Starten

```bash
.\.venv\Scripts\python.exe bot.py
```

Beim ersten Start postet der Bot die neuesten paar Meldungen, danach alle
`POLL_INTERVAL_MINUTES` Minuten nur noch neue.

## Befehle

| Befehl | Wer | Funktion |
|---|---|---|
| `/setchannel #kanal` | Admins („Server verwalten") | Channel für automatische News festlegen |
| `/news [anzahl] [quelle]` | alle | Die neuesten Meldungen sofort anzeigen |
| `/quellen` | alle | Alle Nachrichtenquellen auflisten |
| `/jetzt` | Admins | Sofort nach neuen News suchen |
| `/status` | alle | Channel, Intervall, letzter Check, Filter |

## Einstellungen (`.env`)

| Variable | Standard | Bedeutung |
|---|---|---|
| `POLL_INTERVAL_MINUTES` | 10 | Abstand zwischen den Checks |
| `MAX_POSTS_PER_CYCLE` | 5 | Max. Posts pro Check (Anti-Spam) |
| `KEYWORDS` | leer | Nur News mit diesen Wörtern, z. B. `DAX,EZB,Fed,Bitcoin` |
| `ENRICH_ARTICLES` | true | Artikelseite abrufen für Bild & längere Beschreibung |

## Eigene Quellen hinzufügen

In `feeds.json` einen Eintrag ergänzen (jeder RSS/Atom-Feed funktioniert):

```json
{"name": "Mein Feed", "url": "https://example.com/rss", "lang": "de", "color": "#123456"}
```

## Hinweise

- `state.json` merkt sich bereits gepostete Meldungen und die Channel-Einstellung – nicht löschen,
  sonst wird beim nächsten Start erneut gepostet.
- Der Bot zeigt nur Titel, Kurzbeschreibung und Link der Medien an und verweist immer auf die
  Originalquelle. Die Embeds tragen den Hinweis „Keine Anlageberatung".
- Damit der Bot dauerhaft läuft, muss der Rechner an bleiben – oder du hostest ihn auf einem
  kleinen Server/VPS bzw. Raspberry Pi.
