# Japan Travel Plan

A personal list of places to visit in Japan, with a simple browser map to browse and filter them.

Hosted at **[cobalto.dev/japantravelplan](https://cobalto.dev/japantravelplan)** (static site from repo root `index.html`).

## Project layout

| Path | Description |
|------|-------------|
| `index.html` | Built map app (generated — open this in a browser or serve from `/`). |
| `KML/` | Source location files (one per region + TOKYO). Primary data for the map. |
| `Locations/` | Markdown lists of places. `Tokusatsu.md` is also included in the map. |
| `app/index.shell.html` | UI template. Edit this for layout or behavior changes. |
| `app/build-places.py` | Rebuilds root `index.html` and `app/places.js` from `KML/` and `Locations/`. |
| `app/normalize_names.py` | Normalizes place and group names during the build. |
| `app/places.js` | Reference copy of the place data (not loaded by the app). |

## Using the map

You only need **`index.html`** — copy or download that single file and open it in any modern browser. All place data is embedded inside it, so no server, install, or rest of the repo is required. You do need an **internet connection** the first time (and while using the map) so the app can load jQuery, Leaflet, and OpenStreetMap tiles from the web.

1. Open `index.html` locally (double-click or drag into a browser), or visit the hosted URL.
2. Use **Show on map** checkboxes to choose which sources appear on the map.
3. Search filters the sidebar list (only within selected sources).
4. Click a place in the list or a marker on the map to zoom to it.
5. **Clear** resets the search and re-enables all sources.
6. **Light / Dim / Dark** changes the sidebar theme only; the map always uses OpenStreetMap.

No build step is required to *view* the map once `index.html` exists — including a copy saved to your phone, laptop, or cloud drive.

## Updating locations

After editing files in `KML/` or `Locations/Tokusatsu.md`:

```bash
python3 app/build-places.py
```

This requires Python 3 and `pykakasi` (for romaji name normalization):

```bash
pip3 install pykakasi
```

Then refresh `index.html` in the browser (or push to GitHub to update Pages).

## Favicon

Add **`favicon.png`** at the repo root (next to `index.html`).

## GitHub Pages

Publish the **root** of this repository (not `/app`). Served at `https://cobalto.dev/japantravelplan/`.

## Data sources

The map loads **10 groups** (~2,280 places):

- TOKYO
- Kanto, Chubu, Kansai, Chugoku, Shikoku, Kyushu, Tohoku, Hokkaido (from `KML/`)
- Tokusatsu (from `Locations/Tokusatsu.md`)

Group names in the app are simplified (e.g. `[1] Kanto - 脚下照顧` → `Kanto`). Place names with Japanese script are shown as `Romaji (original)`.