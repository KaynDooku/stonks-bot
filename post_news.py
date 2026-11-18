"""
Einmaliger Durchlauf (fuer GitHub Actions): neue Finanz-News holen und in den
Discord-Channel posten, dann beenden. Kein Dauerbetrieb noetig.

Benoetigt die Umgebungsvariablen DISCORD_TOKEN und NEWS_CHANNEL_ID.
Mit --dry-run wird nichts gepostet, nur angezeigt was gepostet wuerde.
"""

import asyncio
import os
import sys

import aiohttp

import bot

API = "https://discord.com/api/v10"


def link_buttons(item: bot.NewsItem) -> list[dict]:
    return [{
        "type": 1,
        "components": [
            {"type": 2, "style": 5, "label": "Zum Artikel", "url": item.link, "emoji": {"name": "🔗"}},
            {"type": 2, "style": 5, "label": item.feed.name[:80], "url": item.feed.homepage},
        ],
    }]


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


async def main(dry_run: bool) -> None:
    # strip("﻿ ...") entfernt auch unsichtbare BOM-Zeichen, falls beim Setzen der Secrets entstanden
    token = os.getenv("DISCORD_TOKEN", "").strip().strip("﻿").strip()
    channel_id = os.getenv("NEWS_CHANNEL_ID", "").strip().strip("﻿").strip()
    if not dry_run and not (token and channel_id):
        sys.exit("DISCORD_TOKEN und NEWS_CHANNEL_ID muessen gesetzt sein.")

    state = bot.State(bot.STATE_FILE)
    async with aiohttp.ClientSession(headers={"User-Agent": bot.USER_AGENT}) as session:
        items = await bot.fetch_all(session, bot.load_feeds())
        if not items:
            print("Keine Meldungen abrufbar - versuche es beim naechsten Lauf erneut.")
            return
        to_post, n_fresh = bot.select_to_post(items, state)

        posted = 0
        for item in to_post:
            await bot.enrich(session, item)
            embed, _ = bot.build_message(item)
            if dry_run:
                print(f"[dry-run] {item.feed.name}: {item.title}")
                posted += 1
                continue
            if await send(session, channel_id, token, {"embeds": [embed.to_dict()], "components": link_buttons(item)}):
                posted += 1
            await asyncio.sleep(1.5)

    if not dry_run:
        state.save()
    print(f"Fertig: {len(items)} Meldungen, {n_fresh} neu, {posted} gepostet.")


if __name__ == "__main__":
    asyncio.run(main("--dry-run" in sys.argv))
