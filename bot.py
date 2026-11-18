"""
Finanz-News Discord Bot
-----------------------
Liest Finanz-RSS-Feeds (feeds.json), und postet neue Meldungen als ausfuehrliche
Embeds (Titel, Zusammenfassung, Bild, Zeit, Autor, Themen, Ticker, Quelle + Link)
in einen Discord-Channel.

Slash-Commands:
  /news [anzahl] [quelle]   - aktuellste Meldungen anzeigen
  /quellen                  - alle konfigurierten Quellen auflisten
  /setchannel <kanal>       - Channel fuer automatische News festlegen (Admin)
  /jetzt                    - sofort nach neuen News suchen (Admin)
  /status                   - Bot-Status anzeigen
"""

from __future__ import annotations

import asyncio
import calendar
import html
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
import discord
import feedparser
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

# --------------------------------------------------------------------------- #
# Konfiguration
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DEFAULT_CHANNEL_ID = int(os.getenv("NEWS_CHANNEL_ID") or 0)
# Channel-Name, der automatisch auf jedem Server gesucht wird (falls kein /setchannel gesetzt)
DEFAULT_CHANNEL_NAME = os.getenv("NEWS_CHANNEL_NAME", "").strip().lstrip("#").lower()
POLL_MINUTES = max(1, int(os.getenv("POLL_INTERVAL_MINUTES") or 10))
MAX_POSTS = max(1, int(os.getenv("MAX_POSTS_PER_CYCLE") or 5))
KEYWORDS = [k.strip().lower() for k in os.getenv("KEYWORDS", "").split(",") if k.strip()]
ENRICH = os.getenv("ENRICH_ARTICLES", "true").strip().lower() in ("1", "true", "yes", "ja")
MAX_AGE = timedelta(hours=24)  # aeltere Meldungen werden nie automatisch gepostet

FEEDS_FILE = BASE_DIR / "feeds.json"
STATE_FILE = BASE_DIR / "state.json"
USER_AGENT = "Mozilla/5.0 (compatible; FinanzNewsBot/1.0; +https://discord.com)"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("finanznews")


# --------------------------------------------------------------------------- #
# Datenmodell
# --------------------------------------------------------------------------- #
@dataclass
class Feed:
    name: str
    url: str
    lang: str = "de"
    color: int = 0x2B6CB0

    @property
    def homepage(self) -> str:
        p = urlparse(self.url)
        return f"{p.scheme}://{p.netloc}"


@dataclass
class NewsItem:
    uid: str
    title: str
    link: str
    summary: str
    published: datetime | None
    feed: Feed
    author: str = ""
    tags: list[str] = field(default_factory=list)
    image: str = ""

    @property
    def domain(self) -> str:
        return urlparse(self.link).netloc.removeprefix("www.")


def load_feeds() -> list[Feed]:
    raw = json.loads(FEEDS_FILE.read_text(encoding="utf-8"))
    feeds = []
    for f in raw:
        color = f.get("color", "#2B6CB0")
        feeds.append(Feed(f["name"], f["url"], f.get("lang", "de"), int(color.lstrip("#"), 16)))
    return feeds


# --------------------------------------------------------------------------- #
# Persistenter Zustand (bereits gepostete IDs, Channel pro Server)
# --------------------------------------------------------------------------- #
class State:
    MAX_SEEN = 5000

    def __init__(self, path: Path):
        self.path = path
        self.seen: list[str] = []
        self.channels: dict[str, int] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                self.seen = data.get("seen", [])
                self.channels = {k: int(v) for k, v in data.get("channels", {}).items()}
            except (json.JSONDecodeError, OSError) as e:
                log.warning("state.json konnte nicht gelesen werden (%s) - starte neu.", e)
        self._seen_set = set(self.seen)

    def is_seen(self, uid: str) -> bool:
        return uid in self._seen_set

    def mark_seen(self, uid: str) -> None:
        if uid not in self._seen_set:
            self.seen.append(uid)
            self._seen_set.add(uid)

    def save(self) -> None:
        self.seen = self.seen[-self.MAX_SEEN:]
        self._seen_set = set(self.seen)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"seen": self.seen, "channels": self.channels}, indent=1), encoding="utf-8")
        tmp.replace(self.path)


