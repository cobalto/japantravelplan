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
| `app/build-places.py` | Rebuilds root `index.html` and `app/places.js` from `KML/` and `Locations/Tokusatsu.md`. |
| `app/build-locations.py` | Regenerates `Locations/{Region}.md` from KML (skips `Tokusatsu.md`). |
| `app/normalize_names.py` | Normalizes place and group names and builds Google Maps URLs during the build. |
| `app/places.js` | Reference copy of the place data (not loaded by the app). |
| `AGENTS.md` | Guide for LLM agents working in this repo (build rules, UI behavior, pitfalls). |

## Using the map

You only need **`index.html`** — copy or download that single file and open it in any modern browser. All place data is embedded inside it, so no server, install, or rest of the repo is required. You do need an **internet connection** while using the map so the app can load jQuery, Leaflet, and OpenStreetMap tiles from the web.

1. Open `index.html` locally (double-click or drag into a browser), or visit the hosted URL.
2. Use **Show on map** checkboxes to choose which sources appear on the map.
3. Unchecked regions are hidden on the map; their sidebar lists stay **collapsed** and cannot be expanded until re-enabled.
4. Search filters the **sidebar list only** (within currently checked sources). Matching groups auto-expand while searching.
5. Click a place in an expanded list or a visible map pin to zoom to it and open a popup with an **Open in Google Maps** link.
6. **Clear** resets the search, re-enables all sources, and collapses all region lists.
7. **Light / Dim / Dark** changes the sidebar theme only; the map always uses OpenStreetMap.

No build step is required to *view* the map once `index.html` exists — including a copy saved to your phone, laptop, or cloud drive.

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

The map loads **10 groups** (~2,280 places):

- Tokyo, Kanto, Chubu, Kansai, Chugoku, Shikoku, Kyushu, Tohoku, Hokkaido (from `KML/`)
- Tokusatsu (from `Locations/Tokusatsu.md`)

Group names in the app are simplified (e.g. `[1] Kanto - 脚下照顧` → `Kanto`, `TOKYO` → `Tokyo`).

### Place names and links

- Names with Japanese script are shown as **`Romaji (original)`** when applicable.
- KML entries that mix a regionalized label (e.g. Portuguese) with Japanese use the Japanese name for display normalization and for Google Maps links.
- Google Maps URLs use **`query={name},{lat},{lng}`** — Japanese-only in the query when the source name contains Japanese.
- Tokusatsu links use the place name from the markdown table plus coordinates.

## Favicon

**`favicon.png`** lives at the repo root (next to `index.html`).

## GitHub Pages

Publish the **root** of this repository (not `/app`). Served at `https://cobalto.dev/japantravelplan/`.