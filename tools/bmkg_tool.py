"""
BMKG Tool — fetches live disaster data from Indonesia's official
Meteorology, Climatology, and Geophysics Agency (BMKG).

BMKG provides free public APIs — no key needed!
We use two endpoints:
  1. Earthquake data (gempa terkini)
  2. Tsunami early warning data

Why a separate tool file?
  Agents use "tools" — small focused functions that do one thing.
  This keeps agent code clean. The agent decides WHEN to call it,
  the tool just handles HOW to fetch and parse the data.
"""

import httpx
import feedparser
from datetime import datetime, timezone
from typing import Optional
from dotenv import load_dotenv
import os

load_dotenv()

BMKG_BASE_URL = os.getenv("BMKG_BASE_URL", "https://data.bmkg.go.id/DataMKG/TEWS")


async def fetch_latest_earthquakes() -> list[dict]:
    """
    Fetches the latest significant earthquakes from BMKG.
    Returns a list of earthquake dicts, newest first.

    BMKG endpoint: /autogempa.json  (most recent single quake)
                   /gempaterkini.json (15 most recent quakes)
    """
    url = f"{BMKG_BASE_URL}/gempaterkini.json"

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

            # BMKG nests the data under Infogempa > gempa (list)
            earthquakes = data.get("Infogempa", {}).get("gempa", [])

            # Normalize into our own clean format
            results = []
            for quake in earthquakes:
                results.append({
                    "source": "BMKG",
                    "type": "EARTHQUAKE",
                    "title": f"Gempa {quake.get('Magnitude', '?')} SR - {quake.get('Wilayah', 'Unknown')}",
                    "magnitude": float(quake.get("Magnitude", 0)),
                    "depth": quake.get("Kedalaman", "Unknown"),
                    "location_name": quake.get("Wilayah", "Unknown"),
                    "latitude": _parse_coord(quake.get("Lintang", "0")),
                    "longitude": _parse_coord(quake.get("Bujur", "0")),
                    "datetime_str": f"{quake.get('Tanggal', '')} {quake.get('Jam', '')}",
                    "potential": quake.get("Potensi", ""),  # tsunami potential
                    "raw": quake,
                })
            return results

        except httpx.HTTPError as e:
            print(f"[BMKG Tool] HTTP error fetching earthquakes: {e}")
            return []
        except Exception as e:
            print(f"[BMKG Tool] Unexpected error: {e}")
            return []


async def fetch_latest_earthquake_single() -> Optional[dict]:
    """
    Fetches only the single most recent significant earthquake.
    BMKG updates this in real-time for M >= 5.0 quakes.
    """
    url = f"{BMKG_BASE_URL}/autogempa.json"

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

            quake = data.get("Infogempa", {}).get("gempa", {})
            if not quake:
                return None

            return {
                "source": "BMKG",
                "type": "EARTHQUAKE",
                "title": f"Gempa {quake.get('Magnitude', '?')} SR - {quake.get('Wilayah', 'Unknown')}",
                "magnitude": float(quake.get("Magnitude", 0)),
                "depth": quake.get("Kedalaman", "Unknown"),
                "location_name": quake.get("Wilayah", "Unknown"),
                "latitude": _parse_coord(quake.get("Lintang", "0")),
                "longitude": _parse_coord(quake.get("Bujur", "0")),
                "datetime_str": f"{quake.get('Tanggal', '')} {quake.get('Jam', '')}",
                "potential": quake.get("Potensi", ""),
                "shakemap_url": f"https://data.bmkg.go.id/DataMKG/TEWS/{quake.get('Shakemap', '')}",
                "raw": quake,
            }

        except Exception as e:
            print(f"[BMKG Tool] Error fetching latest quake: {e}")
            return None


async def fetch_disaster_rss() -> list[dict]:
    """
    Fetches disaster alerts from GDACS (Global Disaster Alert and
    Coordination System) RSS feed — covers Indonesia floods, volcanoes, etc.

    feedparser handles RSS parsing for us — no manual XML needed.
    """
    GDACS_RSS = "https://www.gdacs.org/xml/rss_Indonesia.xml"

    try:
        # feedparser is sync, but it's fast enough for our use case
        feed = feedparser.parse(GDACS_RSS)
        results = []

        for entry in feed.entries[:10]:  # only latest 10
            results.append({
                "source": "GDACS",
                "type": _guess_disaster_type(entry.get("title", "")),
                "title": entry.get("title", "Unknown Disaster"),
                "description": entry.get("summary", ""),
                "location_name": "Indonesia",
                "latitude": None,
                "longitude": None,
                "datetime_str": entry.get("published", ""),
                "url": entry.get("link", ""),
            })

        return results

    except Exception as e:
        print(f"[BMKG Tool] Error fetching GDACS RSS: {e}")
        return []


def calculate_severity(magnitude: float, potential: str) -> str:
    """
    Determines severity level based on earthquake magnitude
    and tsunami potential text from BMKG.

    This logic will be used by the Assessment Agent too.
    """
    potential_lower = potential.lower()

    # Tsunami potential immediately bumps to CRITICAL
    if "tsunami" in potential_lower:
        return "CRITICAL"

    if magnitude >= 7.0:
        return "CRITICAL"
    elif magnitude >= 6.0:
        return "HIGH"
    elif magnitude >= 5.0:
        return "MEDIUM"
    else:
        return "LOW"


# --- Private helpers ---

def _parse_coord(coord_str: str) -> float:
    """
    BMKG returns coords like '6.50 LS' (South) or '107.00 BT' (East).
    We convert to standard decimal: South = negative, West = negative.
    """
    try:
        parts = coord_str.strip().split(" ")
        value = float(parts[0])
        direction = parts[1].upper() if len(parts) > 1 else ""

        # LS = Lintang Selatan = South (negative)
        # BB = Bujur Barat = West (negative)
        if direction in ("LS", "BB"):
            value = -value

        return value
    except Exception:
        return 0.0


def _guess_disaster_type(title: str) -> str:
    """Guess disaster type from GDACS feed title text."""
    title_lower = title.lower()
    if "earthquake" in title_lower or "gempa" in title_lower:
        return "EARTHQUAKE"
    elif "flood" in title_lower or "banjir" in title_lower:
        return "FLOOD"
    elif "tsunami" in title_lower:
        return "TSUNAMI"
    elif "volcano" in title_lower or "eruption" in title_lower:
        return "VOLCANO"
    elif "landslide" in title_lower or "longsor" in title_lower:
        return "LANDSLIDE"
    else:
        return "OTHER"