# --------------------------------------------------------------------------- #
# Hilfsfunktionen: HTML saeubern, Ticker erkennen, Artikel anreichern
# --------------------------------------------------------------------------- #
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
TICKER_RES = [
    re.compile(r"\$([A-Z]{1,5})\b"),
    re.compile(r"\((?:NYSE|NASDAQ|Nasdaq|XETRA|ETR|FRA|TSX|LSE|AMEX)\s*:\s*([A-Z0-9.]{1,8})\)"),
]


def clean_text(raw: str) -> str:
    text = TAG_RE.sub(" ", raw or "")
    text = html.unescape(text)
    return WS_RE.sub(" ", text).strip()


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0]
    return cut + "…"


def find_tickers(*texts: str) -> list[str]:
    found: list[str] = []
    for t in texts:
        for rx in TICKER_RES:
            for m in rx.findall(t or ""):
                if m not in found:
                    found.append(m)
    return found[:8]


def meta_content(page: str, *names: str) -> str:
    for name in names:
        for pattern in (
            rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(name)}["\']',
        ):
            m = re.search(pattern, page, re.IGNORECASE)
            if m:
                return html.unescape(m.group(1)).strip()
    return ""


def entry_image(entry) -> str:
    for key in ("media_content", "media_thumbnail"):
        for media in entry.get(key, []) or []:
            url = media.get("url", "")
            if url.startswith("http"):
                return url
    for enc in entry.get("enclosures", []) or []:
        if enc.get("type", "").startswith("image") and enc.get("href", "").startswith("http"):
            return enc["href"]
    m = re.search(r'<img[^>]+src=["\'](http[^"\']+)["\']', entry.get("summary", "") or "")
    return m.group(1) if m else ""


def entry_published(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc)
    return None


