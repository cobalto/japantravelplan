#!/usr/bin/env python3
"""Regenerate Locations/*.md from KML/*.kml (skips Tokusatsu.md, Otaku.md and Manual.md)."""
from pathlib import Path

from parse_places import load_overrides, parse_kml_file
from normalize_names import google_maps_url

APP = Path(__file__).resolve().parent
ROOT = APP.parent
KML_DIR = ROOT / "KML"
LOC_DIR = ROOT / "Locations"
OVERRIDES = APP / "name_overrides.json"


def render_md(group_name: str, places: list[dict]) -> str:
    lines = [
        f"# {group_name}",
        "",
        "| Name | Area | Coordinates | Google Maps |",
        "| --- | --- | --- | --- |",
    ]
    for p in places:
        coords = f"{p['lng']},{p['lat']},0"
        url = google_maps_url(p["source"], p["lat"], p["lng"])
        area = p.get("area") or ""
        lines.append(f"| {p['name']} | {area} | {coords} | {url} |")
    lines.append("")
    return "\n".join(lines)


def main():
    LOC_DIR.mkdir(exist_ok=True)
    overrides = load_overrides(OVERRIDES)

    for old in LOC_DIR.glob("*.md"):
        if old.name in ("Tokusatsu.md", "Otaku.md", "Manual.md"):
            continue
        old.unlink()

    total = 0
    for kml in sorted(KML_DIR.glob("*.kml")):
        group_name, places = parse_kml_file(kml, overrides)
        out = LOC_DIR / f"{group_name}.md"
        out.write_text(render_md(group_name, places), encoding="utf-8")
        total += len(places)
        print(f"  {out.name}: {len(places)} places")

    print(f"Wrote {total} places to {LOC_DIR} (Tokusatsu.md, Otaku.md and Manual.md untouched)")


if __name__ == "__main__":
    main()
