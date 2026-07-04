# AGENTS.md — Japan Travel Plan

Guide for LLM agents working in this repository. Read this before editing data, scripts, or the map UI. User-facing overview lives in `README.md`.

## What this project is

A static browser map of ~2,280 places to visit in Japan. Data comes mostly from a friend's Google My Maps KML exports (9 regions + Tokyo), plus a hand-maintained **Tokusatsu** list owned by the repo author.

- **Live site:** [cobalto.dev/japantravelplan](https://cobalto.dev/japantravelplan)
- **Title:** `Japan Travel Plan · Cobalto.dev`
- **Stack:** jQuery + Leaflet + MarkerCluster. No npm/build toolchain. OpenStreetMap tiles only.
- **Runtime:** Root `index.html` is self-contained (place data inlined). Needs internet for CDN libs and map tiles.

---

## Repository layout

| Path | Role | Edit directly? |
|------|------|----------------|
| `KML/*.kml` | Source of truth for 9 regions + Tokyo | Yes (re-export from My Maps) |
| `Locations/Tokyo.md`, `Kanto.md`, … | Generated markdown tables from KML | Regenerate via `build-locations.py` |
| `Locations/Tokusatsu.md` | Author's tokusatsu filming locations | **Yes — never auto-generated** |
| `app/normalize_names.py` | Place/group name + Google Maps URL logic | Yes |
| `app/build-locations.py` | KML → `Locations/*.md` (skips Tokusatsu) | Yes |
| `app/build-places.py` | KML + Tokusatsu → root `index.html` | Yes |
| `app/index.shell.html` | Map UI template (HTML/CSS/JS) | Yes — **not** `index.html` |
| `index.html` | Built output (data + UI) | Regenerate via `build-places.py` |
| `app/places.js` | Reference copy of inlined JSON | Generated, not loaded by app |
| `favicon.png` | Repo root favicon | Manual asset |
| `README.md` | User-facing docs | Update when workflows change |
| `AGENTS.md` | This file — agent/LLM guide | Update when rules or behavior change |

### KML filenames vs app group names

| KML file | App group name |
|----------|----------------|
| `TOKYO.kml` | `Tokyo` (title-cased from `TOKYO`) |
| `[1] Kanto - 脚下照顧.kml` | `Kanto` |
| `[2] Chubu - 脚下照顧.kml` | `Chubu` |
| … same pattern … | `Kansai`, `Chugoku`, `Shikoku`, `Kyushu`, `Tohoku`, `Hokkaido` |
| — | `Tokusatsu` (from `Locations/Tokusatsu.md`) |

Group order in the map: sorted KML filenames, then Tokusatsu appended last.

---

## Build commands

Requires Python 3 and `pykakasi`:

```bash
pip3 install pykakasi
```

### Regenerate the map app (most common)

```bash
python3 app/build-places.py
```

- Reads all `KML/*.kml` + `Locations/Tokusatsu.md`
- Writes root `index.html` and `app/places.js`
- Embeds `window.PLACES_DATA` into `app/index.shell.html` at `PLACES_DATA_PLACEHOLDER`

### Regenerate region markdown files from KML

```bash
python3 app/build-locations.py
```

- Writes `Locations/{Group}.md` for each KML file
- **Deletes** all `Locations/*.md` except `Tokusatsu.md`
- Does **not** update `index.html` — run `build-places.py` after if the map should change

### Typical workflow after KML changes

```bash
python3 app/build-locations.py && python3 app/build-places.py
```

### Tokusatsu-only changes

If you only edited `Locations/Tokusatsu.md`:

```bash
python3 app/build-places.py
```

`build-locations.py` does not touch Tokusatsu.

---

## Name normalization (`app/normalize_names.py`)

Used by both build scripts. Do not duplicate this logic elsewhere.

### Group names — `normalize_group_name()`

1. Strip `[N]` prefix (e.g. `[1] Kanto - …` → `Kanto - …`)
2. Strip trailing ` - {Japanese}` suffix
3. Title-case all-uppercase alpha names (`TOKYO` → `Tokyo`)

### Place names — `normalize_place_name()`

**Display format when Japanese is present:** `{Romaji or Latin label} ({Japanese original})`

**Latin-only names** (no Japanese script): keep as-is (e.g. Portuguese labels like `Torre de Tóquio`).

#### Mixed KML names: regionalized Latin + Japanese

Friend's KML sometimes bundles a **regionalized label** (Portuguese, etc.) with the real Japanese name:

```
Salão Showa 美遊ヘアスタジオ   (KML raw name)
→ Miyu Heasutajio (美遊ヘアスタジオ)   (display)
```

Rules:

1. Extract Japanese-only substring via `_extract_japanese_original()`
2. If Latin **before** Japanese is **regionalized** (accented chars like `ã`, `õ`, or Latin that does not match romaji of the Japanese), **discard** the Latin prefix and build display name from Japanese only
3. If Latin before Japanese **matches** transliteration (e.g. `Hozenji Yokocho 法善寺横丁`, `Tokyo Tower 東京タワー`), keep the Latin as the romaji part

#### Other patterns (keep existing Latin when appropriate)

| KML pattern | Display example |
|-------------|-----------------|
| `ハコビバ（HAKOVIVA）` | `HAKOVIVA (ハコビバ)` |
| `honu.cafe (ホヌカフェ)` | `honu.cafe (ホヌカフェ)` |
| `浅草寺` (Japanese only) | `Sensouji (浅草寺)` |

#### Edge cases handled

- Empty `()` from Latin-only parentheticals (e.g. `ハコビバ(HAKOVIVA)` extraction) are stripped
- Fullwidth punctuation normalized to halfwidth before processing

---

## Google Maps URLs

**Critical:** Display name and URL query name are **different** on purpose.

### `google_maps_url(source_name, lat, lng)`

```text
https://www.google.com/maps/search/?api=1&query={encoded_query},{lat},{lng}
```

### Query name — `maps_query_name()` / `japanese_original_name()`

| Source name type | URL query uses |
|------------------|----------------|
| Contains Japanese | **Japanese original only** (regionalized Latin stripped) |
| Latin only | Full original name |

**Why:** Google Maps regionalization breaks links when the KML bundles Portuguese + Japanese. Example:

```
KML:  Salão Showa 美遊ヘアスタジオ
URL:  …query=美遊ヘアスタジオ,35.7227518,139.6889626
```

### Where URLs appear

- `Locations/*.md` — third table column (from KML via `build-locations.py`)
- `index.html` / map popups — each place has a `url` field (from `build-places.py`)
- `Locations/Tokusatsu.md` — **manual**; `build-places.py` reads column 3 if it starts with `http`, otherwise generates `name,lat,lng`

### Tokusatsu URL format

Must use **name + geolocation** (not coordinates-only):

```text
https://www.google.com/maps/search/?api=1&query=Funado+Bridge+%2F+Funado+Riverside+Park+%28Itabashi%29,35.791359,139.672992
```

Coordinates column format: `lng,lat,0` (KML order).

---

## `Locations/Tokusatsu.md` — special rules

- **Not generated from KML.** Do not run bulk regeneration over it.
- Hand-curated list with section headers (`**TOKYO — Ultraman**`, etc.) — rows with `**` or empty coordinates are skipped by the parser
- Group name in app: `Tokusatsu` (from filename stem)
- For one-off Tokusatsu URL fixes, you may edit the MD and `index.html` directly without changing scripts — but `build-places.py` will preserve MD URLs on next regen if column 3 has `http` links
- Places are English/Latin names (no Japanese normalization needed for URLs)

---

## Map UI (`app/index.shell.html`)

**Always edit the shell, then run `build-places.py`.** Do not hand-edit the JS logic in `index.html` unless doing a one-off Tokusatsu hotfix.

### Data shape (`window.PLACES_DATA`)

```json
{
  "order": ["Tokyo", "Kanto", …, "Tokusatsu"],
  "groups": {
    "Tokyo": [
      { "name": "…", "lat": 35.0, "lng": 139.0, "url": "https://…" }
    ]
  }
}
```

### Region checkboxes ("Show on map")

Each source group has a checkbox filter. Rules:

| State | Map pins | Sidebar list header | List expand/collapse |
|-------|----------|---------------------|----------------------|
| **Checked** | Visible | Normal styling | User can click header to expand/collapse |
| **Unchecked** | **Hidden** (removed from cluster) | Muted + "· hidden on map", `cursor: default` | **Forced collapsed, cannot expand** |

**Important behaviors:**

- Unchecking a region **does not** auto re-enable it when clicking pins or list items — disabled regions stay off until the checkbox is checked again
- Pins from unchecked regions are not on the map, so users should never interact with them
- `showPlace()` returns immediately if `!groupEnabled[gid]`
- Use `document.getElementById("filter-g" + gid)` for checkboxes — **not** `$("#filter-g" + gid)` (jQuery misparses `#filter-g0`)

### Search

- Filters **sidebar list only**, not map markers
- Scoped to **currently checked** regions only
- When search text is non-empty: matching enabled groups auto-expand; non-matching groups hidden (`group-no-match`)
- When search is empty: does **not** force-collapse groups (user collapse state preserved for enabled regions)
- **Clear** button: clears search, checks all regions, collapses all groups

### Map popups

Each pin popup shows:

1. Place name (bold)
2. Group name (small)
3. "Open in Google Maps" link (`p.url`)

### Themes

Light / Dim / Dark affect **sidebar UI only**. Map always uses OpenStreetMap.

### Groups start collapsed

All region lists begin collapsed on load.

---

## What to edit for common tasks

| Task | Files to change | Then run |
|------|-----------------|----------|
| Add/fix KML places | `KML/*.kml` | `build-locations.py` + `build-places.py` |
| Change name/URL rules | `app/normalize_names.py` | both build scripts |
| Change map UI/behavior | `app/index.shell.html` | `build-places.py` |
| Add tokusatsu location | `Locations/Tokusatsu.md` | `build-places.py` |
| Update README for users | `README.md` | — |

---

## Pitfalls for agents

1. **Do not edit `index.html` UI logic for permanent changes** — edit `app/index.shell.html` and rebuild.
2. **Do not regenerate `Locations/Tokusatsu.md`** with `build-locations.py` — it is intentionally excluded.
3. **Do not use full KML name in Google Maps URLs** when Japanese is present — use `maps_query_name()` logic.
4. **Do not treat regionalized Portuguese labels as romaji** — strip them for display and URLs.
5. **Do not re-enable unchecked regions from map/list clicks** — checkbox is the only way to turn a region back on.
6. **Do not force-collapse enabled groups on checkbox toggle** — only collapse + lock **disabled** groups.
7. **Coordinates:** KML and MD use `lng,lat,0`; place JSON uses separate `lat` / `lng` floats.
8. **`build-places.py` re-normalizes Tokusatsu names** via `normalize_place_name()` even though they're Latin-only (no-op for most entries).
9. **Keep `README.md` in sync** with user-visible behavior; this file holds implementation detail agents need.

---

## Hosting

- GitHub Pages serves **repo root** (`index.html`, `favicon.png`)
- `app/` is not the published path
- No server-side code

---

## Dependencies

| Package | Used by |
|---------|---------|
| `pykakasi` | `normalize_names.py` (romaji conversion) |
| jQuery 3.7.1 (CDN) | map UI |
| Leaflet 1.9.4 (CDN) | map |
| Leaflet.markercluster 1.5.3 (CDN) | pin clustering |

---

## Quick verification after changes

```bash
python3 app/build-places.py
# Spot-check in index.html:
# - group "Tokyo" not "TOKYO"
# - mixed-name URL uses Japanese only (search for 美遊ヘアスタジオ)
# - Tokusatsu entries have name+coords in url field
# - PLACES_DATA place count ~2280, 10 groups
```

Open `index.html` in a browser: toggle region checkboxes, confirm unchecked regions collapse and cannot expand, search filters list within selected regions only.