# --------------------------------------------------------------------------- #
# Feeds abrufen
# --------------------------------------------------------------------------- #
async def fetch_feed(session: aiohttp.ClientSession, feed: Feed) -> list[NewsItem]:
    try:
        async with session.get(feed.url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                log.warning("%s: HTTP %s", feed.name, resp.status)
                return []
            body = await resp.read()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.warning("%s: Abruf fehlgeschlagen (%s)", feed.name, e)
        return []

    parsed = await asyncio.to_thread(feedparser.parse, body)
    items: list[NewsItem] = []
    for e in parsed.entries:
        link = e.get("link", "")
        title = clean_text(e.get("title", ""))
        if not link or not title:
            continue
        summary = clean_text(e.get("summary", "") or e.get("description", ""))
        if summary == title:
            summary = ""
        tags = [clean_text(t.get("term", "")) for t in e.get("tags", []) or [] if t.get("term")]
        items.append(
            NewsItem(
                uid=e.get("id") or link,
                title=title,
                link=link,
                summary=summary,
                published=entry_published(e),
                feed=feed,
                author=clean_text(e.get("author", "")),
                tags=[t for t in tags if t][:5],
                image=entry_image(e),
            )
        )
    return items


async def fetch_all(session: aiohttp.ClientSession, feeds: list[Feed]) -> list[NewsItem]:
    results = await asyncio.gather(*(fetch_feed(session, f) for f in feeds))
    items = [i for r in results for i in r]
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    items.sort(key=lambda i: i.published or epoch, reverse=True)
    return items


async def enrich(session: aiohttp.ClientSession, item: NewsItem) -> None:
    """Holt Vorschaubild + ausfuehrlichere Beschreibung von der Artikelseite (best effort)."""
    if not ENRICH:
        return
    try:
        async with session.get(item.link, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200 or "html" not in resp.headers.get("Content-Type", ""):
                return
            page = (await resp.content.read(600_000)).decode("utf-8", errors="ignore")
    except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError):
        return
    if not item.image:
        img = meta_content(page, "og:image", "twitter:image")
        if img.startswith("http"):
            item.image = img
    desc = clean_text(meta_content(page, "og:description", "description", "twitter:description"))
    if len(desc) > len(item.summary):
        item.summary = desc
    if not item.author:
        item.author = clean_text(meta_content(page, "author", "article:author"))
        if item.author.startswith("http"):
            item.author = ""


# --------------------------------------------------------------------------- #
# Embed bauen
# --------------------------------------------------------------------------- #
def build_message(item: NewsItem) -> tuple[discord.Embed, discord.ui.View]:
    desc = truncate(item.summary, 1500) if item.summary else "*Keine Zusammenfassung verfügbar.*"
    desc += f"\n\n**[➜ Vollständigen Artikel lesen]({item.link})**"

    embed = discord.Embed(
        title=truncate(item.title, 256),
        url=item.link,
        description=desc,
        color=item.feed.color,
        timestamp=item.published or datetime.now(timezone.utc),
    )
    embed.set_author(name=item.feed.name, url=item.feed.homepage)

    embed.add_field(name="📰 Quelle", value=f"[{item.feed.name}]({item.link})\n`{item.domain}`", inline=True)
    if item.published:
        ts = int(item.published.timestamp())
        embed.add_field(name="🕒 Veröffentlicht", value=f"<t:{ts}:f>\n(<t:{ts}:R>)", inline=True)
    embed.add_field(name="🌐 Sprache", value="Deutsch" if item.feed.lang == "de" else "Englisch", inline=True)

    if item.author:
        embed.add_field(name="✍️ Autor", value=truncate(item.author, 200), inline=True)
    if item.tags:
        embed.add_field(name="🏷️ Themen", value=truncate(", ".join(item.tags), 300), inline=True)
    tickers = find_tickers(item.title, item.summary)
    if tickers:
        embed.add_field(name="📈 Erwähnte Ticker", value=" ".join(f"`{t}`" for t in tickers), inline=True)

    if item.image:
        embed.set_image(url=item.image)
    embed.set_footer(text=f"Quelle: {item.domain} • Keine Anlageberatung")

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Zum Artikel", url=item.link, emoji="🔗"))
    view.add_item(discord.ui.Button(label=item.feed.name[:80], url=item.feed.homepage))
    return embed, view


def matches_keywords(item: NewsItem) -> bool:
    if not KEYWORDS:
        return True
    hay = f"{item.title} {item.summary} {' '.join(item.tags)}".lower()
    return any(k in hay for k in KEYWORDS)


def select_to_post(items: list[NewsItem], state: State) -> tuple[list[NewsItem], int]:
    """Markiert alle neuen Meldungen als gesehen und gibt die zu postenden (aelteste zuerst) zurueck."""
    now = datetime.now(timezone.utc)
    fresh = [i for i in items if not state.is_seen(i.uid)]
    for i in fresh:  # alle als gesehen markieren -> kein veralteter Rueckstau
        state.mark_seen(i.uid)
    to_post = [
        i for i in fresh
        if (i.published is None or now - i.published <= MAX_AGE) and matches_keywords(i)
    ][:MAX_POSTS]
    to_post.reverse()  # chronologisch: aelteste zuerst
    return to_post, len(fresh)


# --------------------------------------------------------------------------- #
# Bot
# --------------------------------------------------------------------------- #
class NewsBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.feeds = load_feeds()
        self.state = State(STATE_FILE)
        self.session: aiohttp.ClientSession | None = None
        self.cache: list[NewsItem] = []
        self.last_check: datetime | None = None
        self.lock = asyncio.Lock()

    async def setup_hook(self) -> None:
        self.session = aiohttp.ClientSession(headers={"User-Agent": USER_AGENT})
        register_commands(self)
        synced = await self.tree.sync()
        log.info("%d Slash-Commands synchronisiert.", len(synced))
        self.poll.change_interval(minutes=POLL_MINUTES)
        self.poll.start()

    async def close(self) -> None:
        if self.session:
            await self.session.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info("Eingeloggt als %s (ID %s) auf %d Server(n).", self.user, self.user.id, len(self.guilds))
        for g in self.guilds:
            log.info("  Server: %s", g.name)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info("Neuem Server beigetreten: %s - suche sofort nach News.", guild.name)
        await self.check_news()

    # ---- Ziel-Channels ---------------------------------------------------- #
    def target_channels(self) -> list[discord.abc.Messageable]:
        ids = set(self.state.channels.values())
        if DEFAULT_CHANNEL_ID:
            ids.add(DEFAULT_CHANNEL_ID)
        if DEFAULT_CHANNEL_NAME:
            for g in self.guilds:
                if str(g.id) in self.state.channels:
                    continue  # per /setchannel festgelegt
                # exakter Name bevorzugt, sonst Channel der den Namen enthaelt (z. B. "📈stonks")
                ch = discord.utils.find(lambda c: c.name.lower() == DEFAULT_CHANNEL_NAME, g.text_channels) \
                    or discord.utils.find(lambda c: DEFAULT_CHANNEL_NAME in c.name.lower(), g.text_channels)
                if ch:
                    ids.add(ch.id)
                else:
                    log.warning("Server %s: kein Channel #%s gefunden. Vorhandene Text-Channels: %s",
                                g.name, DEFAULT_CHANNEL_NAME, ", ".join(c.name for c in g.text_channels))
        channels = []
        for cid in ids:
            ch = self.get_channel(cid)
            if ch is None:
                log.warning("Channel %s nicht gefunden (Bot nicht auf dem Server / keine Rechte?).", cid)
            else:
                channels.append(ch)
        return channels

    # ---- Hauptschleife ---------------------------------------------------- #
    @tasks.loop(minutes=10)
    async def poll(self) -> None:
        await self.check_news()

    @poll.before_loop
    async def before_poll(self) -> None:
        await self.wait_until_ready()

    async def check_news(self) -> int:
        async with self.lock:
            items = await fetch_all(self.session, self.feeds)
            self.cache = items
            self.last_check = datetime.now(timezone.utc)

            channels = self.target_channels()
            if not channels:
                # Nichts als gesehen markieren, damit beim ersten Channel sofort gepostet wird
                log.info("Kein Channel gefunden - Bot einladen, NEWS_CHANNEL_NAME pruefen oder /setchannel nutzen.")
                return 0

            to_post, n_fresh = select_to_post(items, self.state)

            posted = 0
            for item in to_post:
                await enrich(self.session, item)
                embed, view = build_message(item)
                for ch in channels:
                    try:
                        await ch.send(embed=embed, view=view)
                    except discord.HTTPException as e:
                        log.warning("Senden an #%s fehlgeschlagen: %s", ch, e)
                posted += 1
                await asyncio.sleep(1.5)  # Rate-Limits schonen

            self.state.save()
            log.info("Check fertig: %d Meldungen, %d neu, %d gepostet.", len(items), n_fresh, posted)
            return posted


# --------------------------------------------------------------------------- #
# Slash-Commands
# --------------------------------------------------------------------------- #
def register_commands(bot: NewsBot) -> None:
    tree = bot.tree

    async def source_autocomplete(_: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=f.name, value=f.name)
            for f in bot.feeds if current.lower() in f.name.lower()
        ][:25]

    @tree.command(name="news", description="Zeigt die aktuellsten Finanz-News mit Details und Quellen.")
    @app_commands.describe(anzahl="Wie viele Meldungen (1-10)", quelle="Nur eine bestimmte Quelle")
    @app_commands.autocomplete(quelle=source_autocomplete)
    async def news(interaction: discord.Interaction, anzahl: app_commands.Range[int, 1, 10] = 3, quelle: str | None = None):
        await interaction.response.defer(thinking=True)
        if not bot.cache:
            bot.cache = await fetch_all(bot.session, bot.feeds)
        items = bot.cache
        if quelle:
            items = [i for i in items if i.feed.name.lower() == quelle.lower()]
        items = items[:anzahl]
        if not items:
            await interaction.followup.send("Keine Meldungen gefunden.")
            return
        for item in items:
            await enrich(bot.session, item)
            embed, view = build_message(item)
            await interaction.followup.send(embed=embed, view=view)

    @tree.command(name="quellen", description="Listet alle News-Quellen des Bots auf.")
    async def quellen(interaction: discord.Interaction):
        lines = [f"• [{f.name}]({f.homepage}) — {'🇩🇪' if f.lang == 'de' else '🇺🇸'} `{urlparse(f.url).netloc}`" for f in bot.feeds]
        embed = discord.Embed(title="📚 News-Quellen", description="\n".join(lines), color=0x2B6CB0)
        embed.set_footer(text="Alle Meldungen stammen aus den öffentlichen RSS-Feeds dieser Medien.")
        await interaction.response.send_message(embed=embed)

    @tree.command(name="setchannel", description="Legt fest, in welchen Channel automatisch News gepostet werden.")
    @app_commands.describe(kanal="Ziel-Channel für Finanz-News")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def setchannel(interaction: discord.Interaction, kanal: discord.TextChannel):
        perms = kanal.permissions_for(interaction.guild.me)
        if not (perms.send_messages and perms.embed_links):
            await interaction.response.send_message(
                f"❌ Mir fehlen in {kanal.mention} die Rechte **Nachrichten senden** und/oder **Links einbetten**.",
                ephemeral=True,
            )
            return
        bot.state.channels[str(interaction.guild_id)] = kanal.id
        bot.state.save()
        await interaction.response.send_message(
            f"✅ Finanz-News werden ab jetzt alle {POLL_MINUTES} Minuten in {kanal.mention} gepostet.", ephemeral=True
        )

    @tree.command(name="jetzt", description="Sucht sofort nach neuen Finanz-News.")
    @app_commands.default_permissions(manage_guild=True)
    async def jetzt(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        posted = await bot.check_news()
        await interaction.followup.send(f"🔄 Fertig – {posted} neue Meldung(en) gepostet.", ephemeral=True)

    @tree.command(name="status", description="Zeigt den Status des News-Bots.")
    async def status(interaction: discord.Interaction):
        cid = bot.state.channels.get(str(interaction.guild_id)) or DEFAULT_CHANNEL_ID
        last = f"<t:{int(bot.last_check.timestamp())}:R>" if bot.last_check else "noch nie"
        embed = discord.Embed(title="📊 Finanz-News-Bot Status", color=0x2B6CB0)
        embed.add_field(name="Channel", value=f"<#{cid}>" if cid else "nicht gesetzt", inline=True)
        embed.add_field(name="Intervall", value=f"{POLL_MINUTES} Min.", inline=True)
        embed.add_field(name="Letzter Check", value=last, inline=True)
        embed.add_field(name="Quellen", value=str(len(bot.feeds)), inline=True)
        embed.add_field(name="Max. Posts/Durchlauf", value=str(MAX_POSTS), inline=True)
        embed.add_field(name="Filter", value=", ".join(KEYWORDS) if KEYWORDS else "keiner", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    if not TOKEN or TOKEN == "dein_bot_token_hier":
        raise SystemExit("❌ DISCORD_TOKEN fehlt. Kopiere .env.example nach .env und trage deinen Token ein.")
    NewsBot().run(TOKEN, log_handler=None)
