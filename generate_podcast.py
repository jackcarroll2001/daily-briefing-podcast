#!/usr/bin/env python3
"""
Daily Briefing Podcast Generator

Generates a personalised daily podcast with:
1. Date
2. Weather for Gold Coast
3. Calendar events
4. Market overview (AU, US, UK, Asian)
5. Commodities (Gold, Silver, Oil, etc.)
6. World news summary

Outputs an MP3 and updates the RSS feed.
"""

import os
import json
import re
import datetime
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
import openai
import yfinance as yf
import requests
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator
from pydub import AudioSegment

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AEST = ZoneInfo("Australia/Brisbane")
TODAY = datetime.datetime.now(AEST)
DATE_STR = TODAY.strftime("%Y-%m-%d")
DATE_HUMAN = TODAY.strftime("%A, %B %-d, %Y")

GOLD_COAST_LAT = -28.0167
GOLD_COAST_LON = 153.4000

EPISODES_DIR = Path("episodes")
EPISODES_DIR.mkdir(exist_ok=True)

EPISODE_FILENAME = f"episode-{DATE_STR}.mp3"
EPISODE_PATH = EPISODES_DIR / EPISODE_FILENAME

BASE_URL = os.environ.get(
    "PODCAST_BASE_URL",
    "https://jackcarroll2001.github.io/daily-briefing-podcast",
)

# Target ~2500-3000 words for 15-20 minute podcast at natural pace
TARGET_WORD_COUNT = 2800

# ---------------------------------------------------------------------------
# Data Fetchers
# ---------------------------------------------------------------------------


def fetch_weather() -> str:
    """Fetch today's weather for Gold Coast from OpenWeatherMap."""
    api_key = os.environ.get("OPENWEATHER_API_KEY")
    if not api_key:
        return "Weather data unavailable (no API key configured)."

    url = "https://api.openweathermap.org/data/2.5/forecast"
    params = {
        "lat": GOLD_COAST_LAT,
        "lon": GOLD_COAST_LON,
        "appid": api_key,
        "units": "metric",
        "cnt": 8,  # next 24 hours in 3-hour blocks
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        temps = [item["main"]["temp"] for item in data["list"]]
        feels = [item["main"]["feels_like"] for item in data["list"]]
        descriptions = [item["weather"][0]["description"] for item in data["list"]]
        humidity = [item["main"]["humidity"] for item in data["list"]]
        wind_speeds = [item["wind"]["speed"] * 3.6 for item in data["list"]]  # m/s -> km/h

        rain_chance = any("rain" in d for d in descriptions)
        pop_values = [item.get("pop", 0) for item in data["list"]]
        max_pop = max(pop_values) * 100

        summary = (
            f"Gold Coast Weather for {DATE_HUMAN}:\n"
            f"- Temperature range: {min(temps):.0f}C to {max(temps):.0f}C "
            f"(feels like {min(feels):.0f}C to {max(feels):.0f}C)\n"
            f"- Conditions: {', '.join(set(descriptions))}\n"
            f"- Humidity: {min(humidity)}% to {max(humidity)}%\n"
            f"- Wind: up to {max(wind_speeds):.0f} km/h\n"
            f"- Chance of rain: {max_pop:.0f}%\n"
        )
        return summary
    except Exception as e:
        return f"Weather data unavailable: {e}"


def fetch_calendar() -> str:
    """Fetch today's calendar events from Google Calendar."""
    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS")
    token_json = os.environ.get("GOOGLE_CALENDAR_TOKEN")

    if not creds_json or not token_json:
        return "Calendar data unavailable (no credentials configured)."

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        token_data = json.loads(token_json)
        creds = Credentials.from_authorized_user_info(token_data)

        service = build("calendar", "v3", credentials=creds)

        start_of_day = TODAY.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = TODAY.replace(hour=23, minute=59, second=59, microsecond=0)

        events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=start_of_day.isoformat(),
                timeMax=end_of_day.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )

        events = events_result.get("items", [])

        if not events:
            return f"Calendar for {DATE_HUMAN}: No events scheduled today."

        lines = [f"Calendar for {DATE_HUMAN}:"]
        for event in events:
            start = event["start"].get("dateTime", event["start"].get("date"))
            summary = event.get("summary", "Untitled event")
            location = event.get("location", "")

            if "T" in start:
                time_str = datetime.datetime.fromisoformat(start).strftime("%-I:%M %p")
                lines.append(f"- {time_str}: {summary}" + (f" ({location})" if location else ""))
            else:
                lines.append(f"- All day: {summary}" + (f" ({location})" if location else ""))

        return "\n".join(lines)
    except Exception as e:
        return f"Calendar data unavailable: {e}"


