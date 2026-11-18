"""
Einmaliger Durchlauf (fuer GitHub Actions): Aktien-Ueberblick erstellen und in den
Discord-Channel posten, dann beenden. Kein Dauerbetrieb noetig.

Feste Ausgaben (deutsche Zeit):
  Mo-Fr  08:30 Morgenausgabe, 12:30 Mittagsausgabe, 22:15 Abendausgabe
  Sa+So  10:00 Wochenendausgabe
GitHub rechnet in UTC und kennt keine Sommerzeit. Der Workflow startet deshalb zu beiden
moeglichen UTC-Zeiten; dieses Skript prueft selbst, ob gerade eine Ausgabe faellig ist,
und postet jede Ausgabe nur einmal.

Benoetigt die Umgebungsvariablen DISCORD_TOKEN und NEWS_CHANNEL_ID.

Optionen:
  --dry-run      nichts posten, nur anzeigen was gepostet wuerde (Vorschau im Log + vorschau.json)
  --ignore-seen  auch Meldungen beruecksichtigen, die schon in frueheren Ausgaben vorkamen
  --force        Uhrzeit-Pruefung ueberspringen (z. B. beim manuellen Start)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, time, timedelta, timezone

import aiohttp

import bot
import overview

API = "https://discord.com/api/v10"

# (Wochentage, Uhrzeit, Name) - Wochentage: 0 = Montag ... 6 = Sonntag
SLOTS = [
    (range(0, 5), time(8, 30), "Morgenausgabe"),
    (range(0, 5), time(12, 30), "Mittagsausgabe"),
    (range(0, 5), time(22, 15), "Abendausgabe"),
    (range(5, 7), time(10, 0), "Wochenendausgabe"),
]
EARLY = timedelta(minutes=20)    # so viel frueher darf ein Lauf starten
LATE = timedelta(minutes=150)    # so viel spaeter (GitHub verspaetet sich manchmal)
MIN_WINDOW = timedelta(hours=12)  # Meldungen mindestens so weit zurueck auswerten
MAX_WINDOW = timedelta(hours=48)


def current_slot(now: datetime) -> tuple[str, str] | None:
    """Gibt (Schluessel, Name) der gerade faelligen Ausgabe zurueck oder None."""
    local = now.astimezone(overview.BERLIN)
    for days, at, name in SLOTS:
        for day_offset in (0, -1):  # auch kurz nach Mitternacht die Abendausgabe vom Vortag
            d = local.date() + timedelta(days=day_offset)
            if d.weekday() not in days:
                continue
            slot_dt = datetime.combine(d, at, tzinfo=overview.BERLIN)
            if slot_dt - EARLY <= local < slot_dt + LATE:
                return f"{d.isoformat()} {at:%H:%M}", name
    return None


async def send(session: aiohttp.ClientSession, channel_id: str, token: str, payload: dict) -> bool:
    headers = {"Authorization": f"Bot {token}", "User-Agent": "DiscordBot (https://github.com, 1.0)"}
    url = f"{API}/channels/{channel_id}/messages"
    for _ in range(3):
        async with session.post(url, json=payload, headers=headers) as r:
            if r.status == 429:  # Rate-Limit: kurz warten und erneut versuchen
                await asyncio.sleep(float((await r.json()).get("retry_after", 2)))
                continue
            if r.status >= 400:
                text = await r.text()
                if r.status in (401, 403, 404):
                    # Token/Channel/Rechte falsch -> abbrechen, ohne den Zustand zu speichern
                    sys.exit(f"Discord-Fehler {r.status}: {text[:300]}")
                print(f"Discord-Fehler {r.status}: {text[:300]}")
                return False
            return True
    return False


async def main(dry_run: bool, ignore_seen: bool, force: bool) -> None:
    # strip("﻿ ...") entfernt auch unsichtbare BOM-Zeichen, falls beim Setzen der Secrets entstanden
    token = os.getenv("DISCORD_TOKEN", "").strip().strip("﻿").strip()
    channel_id = os.getenv("NEWS_CHANNEL_ID", "").strip().strip("﻿").strip()
    if not dry_run and not (token and channel_id):
        sys.exit("DISCORD_TOKEN und NEWS_CHANNEL_ID muessen gesetzt sein.")

    now = datetime.now(timezone.utc)
    state = bot.State(bot.STATE_FILE)
    done: list[str] = state.extra.get("ausgaben", [])

    slot = current_slot(now)
    edition = None
    if not force:
        if slot is None:
            print("Gerade ist keine Ausgabe faellig - nichts zu tun.")
            return
        if slot[0] in done:
            print(f"Ausgabe {slot[0]} wurde bereits erstellt - nichts zu tun.")
            return
        edition = slot[1]

    # Zeitraum: seit der letzten Ausgabe, mindestens 12 und hoechstens 48 Stunden
    last = state.extra.get("letzte_ausgabe")
    window = MIN_WINDOW
    if last:
        try:
            window = min(MAX_WINDOW, max(MIN_WINDOW, now - datetime.fromisoformat(last)))
        except ValueError:
            pass

    feeds = bot.load_feeds()
    stocks = overview.load_stocks()
    seen = set() if ignore_seen else set(state.seen)

    async with aiohttp.ClientSession(headers={"User-Agent": bot.USER_AGENT}) as session:
        items = await bot.fetch_all(session, feeds)
        if not items:
            print("Keine Meldungen abrufbar - versuche es beim naechsten Lauf erneut.")
            return
        print(f"{len(items)} Meldungen aus {len({i.feed.name for i in items})} von {len(feeds)} Quellen geladen.")
        missing = [f.name for f in feeds if f.name not in {i.feed.name for i in items}]
        if missing:
            print("Keine Meldungen von: " + ", ".join(missing))

        reports = overview.build_reports(items, stocks, seen, window, now)
        if reports:
            await overview.add_quotes(session, reports)
        messages = overview.build_overview_messages(reports, [f.name for f in feeds], now, window, edition) if reports else []

        if dry_run:
            print(overview.diagnose(items, stocks, window, now))
        if not reports:
            print("Keine Aktie mit erkannten Signalen - diesmal wird nichts gepostet.")
        elif dry_run:
            print(overview.preview_text(messages))
            with open("vorschau.json", "w", encoding="utf-8") as fh:
                json.dump(messages, fh, ensure_ascii=False, indent=1)
        else:
            for msg in messages:
                ok = await send(session, channel_id, token, {"embeds": msg})
                if not ok:
                    print("Eine Nachricht konnte nicht gesendet werden.")
                await asyncio.sleep(1.5)

    if dry_run:
        print(f"[Probelauf] {len(reports)} Aktien, {len(messages)} Discord-Nachricht(en) - nichts gepostet.")
        return

    # Alle ausgewerteten Meldungen merken, damit sie nicht erneut als "neu" zaehlen
    for it in items:
        state.mark_seen(it.uid)
    if slot and not force:
        state.extra["ausgaben"] = (done + [slot[0]])[-30:]
    state.extra["letzte_ausgabe"] = now.isoformat()
    state.save()
    print(f"Fertig: {len(reports)} Aktien in {len(messages)} Nachricht(en) gepostet.")


if __name__ == "__main__":
    asyncio.run(main("--dry-run" in sys.argv, "--ignore-seen" in sys.argv, "--force" in sys.argv))
