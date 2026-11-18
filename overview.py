"""
Aktien-Ueberblick: buendelt die Finanz-News nach Aktien und baut daraus Discord-Embeds.

Ablauf:
  1. Alle Meldungen der letzten Stunden aus den Feeds (feeds.json) nehmen.
  2. Erkennen, welche Firmen aus aktien.json darin vorkommen.
  3. Typische Schlagworte erkennen (z. B. "Prognose angehoben", "Gewinnwarnung").
  4. Pro Aktie eine Ampel bilden: rein regelbasiert, keine KI, keine eigene Bewertung.
  5. Kurse fuer die ausgewaehlten Aktien bei Yahoo Finance abfragen.
  6. Embeds bauen, die in Discord-Nachrichten passen (Discord-Limits beachten).
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp

import bot  # NewsItem, clean_text, truncate

BASE_DIR = Path(__file__).resolve().parent
STOCKS_FILE = BASE_DIR / "aktien.json"
BERLIN = ZoneInfo("Europe/Berlin")

MAX_STOCKS = 6            # hoechstens so viele Aktien pro Ueberblick
MAX_ARTICLES = 5          # hoechstens so viele Artikel pro Aktie
TEASER_CHARS = 150        # Vorspann aus dem Artikel: nur ein sehr kurzer Auszug (Presse-Leistungsschutzrecht)
TITLE_CHARS = 120

# Discord-Limits: max. 10 Embeds und 6000 Zeichen pro Nachricht (Puffer eingeplant)
MSG_MAX_EMBEDS = 10
MSG_MAX_CHARS = 5800

COLOR_HEADER = 0x1F6FB2
COLOR_GREEN = 0x2E9E5B
COLOR_YELLOW = 0xE0B020
COLOR_RED = 0xD9443A
COLOR_NOTE = 0x6B7280

DISCLAIMER = (
    "Alle Informationen dienen ausschließlich zu Informations- und Bildungszwecken. "
    "Die dargestellten Einschätzungen stellen weder eine Kauf- noch eine Verkaufsempfehlung dar. "
    "Aktieninvestitionen sind mit Risiken verbunden und können zum Verlust des eingesetzten Kapitals führen. "
    "Jeder ist selbst für seine Anlageentscheidungen verantwortlich."
)
METHOD_NOTE = (
    "Die Einstufung 🟢🟡🔴 wird automatisch aus Schlagworten in den verlinkten Artikeln ermittelt "
    "(z. B. „Prognose angehoben“, „Gewinnwarnung“). Sie beschreibt die Nachrichtenlage, ist keine "
    "persönliche Bewertung der Aktie und kann sich bei ungewöhnlichen Formulierungen irren. "
    "Maßgeblich sind immer die Originalartikel."
)
STOCK_FOOTER = "⚠️ Keine Anlageberatung · Einstufung automatisch aus Schlagworten · Hinweis am Ende"

# --------------------------------------------------------------------------- #
# Schlagworte (Signale). Gross-/Kleinschreibung egal. Deutsch + Englisch.
# --------------------------------------------------------------------------- #
POSITIVE: dict[str, str] = {
    "Prognose angehoben": r"prognose\w* (?:\w+ )?(?:angehoben|erhöht|hochgeschraubt)|(?:hebt|erhöht|schraubt) .{0,40}(?:prognose|ausblick|jahresziel)|rais(?:es|ed|ing) (?:its |full-year |annual )?(?:guidance|forecast|outlook)|(?:lifts?|boosts?|ups) (?:its |full-year |annual )?(?:guidance|forecast|outlook)|(?:guidance|outlook|forecast) raised",
    "Zahlen über Erwartungen": r"übertr(?:ifft|ifft die|af) .{0,20}erwartungen|über (?:den )?erwartungen|besser als erwartet|stärker als erwartet|beats? .{0,25}(?:estimates|expectations|forecasts)|tops? .{0,25}(?:estimates|expectations)|better[- ]than[- ]expected|stronger[- ]than[- ]expected|above (?:analyst )?expectations",
    "Rekordergebnis": r"rekord(?:umsatz|gewinn|ergebnis|quartal|jahr|auftragseingang|auftragsbestand)|record (?:revenue|profit|sales|quarter|earnings|results)",
    "Großauftrag": r"großauftrag|grossauftrag|milliardenauftrag|rekordauftrag|auftrag (?:über|im wert)|(?:erhält|gewinnt|ergattert|sichert sich) .{0,40}auftrag|(?:wins?|won|secures?|lands?|awarded) .{0,30}(?:contract|order)|(?:billion|major|large)[- ](?:dollar )?(?:contract|order)",
    "Kursziel erhöht": r"kursziel (?:\w+ )?(?:angehoben|erhöht|hochgesetzt)|(?:hebt|erhöht) .{0,40}kursziel|rais(?:es|ed) .{0,30}price target|price target (?:raised|hiked|increased)|(?:lifts?|ups|boosts?) .{0,25}price target",
    "Hochstufung durch Analysten": r"hochgestuft|hochstufung|stuft .{0,40} hoch\b|kaufempfehlung|\bupgraded\b|upgrades? .{0,40}to (?:buy|outperform|overweight)|analyst upgrade",
    "Partnerschaft / Kooperation": r"partnerschaft|kooperation|zusammenarbeit mit|partners? with|partnership|teams? up with|strategic alliance",
    "Zulassung erhalten": r"zulassung (?:erteilt|erhalten)|(?:erhält|bekommt) .{0,30}zulassung|grünes licht|fda (?:approv|clear|nod)|(?:wins?|receives?|gets?) .{0,25}approval|ema empfiehlt",
    "Neues Produkt / Technologie": r"stellt .{0,30}neue[snrm]? .{0,30} vor\b|präsentiert .{0,30}neue|neue[snrm]? (?:produkt|modell|chip|plattform|medikament|technologie)|unveil(?:s|ed)|launch(?:es|ed) (?:a |its |new )|introduces? new",
    "Aktienrückkauf / Dividende": r"aktienrückkauf|rückkaufprogramm|sonderdividende|(?:erhöht|steigert) .{0,20}dividende|dividende (?:erhöht|angehoben)|buyback|share repurchase|special dividend|rais(?:es|ed) .{0,20}dividend|dividend (?:hike|increase)",
    "Staatliche Förderung / Politik": r"fördermittel|förderung|förderzusage|subvention|staatshilfe|sondervermögen|rüstungsausgaben|verteidigungsausgaben|government (?:contract|funding|grant|support)|subsid(?:y|ies)|chips act|defen[cs]e spending",
    "Starke Nachfrage": r"starke nachfrage|hohe nachfrage|nachfrage boomt|strong demand|robust demand|surging demand|demand boom",
}

NEGATIVE: dict[str, str] = {
    "Prognose gesenkt / Gewinnwarnung": r"gewinnwarnung|umsatzwarnung|prognose\w* (?:\w+ )?(?:gesenkt|gekappt|kassiert|zurückgenommen)|(?:senkt|kappt|kassiert|streicht) .{0,40}(?:prognose|ausblick|jahresziel)|profit warning|(?:cuts?|lowers?|slashes?|trims?) (?:its |full-year |annual )?(?:guidance|forecast|outlook)|(?:guidance|outlook|forecast) (?:cut|lowered|slashed)|warns? (?:on|of) (?:profit|earnings|sales|revenue)",
    "Zahlen unter Erwartungen": r"verfehlt .{0,25}erwartungen|unter (?:den )?erwartungen|schlechter als erwartet|schwächer als erwartet|enttäusch|miss(?:es|ed)? .{0,25}(?:estimates|expectations|forecasts)|falls? short of|worse[- ]than[- ]expected|weaker[- ]than[- ]expected|below (?:analyst )?expectations|disappoint",
    "Verluste": r"rote zahlen|tiefrot|in die verlustzone|verlust ausgeweitet|verlust (?:wächst|steigt)|swings? to (?:a )?loss|loss widen|wider loss",
    "Kursziel gesenkt": r"kursziel (?:\w+ )?(?:gesenkt|reduziert|gekappt)|(?:senkt|kappt|reduziert) .{0,40}kursziel|(?:cuts?|lowers?|slashes?|trims?) .{0,30}price target|price target (?:cut|lowered|reduced|slashed)",
    "Herabstufung durch Analysten": r"abgestuft|herabgestuft|abstufung|stuft .{0,40} ab\b|verkaufsempfehlung|downgrade[sd]?\b|downgraded to (?:sell|underperform|underweight|neutral|hold)",
    "Klage / Ermittlungen": r"\bklage\b|verklagt|ermittlung|razzia|staatsanwalt|kartellstrafe|bußgeld|strafzahlung|lawsuit|\bsued\b|\bsues\b|\bprobes? (?:into|of)\b|launch(?:es|ed)? (?:a |an )?probe|investigation|antitrust|\bfined\b|penalty|subpoena",
    "Stellenabbau / Sparprogramm": r"stellenabbau|jobabbau|arbeitsplatzabbau|(?:streicht|abbau von) .{0,30}(?:stellen|jobs|arbeitsplätze)|entlassung|kurzarbeit|sparprogramm|layoffs?|job cuts|cuts? .{0,20}jobs|restructuring|restrukturierung",
    "Rückruf / Produktionsprobleme": r"rückruf|produktionsstopp|lieferprobleme|lieferengp|recall|production halt|supply (?:problems|issues|disruption)",
    "Zölle / Exportbeschränkungen": r"strafzoll|zölle|zollstreit|exportbeschränkung|exportverbot|exportkontrolle|sanktion|tariffs?|export (?:curbs|ban|restrictions|controls)|sanctions",
    "Insolvenz / Finanzierungsprobleme": r"insolvenz|pleite|zahlungsunfähig|kapitalerhöhung|bankruptcy|chapter 11|going concern|share offering|dilution",
    "Rücktritt im Management": r"tritt zurück|rücktritt|(?:ceo|chef|vorstandschef) (?:geht|verlässt|muss gehen)|(?:ceo|cfo|chief executive) (?:resigns|steps down|ousted|exits|departs)",
}

# Werbe-/Reisser-Artikel ("Hot Stock", "Kursexplosion" ...) werden komplett ignoriert
PROMO_RE = re.compile(
    r"hot[ -]?stock|kursexplosion|kursrakete|vervielfach|verzehnfach|verhundertfach|geheimtipp|\banzeige\b|"
    r"sponsored|werbung|advertorial|penny[ -]?stock|jetzt kaufen|\b\d{3,4}\s?%|top[- ]pick|millionär|"
    r"diese aktie (?:könnte|wird)|before it'?s too late|could make you rich",
    re.IGNORECASE,
)

POS_RES = {k: re.compile(v, re.IGNORECASE) for k, v in POSITIVE.items()}
NEG_RES = {k: re.compile(v, re.IGNORECASE) for k, v in NEGATIVE.items()}

# Banken, die haeufig als Analysten-Haeuser zitiert werden
BROKERS = {"GS", "MS", "JPM", "BAC", "C", "WFC", "UBSG.SW", "DBK.DE", "BARC.L", "HSBA.L", "BNP.PA", "CBK.DE"}

WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


# --------------------------------------------------------------------------- #
# Aktienliste + Erkennung von Firmennamen
# --------------------------------------------------------------------------- #
@dataclass
class Stock:
    name: str
    ticker: str
    patterns: dict[str, list[tuple[str, re.Pattern]]]  # lang -> [(alias, regex)]
    exclude: list[re.Pattern]


def _alias_re(alias: str) -> re.Pattern:
    # Gross-/Kleinschreibung beachten; Wortgrenzen, aber "Siemens-Aktie" oder "Nvidia's" erlauben
    return re.compile(rf"(?<![\w&+-]){re.escape(alias)}(?![\w&+])")


def load_stocks() -> list[Stock]:
    raw = json.loads(STOCKS_FILE.read_text(encoding="utf-8"))
    stocks = []
    for s in raw:
        common = s.get("aliases", [])
        pats = {
            "de": [(a, _alias_re(a)) for a in common + s.get("aliases_de", [])],
            "en": [(a, _alias_re(a)) for a in common + s.get("aliases_en", [])],
        }
        excl = [re.compile(re.escape(e)) for e in s.get("exclude", [])]
        stocks.append(Stock(s["name"], s["ticker"], pats, excl))
    return stocks


def find_stocks(text: str, lang: str, stocks: list[Stock]) -> list[Stock]:
    """Findet Firmen im Text. Laengere Namen gewinnen ("Siemens Energy" ist nicht "Siemens")."""
    lang = "en" if lang == "en" else "de"
    candidates: list[tuple[int, Stock, re.Pattern]] = []
    for st in stocks:
        for alias, rx in st.patterns[lang]:
            candidates.append((len(alias), st, rx))
    candidates.sort(key=lambda c: -c[0])

    taken: list[tuple[int, int]] = []
    found: list[Stock] = []
    for _, st, rx in candidates:
        t = text
        for ex in st.exclude:
            t = ex.sub(lambda m: " " * len(m.group(0)), t)
        for m in rx.finditer(t):
            a, b = m.span()
            if any(a < y and x < b for x, y in taken):
                continue
            taken.append((a, b))
            if st not in found:
                found.append(st)
    return found


def find_signals(text: str) -> tuple[list[str], list[str]]:
    pos = [label for label, rx in POS_RES.items() if rx.search(text)]
    neg = [label for label, rx in NEG_RES.items() if rx.search(text)]
    return pos, neg


def is_promo(item: bot.NewsItem) -> bool:
    return bool(PROMO_RE.search(item.title) or PROMO_RE.search(item.summary[:300]))


# --------------------------------------------------------------------------- #
# Datenmodell fuer den Ueberblick
# --------------------------------------------------------------------------- #
@dataclass
class Article:
    item: bot.NewsItem
    pos: list[str] = field(default_factory=list)
    neg: list[str] = field(default_factory=list)
    new: bool = True  # noch in keinem frueheren Ueberblick verwendet

    @property
    def has_signal(self) -> bool:
        return bool(self.pos or self.neg)


@dataclass
class Quote:
    price: float
    change_pct: float | None
    currency: str
    time: datetime | None


@dataclass
class StockReport:
    stock: Stock
    articles: list[Article] = field(default_factory=list)  # bereits ausgewaehlt + nummeriert
    pos: dict[str, list[int]] = field(default_factory=dict)  # Signal -> Artikelnummern (1-basiert)
    neg: dict[str, list[int]] = field(default_factory=dict)
    quote: Quote | None = None

    @property
    def rating(self) -> str:
        p, n = len(self.pos), len(self.neg)
        if p >= 2 and n == 0:
            return "green"
        if n > p:
            return "red"
        return "yellow"

    def sources_for(self, numbers: list[int]) -> int:
        return len({self.articles[i - 1].item.feed.name for i in numbers})


def _norm_title(t: str) -> str:
    return re.sub(r"\W+", " ", t.lower()).strip()


def build_reports(
    items: list[bot.NewsItem],
    stocks: list[Stock],
    seen: set[str],
    window: timedelta,
    now: datetime | None = None,
) -> list[StockReport]:
    """Ordnet Meldungen den Aktien zu, erkennt Signale und waehlt die interessantesten Aktien aus."""
    now = now or datetime.now(timezone.utc)
    per_stock: dict[str, tuple[Stock, list[Article]]] = {}
    seen_titles: set[str] = set()

    for it in items:
        if it.published and now - it.published > window:
            continue
        if is_promo(it):
            continue
        key = _norm_title(it.title)
        if key in seen_titles:  # gleiche Meldung in zwei Feeds -> nur einmal
            continue
        seen_titles.add(key)

        text = f"{it.title}. {it.summary}"
        in_title = find_stocks(it.title, it.feed.lang, stocks)
        in_text = find_stocks(text, it.feed.lang, stocks)
        if not in_text:
            continue
        pos, neg = find_signals(text)
        # Banken, die oft nur als Analysten-Haus genannt werden ("upgraded at Morgan Stanley"),
        # bekommen keine Signale, wenn noch eine andere Firma in der Meldung steht
        owners = [s for s in in_title if s.ticker not in BROKERS] or in_title
        if not owners and len(in_text) == 1:
            owners = in_text
        for st in in_text:
            # Signale nur zuordnen, wenn die Meldung eindeutig von dieser Firma handelt
            own = st in owners
            art = Article(it, pos if own else [], neg if own else [], new=it.uid not in seen)
            per_stock.setdefault(st.ticker, (st, []))[1].append(art)

    reports: list[StockReport] = []
    for st, arts in per_stock.values():
        # Nur Aktien mit mindestens einem Signal aus einer NEUEN Meldung
        if not any(a.has_signal and a.new for a in arts):
            continue
        rep = StockReport(st)
        rep.articles = _pick_articles(arts)
        for idx, a in enumerate(rep.articles, start=1):
            for s in a.pos:
                rep.pos.setdefault(s, []).append(idx)
            for s in a.neg:
                rep.neg.setdefault(s, []).append(idx)
        if rep.pos or rep.neg:
            reports.append(rep)

    order = {"green": 0, "yellow": 1, "red": 2}
    reports.sort(key=lambda r: (
        order[r.rating],
        -(len(r.pos) + len(r.neg)),
        -len({a.item.feed.name for a in r.articles}),
    ))
    return reports[:MAX_STOCKS]


def _pick_articles(arts: list[Article]) -> list[Article]:
    """Bis zu MAX_ARTICLES Artikel, Signal-Artikel zuerst, moeglichst aus verschiedenen Medien."""
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    ranked = sorted(arts, key=lambda a: (not a.has_signal, -(a.item.published or epoch).timestamp()))
    picked: list[Article] = []
    used_feeds: set[str] = set()
    for a in ranked:  # erste Runde: jedes Medium nur einmal
        if a.item.feed.name not in used_feeds:
            picked.append(a)
            used_feeds.add(a.item.feed.name)
        if len(picked) == MAX_ARTICLES:
            break
    for a in ranked:  # zweite Runde: auffuellen
        if len(picked) == MAX_ARTICLES:
            break
        if a not in picked:
            picked.append(a)
    picked.sort(key=lambda a: (not a.has_signal, -(a.item.published or epoch).timestamp()))
    return picked


# --------------------------------------------------------------------------- #
# Kurse (Yahoo Finance, inoffiziell, ca. 15 Min. verzoegert)
# --------------------------------------------------------------------------- #
QUOTE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d"


async def fetch_quote(session: aiohttp.ClientSession, ticker: str) -> Quote | None:
    try:
        async with session.get(QUOTE_URL.format(sym=ticker), timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                bot.log.warning("Kurs %s: HTTP %s", ticker, r.status)
                return None
            data = await r.json(content_type=None)
        meta = data["chart"]["result"][0]["meta"]
        price = float(meta["regularMarketPrice"])
        prev = meta.get("previousClose") or meta.get("chartPreviousClose")
        cur = meta.get("currency") or ""
        if cur == "GBp":  # Londoner Kurse in Pence -> Pfund
            price /= 100
            prev = prev / 100 if prev else prev
            cur = "GBP"
        change = (price / float(prev) - 1) * 100 if prev else None
        ts = meta.get("regularMarketTime")
        when = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
        return Quote(price, change, cur, when)
    except (aiohttp.ClientError, asyncio.TimeoutError, KeyError, IndexError, TypeError, ValueError) as e:
        bot.log.warning("Kurs %s nicht verfuegbar (%s)", ticker, e)
        return None


async def add_quotes(session: aiohttp.ClientSession, reports: list[StockReport]) -> None:
    quotes = await asyncio.gather(*(fetch_quote(session, r.stock.ticker) for r in reports))
    for r, q in zip(reports, quotes):
        r.quote = q


# --------------------------------------------------------------------------- #
# Formatierung
# --------------------------------------------------------------------------- #
CURRENCY = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF", "DKK": "DKK", "SEK": "SEK", "NOK": "NOK"}


def fmt_num(x: float, digits: int = 2) -> str:
    return f"{x:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_quote(q: Quote, now: datetime) -> str:
    sym = CURRENCY.get(q.currency, q.currency)
    price = f"{fmt_num(q.price)} {sym}"
    line = f"💶 **{price}**" if q.currency == "EUR" else f"💵 **{price}**"
    if q.change_pct is not None:
        arrow = "▲" if q.change_pct > 0.005 else "▼" if q.change_pct < -0.005 else "▶"
        sign = "+" if q.change_pct > 0 else ""
        stale = q.time is not None and now - q.time > timedelta(hours=14)
        label = "zum Vortag (letzter Handelstag)" if stale else "heute"
        line += f"  {arrow} {sign}{fmt_num(q.change_pct)} % {label}"
    stand = f" · Stand <t:{int(q.time.timestamp())}:f>" if q.time else ""
    return f"{line}\n-# Kursdaten: Yahoo Finance{stand} (ca. 15 Min. verzögert)"


def refs(numbers: list[int]) -> str:
    return "".join(f"[{n}]" for n in numbers)


def signal_lines(sig: dict[str, list[int]], rep: StockReport) -> str:
    if not sig:
        return "keine erkannt"
    lines = []
    for label, nums in sig.items():
        n_src = rep.sources_for(nums)
        extra = f" – von {n_src} Quellen berichtet" if n_src > 1 else ""
        lines.append(f"• {label} {refs(nums)}{extra}")
    return "\n".join(lines)


RATING_TEXT = {
    "green": ("🟢", "Überwiegend positive Nachrichtenlage"),
    "yellow": ("🟡", "Beobachten – Nachrichtenlage nicht eindeutig"),
    "red": ("🔴", "Überwiegend negative Nachrichtenlage"),
}
RATING_COLOR = {"green": COLOR_GREEN, "yellow": COLOR_YELLOW, "red": COLOR_RED}


def rating_reason(rep: StockReport) -> str:
    p, n = len(rep.pos), len(rep.neg)
    pos_txt = f"{p} positive{'s' if p == 1 else ''} Signal{'e' if p != 1 else ''}" if p else "keine positiven Signale"
    neg_txt = f"{n} negative{'s' if n == 1 else ''} Signal{'e' if n != 1 else ''}" if n else "keine negativen"
    if rep.rating == "yellow" and n == 0 and p == 1:
        return f"{pos_txt}, keine negativen – für eine klare Einordnung noch zu wenig"
    return f"{pos_txt}, {neg_txt}"


def teaser(item: bot.NewsItem) -> str:
    s = item.summary.strip()
    if not s:
        return ""
    first = re.split(r"(?<=[a-zäöüß0-9)\]][.!?])\s+(?=[A-ZÄÖÜ„\"'])", s, maxsplit=1)[0]
    return bot.truncate(first, TEASER_CHARS)


def stock_embed(rep: StockReport, now: datetime) -> dict:
    emoji, label = RATING_TEXT[rep.rating]
    lines = []
    if rep.quote:
        lines.append(fmt_quote(rep.quote, now))
    else:
        lines.append("-# Kurs gerade nicht verfügbar")
    lines.append("")
    lines.append("**📰 Was ist passiert?**")
    for i, a in enumerate(rep.articles, start=1):
        lines.append(f"**[{i}]** {bot.truncate(a.item.title, TITLE_CHARS)}")
        t = teaser(a.item)
        if t:
            lines.append(f"> {t}")

    sources = []
    for i, a in enumerate(rep.articles, start=1):
        when = f" · <t:{int(a.item.published.timestamp())}:f>" if a.item.published else ""
        sources.append(f"[{i}] {a.item.feed.name}{when} · [🔗 Artikel lesen]({a.item.link})")

    return {
        "title": f"{emoji} {rep.stock.name} · {rep.stock.ticker}",
        "description": "\n".join(lines)[:4000],
        "color": RATING_COLOR[rep.rating],
        "fields": [
            {"name": "✅ Positive Signale", "value": signal_lines(rep.pos, rep)[:1024], "inline": False},
            {"name": "⚠️ Risiken / negative Signale", "value": signal_lines(rep.neg, rep)[:1024], "inline": False},
            {"name": "🧭 Einstufung", "value": f"{emoji} **{label}**\n{rating_reason(rep)}"[:1024], "inline": False},
            {"name": "📚 Quellen zum Nachlesen", "value": "\n".join(sources)[:1024], "inline": False},
        ],
        "footer": {"text": STOCK_FOOTER},
    }


def edition_name(local: datetime) -> str:
    if local.weekday() >= 5:
        return "Wochenendausgabe"
    if local.hour < 11:
        return "Morgenausgabe"
    if local.hour < 17:
        return "Mittagsausgabe"
    return "Abendausgabe"


def header_embed(reports: list[StockReport], now: datetime, window: timedelta, edition: str | None = None) -> dict:
    local = now.astimezone(BERLIN)
    edition = edition or edition_name(local)
    counts = {k: sum(1 for r in reports if r.rating == k) for k in ("green", "yellow", "red")}
    hours = round(window.total_seconds() / 3600)
    n = len(reports)
    lines = [
        f"**{n} {'Aktie' if n == 1 else 'Aktien'}** mit relevanten Neuigkeiten aus den letzten {hours} Stunden",
        "",
        f"🟢 {counts['green']} × überwiegend positiv   🟡 {counts['yellow']} × beobachten   🔴 {counts['red']} × überwiegend negativ",
        "",
        "-# Reihenfolge: 🟢 vor 🟡 vor 🔴. Nummern wie [1] verweisen auf die Quellen am Ende jedes Blocks.",
    ]
    return {
        "title": f"📊 Aktien-Überblick · {WEEKDAYS[local.weekday()]}, {local:%d.%m.%Y} · {edition}",
        "description": "\n".join(lines),
        "color": COLOR_HEADER,
    }


def disclaimer_embed(feed_names: list[str]) -> dict:
    return {
        "title": "⚠️ Wichtiger Hinweis: Keine Anlageberatung!",
        "description": f"{DISCLAIMER}\n\n{METHOD_NOTE}",
        "color": COLOR_NOTE,
        "fields": [{"name": "📚 Ausgewertete Nachrichtenquellen", "value": ", ".join(feed_names)[:1024], "inline": False}],
    }


def embed_len(e: dict) -> int:
    n = len(e.get("title", "")) + len(e.get("description", ""))
    n += len(e.get("footer", {}).get("text", ""))
    for f in e.get("fields", []):
        n += len(f["name"]) + len(f["value"])
    return n


def pack_messages(embeds: list[dict]) -> list[list[dict]]:
    """Verteilt Embeds so auf Nachrichten, dass Discords Limits eingehalten werden."""
    messages: list[list[dict]] = [[]]
    size = 0
    for e in embeds:
        n = embed_len(e)
        if messages[-1] and (size + n > MSG_MAX_CHARS or len(messages[-1]) >= MSG_MAX_EMBEDS):
            messages.append([])
            size = 0
        messages[-1].append(e)
        size += n
    return messages


def build_overview_messages(
    reports: list[StockReport], feed_names: list[str], now: datetime, window: timedelta, edition: str | None = None
) -> list[list[dict]]:
    embeds = [header_embed(reports, now, window, edition)]
    embeds += [stock_embed(r, now) for r in reports]
    embeds.append(disclaimer_embed(feed_names))
    return pack_messages(embeds)


# --------------------------------------------------------------------------- #
# Textvorschau (fuer Probelaeufe im GitHub-Log)
# --------------------------------------------------------------------------- #
def _plain(text: str) -> str:
    text = re.sub(r"<t:(\d+):\w>", lambda m: datetime.fromtimestamp(int(m.group(1)), BERLIN).strftime("%d.%m. %H:%M"), text)
    text = re.sub(r"\[🔗 Artikel lesen\]\((\S+)\)", r"\1", text)
    return text.replace("**", "").replace("-# ", "")


def preview_text(messages: list[list[dict]]) -> str:
    out = []
    for m_i, msg in enumerate(messages, start=1):
        out.append(f"==================== Discord-Nachricht {m_i} ====================")
        for e in msg:
            out.append("┃ " + _plain(e.get("title", "")))
            for line in _plain(e.get("description", "")).splitlines():
                out.append("┃ " + line)
            for f in e.get("fields", []):
                out.append("┃")
                out.append("┃ " + _plain(f["name"]))
                for line in _plain(f["value"]).splitlines():
                    out.append("┃   " + line)
            if e.get("footer"):
                out.append("┃ " + e["footer"]["text"])
            out.append("")
    return "\n".join(out)