def fetch_market_data() -> str:
    """Fetch market data for AU, US, UK, and Asian markets."""
    markets = {
        "Australian Market (ASX)": {
            "indices": {"^AXJO": "ASX 200", "^AORD": "All Ordinaries"},
            "key_stocks": {
                "BHP.AX": "BHP Group",
                "CBA.AX": "Commonwealth Bank",
                "CSL.AX": "CSL Limited",
                "NAB.AX": "NAB",
                "WBC.AX": "Westpac",
                "ANZ.AX": "ANZ",
                "FMG.AX": "Fortescue",
                "WDS.AX": "Woodside Energy",
                "RIO.AX": "Rio Tinto",
                "MQG.AX": "Macquarie Group",
                "WES.AX": "Wesfarmers",
                "WOW.AX": "Woolworths",
                "TLS.AX": "Telstra",
                "ALL.AX": "Aristocrat Leisure",
            },
        },
        "US Market": {
            "indices": {"^GSPC": "S&P 500", "^DJI": "Dow Jones", "^IXIC": "NASDAQ"},
            "key_stocks": {
                "AAPL": "Apple",
                "MSFT": "Microsoft",
                "GOOGL": "Alphabet",
                "AMZN": "Amazon",
                "NVDA": "NVIDIA",
                "META": "Meta",
                "TSLA": "Tesla",
                "JPM": "JP Morgan",
                "V": "Visa",
                "JNJ": "Johnson & Johnson",
            },
        },
        "UK Market": {
            "indices": {"^FTSE": "FTSE 100"},
            "key_stocks": {
                "SHEL.L": "Shell",
                "AZN.L": "AstraZeneca",
                "HSBA.L": "HSBC",
                "ULVR.L": "Unilever",
                "BP.L": "BP",
                "RIO.L": "Rio Tinto",
                "GSK.L": "GSK",
                "BARC.L": "Barclays",
            },
        },
        "Asian Markets": {
            "indices": {
                "^N225": "Nikkei 225",
                "^HSI": "Hang Seng",
                "000001.SS": "Shanghai Composite",
                "^KS11": "KOSPI",
            },
            "key_stocks": {
                "7203.T": "Toyota",
                "9984.T": "SoftBank",
                "6758.T": "Sony",
                "005930.KS": "Samsung",
                "9988.HK": "Alibaba",
                "0700.HK": "Tencent",
            },
        },
    }

    result_parts = []

    for market_name, market_info in markets.items():
        lines = [f"\n{market_name}:"]

        # Fetch indices
        lines.append("  Indices:")
        for ticker, name in market_info["indices"].items():
            try:
                data = yf.Ticker(ticker)
                hist = data.history(period="2d")
                if len(hist) >= 2:
                    prev_close = hist["Close"].iloc[-2]
                    last_close = hist["Close"].iloc[-1]
                    change = last_close - prev_close
                    pct_change = (change / prev_close) * 100
                    direction = "up" if change > 0 else "down"
                    lines.append(
                        f"    {name}: {last_close:,.2f} ({direction} {abs(pct_change):.2f}%)"
                    )
                elif len(hist) == 1:
                    last_close = hist["Close"].iloc[-1]
                    lines.append(f"    {name}: {last_close:,.2f}")
            except Exception:
                lines.append(f"    {name}: data unavailable")

        # Fetch key stocks - find biggest movers
        lines.append("  Key movers:")
        stock_moves = []
        for ticker, name in market_info["key_stocks"].items():
            try:
                data = yf.Ticker(ticker)
                hist = data.history(period="2d")
                if len(hist) >= 2:
                    prev_close = hist["Close"].iloc[-2]
                    last_close = hist["Close"].iloc[-1]
                    pct_change = ((last_close - prev_close) / prev_close) * 100
                    stock_moves.append((name, ticker, last_close, pct_change))
            except Exception:
                continue

        # Sort by absolute percentage change and show top 5 movers
        stock_moves.sort(key=lambda x: abs(x[3]), reverse=True)
        for name, ticker, price, pct in stock_moves[:5]:
            direction = "up" if pct > 0 else "down"
            lines.append(f"    {name}: {direction} {abs(pct):.2f}%")

        result_parts.append("\n".join(lines))

    return "\n".join(result_parts)


