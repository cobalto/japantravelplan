#!/usr/bin/env python3
"""Regenerate Locations/*.md from KML/*.kml (skips Tokusatsu.md)."""
import xml.etree.ElementTree as ET
from pathlib import Path

from normalize_names import google_maps_url, normalize_group_name, normalize_place_name

APP = Path(__file__).resolve().parent
ROOT = APP.parent
KML_DIR = ROOT / "KML"
LOC_DIR = ROOT / "Locations"
NS = {"kml": "http://www.opengis.net/kml/2.2"}
OLD_MD_GLOB = "[[]*]*.md"


def parse_kml(path: Path) -> tuple[str, list[dict]]:
    tree = ET.parse(path)
    doc = tree.getroot()
    group_name = normalize_group_name(path.stem)
    name_el = doc.find(".//kml:Document/kml:name", NS)
    if name_el is not None and name_el.text:
        group_name = normalize_group_name(name_el.text.strip())

    places = []
    for pm in doc.findall(".//kml:Placemark", NS):
        name_el = pm.find("kml:name", NS)
        coords_el = pm.find(".//kml:coordinates", NS)
        if name_el is None or not name_el.text or coords_el is None or not coords_el.text:
            continue
        original = name_el.text.strip()
        parts = coords_el.text.strip().split(",")
        if len(parts) < 2:
            continue
        lng, lat = float(parts[0]), float(parts[1])
        places.append({
            "original": original,
            "name": normalize_place_name(original),
            "lat": lat,
            "lng": lng,
        })
    return group_name, places


def render_md(group_name: str, places: list[dict]) -> str:
    lines = [
        f"# {group_name}",
        "",
        "| Name | Coordinates | Google Maps |",
        "| --- | --- | --- |",
    ]
    for p in places:
        coords = f"{p['lng']},{p['lat']},0"
        url = google_maps_url(p["original"], p["lat"], p["lng"])
        lines.append(f"| {p['name']} | {coords} | {url} |")
    lines.append("")
    return "\n".join(lines)


def main():
    LOC_DIR.mkdir(exist_ok=True)

    for old in LOC_DIR.glob("*.md"):
        if old.name == "Tokusatsu.md":
            continue
        old.unlink()

    total = 0
    for kml in sorted(KML_DIR.glob("*.kml")):
        group_name, places = parse_kml(kml)
        out = LOC_DIR / f"{group_name}.md"
        out.write_text(render_md(group_name, places), encoding="utf-8")
        total += len(places)
        print(f"  {out.name}: {len(places)} places")

    print(f"Wrote {total} places to {LOC_DIR} (Tokusatsu.md untouched)")


if __name__ == "__main__":
    main()