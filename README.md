# Japan Travel Plan

A personal list of places to visit in Japan, with a simple browser map to browse and filter them.

Hosted at **[cobalto.dev/japantravelplan](https://cobalto.dev/japantravelplan)** (static site from repo root `index.html`).

## Project layout

| Path | Description |
|------|-------------|
| `index.html` | Built map app (generated — open this in a browser or serve from `/`). |
| `KML/` | Source location files (one per region + `TOKYO.kml`). Primary data for the map. |
| `Locations/` | Markdown place lists. Region files are generated from KML; `Tokusatsu.md` is hand-maintained and also included in the map. |
| `app/index.shell.html` | UI template. Edit this for layout or behavior changes. |
| `app/parse_places.py` | Shared build-time parser (KML folders, dedup, Tokusatsu tags). |
| `app/name_overrides.json` | Hand-accepted English/Kanji names for Portuguese or wrong labels. |
| `app/enrich_places.py` | Optional GSI/Nominatim enrich (writes cache + proposals; never required for rebuild). |
| `app/enrich_cache.json` / `app/enrich-proposals.md` | Committed HTTP cache and human-review name proposals. |
| `app/build-places.py` | Rebuilds root `index.html` and `app/places.js` from `KML/` and `Locations/Tokusatsu.md` (offline). |
| `app/build-locations.py` | Regenerates `Locations/{Region}.md` from KML (skips `Tokusatsu.md`). |
| `app/normalize_names.py` | Normalizes place and group names and builds Google Maps URLs during the build. |
| `app/places.js` | Reference copy of the place data (not loaded by the app). |
| `AGENTS.md` | Guide for LLM agents working in this repo (build rules, UI behavior, pitfalls). |
| `PLAN.md` | Phased plan: build-time data, light runtime, desktop/mobile UX. |

## Using the map

You only need **`index.html`** — copy or download that single file and open it in any modern browser. All place data is embedded inside it, so no server, install, or rest of the repo is required. You do need an **internet connection** while using the map so the app can load Leaflet and map tiles from the web.

1. Open `index.html` locally (double-click or drag into a browser), or visit the hosted URL.
2. On phones (or short landscape height), use the **List / Map** tabs at the bottom to switch views; tapping a place in the list jumps to the map. List scroll is kept when you go back. On Map, a **selected-place bar** shows name, area, Maps, Overview, Directions, and List.
3. Use **Show on map** checkboxes to choose which sources appear on the map. Toggling a region fits the map when the visible set is small (or a search is active); it skips a jumpy Japan-wide fit when nearly all regions are on.
4. Unchecked regions are hidden on the map; their sidebar lists stay **collapsed** and cannot be expanded until re-enabled (except when opening a shared place link — see below).
5. The list is **Region → Area → Place**. Expanding a region shows area headers (wards, cities); expanding an area shows its places.
6. Search filters the **sidebar list and the map** (within currently checked sources, and the Tokusatsu chip if it is on). Matching groups and areas auto-expand; the map fits the matches and shows **N matches**.
7. The **Tokusatsu** chip shows only tagged filming sites (still respecting enabled regions). Tagged rows have a small badge; they are not a separate map layer.
8. Click a place in an expanded list or a visible map pin to zoom to it. The popup offers **Maps**, **Overview**, **Directions** (Google), **Apple Maps** on iPhone/iPad, and **Copy link**.
9. Use the **locate** control (◎) to center on your position (fails quietly if permission is denied).
10. **Reset** clears the search, re-enables all sources, turns the Tokusatsu chip off, clears the shared place hash, and collapses all region/area lists (theme is kept). The search box **x** only clears the query.
11. **Light / Dim / Dark** changes the sidebar UI only; the map always uses free OpenStreetMap tiles. First visit follows your system light/dark preference unless you pick a theme.
12. Filters and the last place are remembered in the browser. You can share a view with URL params like `?g=Tokyo,Kansai&q=ghibli&t=1` and `#p=…` (opening a `#p=` link shows that place even if its region was off).
13. Map pins are **color-tinted by region** (nine geographic colors). Tokusatsu is a badge/filter, not a separate pin color.
14. **Keyboard:** `/` focuses search; ↑/↓ move among visible list rows (region/area headers and places); Enter opens the focused or active place; Escape blurs the search box. Typing in the search field is not intercepted (except Escape).

No build step is required to *view* the map once `index.html` exists — including a copy saved to your phone, laptop, or cloud drive.

### Place data shape (build output)

`window.PLACES_DATA` (inlined into `index.html` by `build-places.py`):

```json
{
  "order": ["Tokyo", "Kanto", "Chubu", "Kansai", "Chugoku", "Shikoku", "Kyushu", "Tohoku", "Hokkaido"],
  "bounds": { "Tokyo": [minLat, minLng, maxLat, maxLng] },
  "groups": {
    "Tokyo": [
      {
        "id": "…",
        "name": "Tokyo Tower (東京タワー)",
        "lat": 35.65858,
        "lng": 139.74543,
        "url": "https://…",
        "overviewUrl": "https://…",
        "q": "tokyo tower … ultraman",
        "area": "Minato",
        "tokusatsu": true,
        "tokusatsuArea": "TOKYO — Ultraman"
      }
    ]
  }
}
```

- **Folders → area:** KML `Folder` ancestors become `area` (wards, cities). Places with no folder list under the region.
- **Overrides:** `app/name_overrides.json` is the human-accepted source of truth for display names/aliases. Do not auto-apply enrich proposals.
- **Enrich:** `app/enrich_places.py` is optional/offline-cache rebuild only. **Never geocode in the browser.**
- **CDN:** Leaflet and MarkerCluster load from unpkg with Subresource Integrity (`integrity` + `crossorigin`). Map tiles (OSM) do not use SRI.

## Updating locations

Requires Python 3 and `pykakasi` (romaji name normalization):

```bash
pip3 install pykakasi
```

### Map only (KML or Tokusatsu changes)

After editing `KML/` or `Locations/Tokusatsu.md`:

```bash
python3 app/build-places.py
```

### KML changes + region markdown files

To refresh `Locations/*.md` from KML and rebuild the map:

```bash
python3 app/build-locations.py && python3 app/build-places.py
```

`build-locations.py` deletes and regenerates all `Locations/*.md` **except** `Tokusatsu.md`.

Then refresh `index.html` in the browser (or push to GitHub to update Pages).

## Data sources

The map loads **9 regions** (~2,250 places after de-duplication):

- Tokyo, Kanto, Chubu, Kansai, Chugoku, Shikoku, Kyushu, Tohoku, Hokkaido (from `KML/`)
- Tokusatsu filming sites (from `Locations/Tokusatsu.md`) are **tagged** onto those regions (not a separate map layer). Use the **Tokusatsu** chip to show only tagged places.

Group names in the app are simplified (e.g. `[1] Kanto - 脚下照顧` → `Kanto`, `TOKYO` → `Tokyo`).

### Place names and links

- Names with Japanese script are shown as **`Romaji (original)`** when applicable.
- KML folders (wards, cities) are stored as each place’s **area**. Numbered waypoints become `{area} · 1`.
- KML entries that mix a regionalized label (e.g. Portuguese) with Japanese use the Japanese name for display normalization and for Google Maps links.
- A few famous Portuguese-only labels are corrected at build time via `app/name_overrides.json` (e.g. Torre de Tóquio → Tokyo Tower).
- Google Maps URLs use **`query={name},{lat},{lng}`** — Japanese-only in the query when the source name contains Japanese.
- Overview search uses **`{label}, {region}, Japan`** (Japanese label when present) via Google Search in a new tab.
- Tokusatsu links use the place name from the markdown table plus coordinates.

### Optional enrich (names / locality)

`python3 app/enrich_places.py` is optional. It fetches GSI + Nominatim for the dirty name subset only, rate-limits politely, and writes `app/enrich_cache.json` plus `app/enrich-proposals.md` for human review. It never auto-applies overrides or moves pins. A normal map rebuild with `build-places.py` stays offline and does not need this step.

## Favicon

**`favicon.png`** lives at the repo root (next to `index.html`).

## GitHub Pages

Publish the **root** of this repository (not `/app`). Served at `https://cobalto.dev/japantravelplan/`.