def fetch_commodities() -> str:
    """Fetch commodities data: Gold, Silver, Oil, Copper, Natural Gas."""
    commodities = {
        "GC=F": "Gold",
        "SI=F": "Silver",
        "CL=F": "Crude Oil (WTI)",
        "BZ=F": "Brent Crude",
        "HG=F": "Copper",
        "NG=F": "Natural Gas",
        "PL=F": "Platinum",
    }

    lines = ["Commodities:"]
    for ticker, name in commodities.items():
        try:
            data = yf.Ticker(ticker)
            hist = data.history(period="2d")
            if len(hist) >= 2:
                prev_close = hist["Close"].iloc[-2]
                last_close = hist["Close"].iloc[-1]
                change = last_close - prev_close
                pct_change = (change / prev_close) * 100
                direction = "up" if change > 0 else "down"
                lines.append(
                    f"  {name}: ${last_close:,.2f} ({direction} {abs(pct_change):.2f}%)"
                )
            elif len(hist) == 1:
                last_close = hist["Close"].iloc[-1]
                lines.append(f"  {name}: ${last_close:,.2f}")
        except Exception:
            lines.append(f"  {name}: data unavailable")

    return "\n".join(lines)


def fetch_news() -> str:
    """Fetch top world news headlines from multiple sources."""
    headlines = []

    # Google News RSS - Top Stories
    sources = [
        ("https://news.google.com/rss?hl=en-AU&gl=AU&ceid=AU:en", "Google News AU"),
        ("https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx1YlY4U0FtVnVHZ0pCVlNnQVAB?hl=en-AU&gl=AU&ceid=AU:en", "Google News World"),
    ]

    for url, source_name in sources:
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, "html.parser")
            items = soup.find_all("item")[:10]
            for item in items:
                title = item.find("title")
                pub_date = item.find("pubdate")
                if title:
                    headlines.append(
                        {
                            "title": title.text.strip(),
                            "source": source_name,
                            "date": pub_date.text.strip() if pub_date else "",
                        }
                    )
        except Exception:
            continue

    # Also try ABC News Australia
    try:
        resp = requests.get("https://www.abc.net.au/news/feed/2942460/rss.xml", timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")
        items = soup.find_all("item")[:5]
        for item in items:
            title = item.find("title")
            if title:
                headlines.append({"title": title.text.strip(), "source": "ABC News AU"})
    except Exception:
        pass

    # Try BBC World
    try:
        resp = requests.get("https://feeds.bbci.co.uk/news/world/rss.xml", timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")
        items = soup.find_all("item")[:5]
        for item in items:
            title = item.find("title")
            if title:
                headlines.append({"title": title.text.strip(), "source": "BBC World"})
    except Exception:
        pass

    # Try Reuters
    try:
        resp = requests.get("https://www.reutersagency.com/feed/?best-topics=business&post_type=best", timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")
        items = soup.find_all("item")[:5]
        for item in items:
            title = item.find("title")
            if title:
                headlines.append({"title": title.text.strip(), "source": "Reuters"})
    except Exception:
        pass

    if not headlines:
        return "World news headlines unavailable."

    # Deduplicate by checking for similar titles
    seen = set()
    unique = []
    for h in headlines:
        title_lower = h["title"].lower()[:50]
        if title_lower not in seen:
            seen.add(title_lower)
            unique.append(h)

    lines = ["Top World News Headlines:"]
    for h in unique[:20]:
        lines.append(f"- [{h['source']}] {h['title']}")

    return "\n".join(lines)


def fetch_forex() -> str:
    """Fetch key forex rates relevant to Australia."""
    pairs = {
        "AUDUSD=X": "AUD/USD",
        "AUDEUR=X": "AUD/EUR",
        "AUDGBP=X": "AUD/GBP",
        "AUDJPY=X": "AUD/JPY",
        "AUDCNY=X": "AUD/CNY",
    }

    lines = ["Key Forex Rates (AUD):"]
    for ticker, name in pairs.items():
        try:
            data = yf.Ticker(ticker)
            hist = data.history(period="2d")
            if len(hist) >= 2:
                prev = hist["Close"].iloc[-2]
                last = hist["Close"].iloc[-1]
                pct = ((last - prev) / prev) * 100
                direction = "up" if pct > 0 else "down"
                lines.append(f"  {name}: {last:.4f} ({direction} {abs(pct):.2f}%)")
            elif len(hist) == 1:
                last = hist["Close"].iloc[-1]
                lines.append(f"  {name}: {last:.4f}")
        except Exception:
            lines.append(f"  {name}: data unavailable")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Script Generation via Claude
# ---------------------------------------------------------------------------


def generate_script(weather: str, calendar: str, markets: str, commodities: str,
                    forex: str, news: str) -> str:
    """Use Claude to write the podcast script."""
    client = anthropic.Anthropic()

    prompt = f"""You are the host of "The Daily Briefing", a professional yet conversational
daily podcast for an Australian listener based on the Gold Coast. Your name is not important -
you never introduce yourself by name. Your style is confident, knowledgeable, and engaging -
like a smart friend who reads everything so they don't have to.

Write a complete podcast script for today's episode. The script should be {TARGET_WORD_COUNT} words
(this is critical - it needs to be 15-20 minutes when read aloud at a natural pace).

IMPORTANT FORMATTING RULES:
- Write ONLY the spoken words. No stage directions, no [pause], no (laughs), no sound effects.
- No headings, no markdown, no asterisks, no formatting characters.
- Write naturally flowing speech with clear transitions between segments.
- Use conversational Australian English (not overly formal, but professional).
- Numbers should be written as they'd be spoken (e.g., "twenty-three point five percent" not "23.5%").
- For stock prices, round appropriately and speak naturally.

STRUCTURE (follow this exact order):

1. OPENING & DATE: Brief, energetic opening. State today's date ({DATE_HUMAN}).

2. WEATHER: Natural conversational summary of today's Gold Coast weather. What to expect,
   what to wear, whether to grab an umbrella. Keep it brief but useful (~30 seconds).

3. CALENDAR: What's on today. If no events, briefly note it's a clear day. (~30 seconds)

4. MARKETS OVERVIEW: This is the meat of the financial section (4-5 minutes total).
   Start with the Australian market from the previous trading day - how the ASX performed,
   key movers and why they moved if apparent.
   Then US markets - S&P 500, Dow, NASDAQ performance and notable movers.
   Then UK - FTSE and notable movers.
   Then Asian markets - Nikkei, Hang Seng, Shanghai and notable movers.
   For each market, give the high-level index movement first, then drill into the interesting
   individual stock stories. Try to connect themes across markets where relevant.

5. COMMODITIES: Gold, Silver, Oil and other commodities. Note any significant moves and
   briefly explain what's driving them. (~2 minutes)

6. FOREX: Brief AUD movements against major currencies. (~30 seconds)

7. WORLD NEWS: This is the second major section (5-10 minutes). Cover the most important
   world events and developments. Group related stories together. Provide context and analysis,
   not just headlines. Cover a mix of geopolitical, economic, and significant social/tech stories.
   Prioritise stories relevant to Australia and the Asia-Pacific region.

8. CLOSING: Brief, forward-looking close. Mention anything to watch for today. Sign off warmly.

Here is today's data:

=== WEATHER ===
{weather}

=== CALENDAR ===
{calendar}

=== MARKET DATA ===
{markets}

=== COMMODITIES ===
{commodities}

=== FOREX ===
{forex}

=== NEWS HEADLINES ===
{news}

Remember: Write exactly {TARGET_WORD_COUNT} words of natural, flowing speech. No formatting
characters. No stage directions. Just the words to be spoken aloud."""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )

    script = response.content[0].text

    word_count = len(script.split())
    print(f"Generated script: {word_count} words")

    return script


# ---------------------------------------------------------------------------
# Text-to-Speech via OpenAI
# ---------------------------------------------------------------------------


def generate_audio(script: str, output_path: Path) -> None:
    """Convert script to audio using OpenAI TTS, chunked to handle length limits."""
    client = openai.OpenAI()

    # OpenAI TTS has a 4096 character limit per request
    # Split script into chunks at sentence boundaries
    chunks = split_into_chunks(script, max_chars=4000)
    print(f"Split script into {len(chunks)} audio chunks")

    audio_segments = []

    for i, chunk in enumerate(chunks):
        print(f"Generating audio chunk {i + 1}/{len(chunks)}...")

        response = client.audio.speech.create(
            model="tts-1-hd",
            voice="onyx",  # Deep, authoritative voice good for news/briefings
            input=chunk,
            response_format="mp3",
        )

        # Save chunk to temp file
        chunk_path = tempfile.mktemp(suffix=".mp3")
        response.stream_to_file(chunk_path)

        segment = AudioSegment.from_mp3(chunk_path)
        audio_segments.append(segment)
        os.unlink(chunk_path)

    # Concatenate all segments with brief pauses between
    print("Concatenating audio segments...")
    silence = AudioSegment.silent(duration=500)  # 500ms pause between chunks
    final_audio = audio_segments[0]
    for segment in audio_segments[1:]:
        final_audio += silence + segment

    # Export final audio
    final_audio.export(str(output_path), format="mp3", bitrate="128k")

    duration_minutes = len(final_audio) / 1000 / 60
    file_size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"Final audio: {duration_minutes:.1f} minutes, {file_size_mb:.1f} MB")


def split_into_chunks(text: str, max_chars: int = 4000) -> list[str]:
    """Split text into chunks at sentence boundaries."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current_chunk = ""

    for sentence in sentences:
        if len(current_chunk) + len(sentence) + 1 > max_chars:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = sentence
        else:
            current_chunk += " " + sentence if current_chunk else sentence

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks


# ---------------------------------------------------------------------------
# RSS Feed Generation
# ---------------------------------------------------------------------------


def update_feed() -> None:
    """Generate/update the RSS feed with the new episode."""
    fg = FeedGenerator()
    fg.load_extension("podcast")

    fg.title("The Daily Briefing")
    fg.link(href=BASE_URL)
    fg.description(
        "Your personalised daily briefing - weather, calendar, markets, "
        "commodities, and world news. Generated fresh every morning."
    )
    fg.language("en-au")
    fg.podcast.itunes_category("News")
    fg.podcast.itunes_author("Daily Briefing Bot")
    fg.podcast.itunes_explicit("no")
    fg.podcast.itunes_summary(
        "AI-generated daily briefing covering Gold Coast weather, "
        "calendar, global markets, commodities, and world news."
    )
    fg.podcast.itunes_image(f"{BASE_URL}/podcast-cover.jpg")
    fg.image(
        url=f"{BASE_URL}/podcast-cover.jpg",
        title="The Daily Briefing",
        link=BASE_URL,
    )

    # Find all existing episodes
    episode_files = sorted(EPISODES_DIR.glob("episode-*.mp3"), reverse=True)

    for ep_file in episode_files[:30]:  # Keep last 30 episodes in feed
        ep_date_str = ep_file.stem.replace("episode-", "")
        try:
            ep_date = datetime.datetime.strptime(ep_date_str, "%Y-%m-%d").replace(
                hour=5, minute=0, tzinfo=AEST
            )
        except ValueError:
            continue

        fe = fg.add_entry()
        fe.id(f"{BASE_URL}/episodes/{ep_file.name}")
        fe.title(f"Daily Briefing - {ep_date.strftime('%A, %B %-d, %Y')}")
        fe.description(f"Your daily briefing for {ep_date.strftime('%A, %B %-d, %Y')}.")
        fe.published(ep_date)
        fe.enclosure(
            url=f"{BASE_URL}/episodes/{ep_file.name}",
            length=str(ep_file.stat().st_size),
            type="audio/mpeg",
        )
        fe.podcast.itunes_duration(str(get_mp3_duration(ep_file)))

    fg.rss_file("feed.xml")
    print("RSS feed updated: feed.xml")


def get_mp3_duration(path: Path) -> int:
    """Get MP3 duration in seconds."""
    try:
        audio = AudioSegment.from_mp3(str(path))
        return int(len(audio) / 1000)
    except Exception:
        return 900  # Default 15 minutes


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    print(f"=== Generating Daily Briefing for {DATE_HUMAN} ===\n")

    # Check if episode already exists
    if EPISODE_PATH.exists():
        print(f"Episode already exists: {EPISODE_PATH}")
        return

    # Fetch all data
    print("Fetching weather...")
    weather = fetch_weather()
    print(weather)

    print("\nFetching calendar...")
    calendar = fetch_calendar()
    print(calendar)

    print("\nFetching market data (this may take a minute)...")
    markets = fetch_market_data()
    print(markets)

    print("\nFetching commodities...")
    commodities = fetch_commodities()
    print(commodities)

    print("\nFetching forex rates...")
    forex = fetch_forex()
    print(forex)

    print("\nFetching news...")
    news = fetch_news()
    print(news)

    # Generate script
    print("\nGenerating podcast script via Claude...")
    script = generate_script(weather, calendar, markets, commodities, forex, news)

    # Save script for reference
    script_path = EPISODES_DIR / f"script-{DATE_STR}.txt"
    script_path.write_text(script)
    print(f"Script saved: {script_path}")

    # Generate audio
    print("\nGenerating audio via OpenAI TTS...")
    generate_audio(script, EPISODE_PATH)
    print(f"Episode saved: {EPISODE_PATH}")

    # Update RSS feed
    print("\nUpdating RSS feed...")
    update_feed()

    print("\n=== Done! ===")


if __name__ == "__main__":
    main()
