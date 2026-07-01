"""
BMKG Tool — fetches live disaster data.

Data sources (in order of priority):
  1. BMKG (Indonesia's official agency) — gempaterkini.json
  2. USGS (US Geological Survey) — global earthquakes, always available
  3. GDACS RSS — floods, volcanoes, other disasters

Note: BMKG's directory (data.bmkg.go.id/DataMKG/TEWS/) returns 403 —
that's normal, you can't browse the folder. But the specific JSON
files inside it are publicly accessible.
"""

import httpx
import feedparser
from typing import Optional
from dotenv import load_dotenv
import os

load_dotenv()

# Direct file URLs — NOT the directory (directory gives 403)
BMKG_RECENT = "https://data.bmkg.go.id/DataMKG/TEWS/gempaterkini.json"
BMKG_LATEST = "https://data.bmkg.go.id/DataMKG/TEWS/autogempa.json"

# USGS fallback — always available, covers Indonesia (lat -11 to 6, lon 95 to 141)
USGS_URL = (
    "https://earthquake.usgs.gov/fdsnws/event/1/query"
    "?format=geojson&minmagnitude=4.5"
    "&minlatitude=-11&maxlatitude=6"
    "&minlongitude=95&maxlongitude=141"
    "&orderby=time&limit=15"
)

# Browser-like headers — some servers block Python's default user agent
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


async def fetch_latest_earthquakes() -> list[dict]:
    """
    Fetch recent earthquakes. Tries BMKG first, falls back to USGS.
    Always returns a list — never raises an exception to the caller.
    """
    # Try BMKG first
    bmkg_results = await _fetch_bmkg()
    if bmkg_results:
        print(f"[BMKG Tool] Got {len(bmkg_results)} events from BMKG")
        return bmkg_results

    # BMKG failed — use USGS as fallback
    print("[BMKG Tool] BMKG unavailable, falling back to USGS...")
    usgs_results = await _fetch_usgs()
    print(f"[BMKG Tool] Got {len(usgs_results)} events from USGS")
    return usgs_results


async def _fetch_bmkg() -> list[dict]:
    """Fetch from BMKG gempaterkini.json (15 most recent quakes)."""
    async with httpx.AsyncClient(timeout=10.0, headers=HEADERS) as client:
        try:
            response = await client.get(BMKG_RECENT)
            response.raise_for_status()
            data = response.json()

            earthquakes = data.get("Infogempa", {}).get("gempa", [])
            results = []
            for quake in earthquakes:
                mag = float(quake.get("Magnitude", 0))
                potential = quake.get("Potensi", "")
                results.append({
                    "source": "BMKG",
                    "type": "EARTHQUAKE",
                    "title": f"Gempa M{quake.get('Magnitude', '?')} - {quake.get('Wilayah', 'Unknown')}",
                    "magnitude": mag,
                    "depth": quake.get("Kedalaman", "Unknown"),
                    "location_name": quake.get("Wilayah", "Unknown"),
                    "latitude": _parse_coord(quake.get("Lintang", "0")),
                    "longitude": _parse_coord(quake.get("Bujur", "0")),
                    "datetime_str": f"{quake.get('Tanggal', '')} {quake.get('Jam', '')}",
                    "potential": potential,
                    "severity": calculate_severity(mag, potential),
                })
            return results

        except httpx.HTTPStatusError as e:
            print(f"[BMKG Tool] HTTP {e.response.status_code} from BMKG: {e}")
            return []
        except Exception as e:
            print(f"[BMKG Tool] BMKG fetch error: {e}")
            return []


async def _fetch_usgs() -> list[dict]:
    """
    Fetch from USGS FDSN API — covers Indonesian earthquakes.
    USGS GeoJSON format is very clean and reliable.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.get(USGS_URL)
            response.raise_for_status()
            data = response.json()

            features = data.get("features", [])
            results = []
            for feature in features:
                props = feature.get("properties", {})
                coords = feature.get("geometry", {}).get("coordinates", [0, 0, 0])
                mag = float(props.get("mag", 0))
                place = props.get("place", "Indonesia Region")

                results.append({
                    "source": "USGS",
                    "type": "EARTHQUAKE",
                    "title": f"M{mag} - {place}",
                    "magnitude": mag,
                    "depth": f"{coords[2]} km" if len(coords) > 2 else "Unknown",
                    "location_name": place,
                    "latitude": coords[1],
                    "longitude": coords[0],
                    "datetime_str": str(props.get("time", "")),
                    "potential": "Tsunami possible" if props.get("tsunami", 0) == 1 else "",
                    "severity": calculate_severity(mag, "tsunami" if props.get("tsunami") else ""),
                    "url": props.get("url", ""),
                })
            return results

        except Exception as e:
            print(f"[BMKG Tool] USGS fetch error: {e}")
            return []


async def fetch_disaster_rss() -> list[dict]:
    """
    Fetch disaster alerts from GDACS RSS feed.
    Covers floods, volcanoes, cyclones in Indonesia.
    """
    GDACS_RSS = "https://www.gdacs.org/xml/rss_Indonesia.xml"
    try:
        feed = feedparser.parse(GDACS_RSS)
        results = []
        for entry in feed.entries[:10]:
            results.append({
                "source": "GDACS",
                "type": _guess_disaster_type(entry.get("title", "")),
                "title": entry.get("title", "Unknown Disaster"),
                "description": entry.get("summary", "")[:300],
                "location_name": "Indonesia",
                "latitude": None,
                "longitude": None,
                "datetime_str": entry.get("published", ""),
                "url": entry.get("link", ""),
                "severity": "MEDIUM",
            })
        return results
    except Exception as e: 
        print(f"[BMKG Tool] GDACS RSS error: {e}")
        return []


def calculate_severity(magnitude: float, potential: str) -> str:
    """Determine severity from magnitude and tsunami potential text."""
    if "tsunami" in potential.lower():
        return "CRITICAL"
    if magnitude >= 7.0:
        return "CRITICAL"
    elif magnitude >= 6.0:
        return "HIGH"
    elif magnitude >= 5.0:
        return "MEDIUM"
    else:
        return "LOW"


def _parse_coord(coord_str: str) -> float:
    """
    Parse BMKG coordinate strings like '6.50 LS' or '107.00 BT'.
    LS = South (negative), BB = West (negative).
    """
    try:
        parts = coord_str.strip().split(" ")
        value = float(parts[0])
        direction = parts[1].upper() if len(parts) > 1 else ""
        if direction in ("LS", "BB"):
            value = -value
        return value
    except Exception:
        return 0.0


def _guess_disaster_type(title: str) -> str:
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
    return "OTHER"
