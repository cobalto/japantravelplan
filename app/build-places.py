#!/usr/bin/env python3
"""Regenerate /index.html with inlined place data from KML/*.kml and Locations/Tokusatsu.md"""
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from normalize_names import (
    google_maps_url,
    google_overview_url,
    normalize_group_name,
    normalize_place_name,
)

APP = Path(__file__).resolve().parent
ROOT = APP.parent
KML_DIR = ROOT / "KML"
LOC_DIR = ROOT / "Locations"
SHELL = APP / "index.shell.html"
OUT_HTML = ROOT / "index.html"
OUT_JS = APP / "places.js"
NS = {"kml": "http://www.opengis.net/kml/2.2"}


def parse_kml(path):
    tree = ET.parse(path)
    doc = tree.getroot()
    group_name = path.stem
    name_el = doc.find(".//kml:Document/kml:name", NS)
    if name_el is not None and name_el.text:
        group_name = name_el.text.strip()
    gname = normalize_group_name(group_name)
    places = []
    for pm in doc.findall(".//kml:Placemark", NS):
        name_el = pm.find("kml:name", NS)
        coords_el = pm.find(".//kml:coordinates", NS)
        if name_el is None or not name_el.text or coords_el is None or not coords_el.text:
            continue
        parts = coords_el.text.strip().split(",")
        if len(parts) < 2:
            continue
        original = name_el.text.strip()
        lat, lng = float(parts[1]), float(parts[0])
        places.append({
            "name": normalize_place_name(original),
            "lat": lat,
            "lng": lng,
            "url": google_maps_url(original, lat, lng),
            "overviewUrl": google_overview_url(original, gname),
        })
    return gname, places


def parse_md(path):
    region = normalize_group_name(path.stem)
    places = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("| ---"):
            continue
        cols = [c.strip() for c in line.split("|")[1:-1]]
        if len(cols) < 2 or not cols[1] or cols[1] == "Coordinates" or cols[0].startswith("**"):
            continue
        parts = cols[1].split(",")
        if len(parts) < 2:
            continue
        lat, lng = float(parts[1]), float(parts[0])
        url = cols[2] if len(cols) > 2 and cols[2].startswith("http") else google_maps_url(cols[0], lat, lng)
        source = cols[0]
        places.append({
            "name": normalize_place_name(source),
            "lat": lat,
            "lng": lng,
            "url": url,
            "overviewUrl": google_overview_url(source, region),
        })
    return region, places


def main():
    groups, order = {}, []
    for kml in sorted(KML_DIR.glob("*.kml")):
        gname, places = parse_kml(kml)
        groups[gname] = places
        order.append(gname)
    tokusatsu = LOC_DIR / "Tokusatsu.md"
    if tokusatsu.exists():
        gname, places = parse_md(tokusatsu)
        groups[gname] = places
        order.append(gname)

    payload = {"order": order, "groups": groups}
    places_js = "window.PLACES_DATA = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n"
    OUT_JS.write_text(places_js, encoding="utf-8")

    shell = SHELL.read_text(encoding="utf-8")
    inline = "  <script>\n" + places_js.rstrip() + "\n  </script>"
    html = shell.replace("  PLACES_DATA_PLACEHOLDER", inline)
    OUT_HTML.write_text(html, encoding="utf-8")

    total = sum(len(v) for v in groups.values())
    print(f"Wrote {total} places in {len(groups)} groups")
    print(f"  -> {OUT_HTML} (open this in your browser)")
    print(f"  -> {OUT_JS} (reference copy)")


if __name__ == "__main__":
    main()