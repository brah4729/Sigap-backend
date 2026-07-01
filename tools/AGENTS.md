# Tools Folder — AGENTS.md

Helper functions that agents call to get data or compute things.
Tools do ONE thing well — they don't contain agent logic.

---

## What's In Here

```
tools/
├── bmkg_tool.py      ← Fetches earthquake data from BMKG + USGS fallback
├── resource_tool.py  ← Queries available resources, haversine distance, seed data
└── __init__.py
```

---

## Design Philosophy

Tools are DUMB — they just fetch or compute data.
Agents are SMART — they decide what to do with the data.

```
Agent: "I need earthquake data"
         ↓ calls
Tool: fetch_latest_earthquakes()
         ↓ returns
    [ {title, magnitude, lat, lon, ...}, ... ]
         ↓ back to
Agent: "Ok, this M6.8 is significant, let me assess it"
```

This separation means:
- Tools are easy to test in isolation
- Agents can be swapped without touching tools
- Tools can be reused across multiple agents

---

## bmkg_tool.py

### Data Sources (in priority order)

1. **BMKG** — `data.bmkg.go.id/DataMKG/TEWS/gempaterkini.json`
   - Indonesia's official meteorology agency
   - 15 most recent significant earthquakes
   - Returns 403 if you hit the directory — always use the full file URL

2. **USGS** — `earthquake.usgs.gov/fdsnws/event/1/query`
   - US Geological Survey — global earthquakes
   - Filtered to Indonesia bounding box (lat -11 to 6, lon 95 to 141)
   - Used as fallback when BMKG is unavailable

3. **GDACS RSS** — `gdacs.org/xml/rss_Indonesia.xml`
   - Global disaster alerts (floods, volcanoes, cyclones)
   - Not just earthquakes — complements BMKG

### BMKG Coordinate Format

BMKG returns coordinates as strings like `"6.50 LS"` or `"107.00 BT"`.
`_parse_coord()` converts these to standard decimal degrees.

```
LS = Lintang Selatan = South → negative
BT = Bujur Timur = East → positive
BB = Bujur Barat = West → negative
```

---

## resource_tool.py

### Haversine Distance

`haversine_distance_km()` calculates straight-line distance between
two lat/lon points on Earth. Used by CoordinatorAgent to find the
nearest resources to a disaster.

This is "as the crow flies" — not driving distance.
Good enough for resource prioritization decisions.

### Seed Data

`SEED_RESOURCES` contains 10 sample resources across Indonesia's
disaster-prone regions (Jakarta, Manado, Palu, etc.).

Run `POST /api/resources/seed` once after a fresh database.
`seed_resources()` is idempotent — safe to call multiple times,
won't duplicate if resources already exist.

---

## Adding a New Tool

1. Create `tools/your_tool.py`
2. Write focused functions — one function, one job
3. Always handle errors and return empty list/None (never raise to caller)
4. Import and call from the relevant agent
5. Document the data source and format in a docstring
