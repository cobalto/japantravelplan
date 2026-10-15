#!/usr/bin/env bash
# One-command refresh after the trip sheet changes:
#   app/sync_otaku_sheet.sh [path/to/Japan_Trip_Trump_Cards.xlsx]
# Re-reads the xlsx, geocodes new places (cached, 1 req/s), rebuilds index.html, runs the
# tests and prints what changed in Locations/Otaku.md and the build report.
set -euo pipefail
cd "$(dirname "$0")/.."
# Sheet path: first argument, else OTAKU_SHEET, else DEFAULT_XLSX in app/import_otaku_sheet.py.
python3 app/import_otaku_sheet.py "$@"
python3 app/build-places.py | grep -E "^(Places|Otaku|  [=?])" || true
python3 -m unittest discover -s app -q
echo "--- changes ---"
git --no-pager diff --stat -- Locations/Otaku.md app/otaku-import-report.md app/otaku_geocode_cache.json index.html
git --no-pager diff --unified=0 -- Locations/Otaku.md | grep -E '^[+-]\|' | cut -c1-120 || echo "(no place changes)"
