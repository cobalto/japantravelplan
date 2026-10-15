#!/usr/bin/env python3
"""Regenerate /index.html with inlined place data from KML/*.kml Locations/Tokusatsu.md and Locations/Otaku.md.

Offline. Does not call geocoders. Optional enrich cache is applied only via name_overrides.json.
"""
import json
from pathlib import Path

from parse_places import build_from_dirs, format_report

APP = Path(__file__).resolve().parent
ROOT = APP.parent
KML_DIR = ROOT / "KML"
LOC_DIR = ROOT / "Locations"
SHELL = APP / "index.shell.html"
OUT_HTML = ROOT / "index.html"
OUT_JS = APP / "places.js"
OVERRIDES = APP / "name_overrides.json"
REPORT = APP / "build-report.txt"


def main():
    payload, report = build_from_dirs(KML_DIR, LOC_DIR / "Tokusatsu.md", OVERRIDES, LOC_DIR / "Otaku.md", LOC_DIR / "Manual.md")

    pretty = json.dumps(payload, ensure_ascii=False, indent=2)
    compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    OUT_JS.write_text("window.PLACES_DATA = " + pretty + ";\n", encoding="utf-8")

    shell = SHELL.read_text(encoding="utf-8")
    inline = "  <script>\n  window.PLACES_DATA = " + compact + ";\n  </script>"
    html = shell.replace("  PLACES_DATA_PLACEHOLDER", inline)
    OUT_HTML.write_text(html, encoding="utf-8")

    text = format_report(report)
    REPORT.write_text(text, encoding="utf-8")
    print(text.rstrip())
    print(f"  -> {OUT_HTML} (open this in your browser)")
    print(f"  -> {OUT_JS} (pretty reference copy)")
    print(f"  -> {REPORT}")


if __name__ == "__main__":
    main()
