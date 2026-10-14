"""Build-time place parsing: KML folders, Tokusatsu tags, dedup, search haystacks.

This module is offline. Do not fetch geocoders here.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from normalize_names import (
    fold_for_search,
    google_maps_url,
    google_overview_url,
    has_japanese,
    japanese_original_name,
    normalize_group_name,
    normalize_place_name,
)

NS = {"kml": "http://www.opengis.net/kml/2.2"}
COORD_DP = 5
TOKUSATSU_NEAR_M = 100

REGIONAL_LATIN_RE = re.compile(
    r"[ãõç]|"
    r"\bmuseu\b|\bpal[aá]cio\b|\best[uú]dio\b|\bcemit[eé]rio\b|\bsantu[aá]rio\b|"
    r"\bzool[oó]gico\b|\baqu[aá]rio\b|\bgaleria\b|\best[aá]tua\b|\bjardim\b|"
    r"\brua de\b|\btorre de\b|\bhospedagem\b|\bquioto\b|\bjap[aã]o\b",
    re.IGNORECASE,
)

GENERIC_RE = re.compile(
    r"""^(
        \d+(?:\.\d+)?
        |Hospedagem\s+\d+
        |Linha\s+\d+
        |Mountain
        |Park
        |Lookout
        |Photo\s+point
    )$""",
    re.IGNORECASE | re.VERBOSE,
)

# Markdown **SECTION** first Latin token → geographic KML group.
TOKUSATSU_SECTION_GROUP = {
    "TOKYO": "Tokyo",
    "KANAGAWA": "Kanto",
    "SAITAMA": "Kanto",
    "CHIBA": "Kanto",
    "GUNMA": "Kanto",
    "IBARAKI": "Kanto",
    "SHIZUOKA": "Chubu",
    "KYOTO": "Kansai",
    "FUKUSHIMA": "Tohoku",
    "OKINAWA": "Kyushu",
}


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _el_name(el: ET.Element) -> str:
    name_el = el.find("kml:name", NS)
    if name_el is None:
        name_el = el.find("name")
    if name_el is None or not name_el.text:
        return ""
    return name_el.text.strip()


def coord_key(lat: float, lng: float) -> tuple[float, float]:
    return (round(lat, COORD_DP), round(lng, COORD_DP))


def is_generic_name(name: str) -> bool:
    return bool(GENERIC_RE.match(name.strip()))


def normalize_area_name(name: str | None) -> str | None:
    if not name:
        return None
    cleaned = normalize_group_name(name)
    return cleaned or None


def place_id(group: str, lat: float, lng: float, source: str) -> str:
    raw = f"{group}|{lat:.5f}|{lng:.5f}|{source}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _parse_coordinates(text: str) -> tuple[float, float] | None:
    parts = text.strip().split(",")
    if len(parts) < 2:
        return None
    try:
        lng, lat = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    return lat, lng


def load_overrides(path: Path) -> dict:
    if not path.exists():
        return {"byName": {}, "byCoord": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "byName": data.get("byName") or {},
        "byCoord": data.get("byCoord") or {},
    }


def apply_override(place: dict, overrides: dict) -> dict | None:
    """Return override record if one matches, else None."""
    key = f"{place['lat']:.5f},{place['lng']:.5f}"
    rec = overrides.get("byCoord", {}).get(key)
    if rec:
        return rec
    return overrides.get("byName", {}).get(place["name"]) or overrides.get("byName", {}).get(
        place["source"]
    )


def display_name_for(source: str, area: str | None, overrides: dict, lat: float, lng: float) -> tuple[str, list[str]]:
    base = {
        "source": source,
        "name": normalize_place_name(source),
        "lat": lat,
        "lng": lng,
    }
    ov = apply_override(base, overrides)
    aliases: list[str] = []
    if ov:
        name = ov.get("name") or base["name"]
        aliases = list(ov.get("aliases") or [])
        aliases.append(base["name"])
        aliases.append(source)
    else:
        name = base["name"]
        if is_generic_name(source) or is_generic_name(name):
            label = source if is_generic_name(source) else name
            if area:
                name = f"{area} · {label}"
            else:
                name = label
    return name, aliases


def build_q(name: str, source: str, group: str, area: str | None, aliases: list[str], extra: list[str]) -> str:
    parts = [name, source, group]
    if area:
        parts.append(area)
    parts.extend(aliases)
    parts.extend(extra)
    jp = japanese_original_name(source) or japanese_original_name(name)
    if jp:
        parts.append(jp)
    seen: set[str] = set()
    out: list[str] = []
    for item in parts:
        for token in fold_for_search(item).split():
            if token and token not in seen:
                seen.add(token)
                out.append(token)
    return " ".join(out)


def _placemark_to_place(
    pm: ET.Element,
    group: str,
    folder_path: list[str],
    overrides: dict,
) -> dict | None:
    original = _el_name(pm)
    coords_el = pm.find(".//kml:coordinates", NS)
    if coords_el is None:
        coords_el = pm.find(".//coordinates")
    if not original or coords_el is None or not coords_el.text:
        return None
    parsed = _parse_coordinates(coords_el.text)
    if not parsed:
        return None
    lat, lng = parsed
    area = normalize_area_name(folder_path[-1]) if folder_path else None
    name, aliases = display_name_for(original, area, overrides, lat, lng)
    probe = {
        "source": original,
        "name": normalize_place_name(original),
        "lat": lat,
        "lng": lng,
    }
    maps_source = name if apply_override(probe, overrides) else original
    return {
        "source": original,
        "name": name,
        "area": area,
        "lat": lat,
        "lng": lng,
        "group": group,
        "url": google_maps_url(maps_source, lat, lng),
        "overviewUrl": google_overview_url(maps_source, group),
        "aliases": aliases,
        "tokusatsu": False,
        "tokusatsuArea": None,
        "generic": is_generic_name(original),
    }


def _walk_kml(el: ET.Element, group: str, folder_path: list[str], overrides: dict) -> list[dict]:
    places: list[dict] = []
    for child in list(el):
        tag = _local(child.tag)
        if tag == "Folder":
            fname = _el_name(child)
            nxt = folder_path + [fname] if fname else folder_path
            places.extend(_walk_kml(child, group, nxt, overrides))
        elif tag == "Document":
            places.extend(_walk_kml(child, group, folder_path, overrides))
        elif tag == "Placemark":
            place = _placemark_to_place(child, group, folder_path, overrides)
            if place:
                places.append(place)
    return places


def parse_kml_file(path: Path, overrides: dict | None = None) -> tuple[str, list[dict]]:
    overrides = overrides or {"byName": {}, "byCoord": {}}
    tree = ET.parse(path)
    root = tree.getroot()
    group_name = normalize_group_name(path.stem)
    name_el = root.find(".//kml:Document/kml:name", NS)
    if name_el is not None and name_el.text:
        group_name = normalize_group_name(name_el.text.strip())
    places = _walk_kml(root, group_name, [], overrides)
    for p in places:
        p["group"] = group_name
    return group_name, places


def parse_tokusatsu_header(cell: str) -> tuple[str, str]:
    """Return (tokusatsuArea, section_key)."""
    text = cell.strip().strip("*").strip()
    m = re.match(r"([A-Za-z]+)", text)
    key = m.group(1).upper() if m else ""
    return text, key


def parse_tokusatsu_md(path: Path, overrides: dict | None = None) -> list[dict]:
    overrides = overrides or {"byName": {}, "byCoord": {}}
    places: list[dict] = []
    area_label = ""
    section_key = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("| ---"):
            continue
        cols = [c.strip() for c in line.split("|")[1:-1]]
        if len(cols) < 2:
            continue
        name_cell = cols[0]
        if name_cell.startswith("**") or (name_cell.startswith("*") and not cols[1]):
            area_label, section_key = parse_tokusatsu_header(name_cell)
            continue
        if not cols[1] or cols[1] == "Coordinates":
            continue
        parsed = _parse_coordinates(cols[1])
        if not parsed:
            continue
        lat, lng = parsed
        source = name_cell
        group = TOKUSATSU_SECTION_GROUP.get(section_key, "")
        area = None
        if group:
            # Locality from section (TOKYO) or parenthetical in the name.
            area = normalize_area_name(section_key.title() if section_key else None)
            m = re.search(r"\(([^)]+)\)", source)
            if m and not has_japanese(m.group(1)):
                area = normalize_area_name(m.group(1).split(",")[0].strip()) or area
        name, aliases = display_name_for(source, area, overrides, lat, lng)
        url = cols[2] if len(cols) > 2 and cols[2].startswith("http") else google_maps_url(source, lat, lng)
        places.append({
            "source": source,
            "name": name,
            "area": area,
            "lat": lat,
            "lng": lng,
            "group": group,
            "url": url,
            "overviewUrl": google_overview_url(source, group or "Japan"),
            "aliases": aliases,
            "tokusatsu": True,
            "tokusatsuArea": area_label or None,
            "generic": is_generic_name(source),
            "section_key": section_key,
        })
    return places


def _name_tokens(text: str) -> set[str]:
    return {t for t in fold_for_search(text).split() if len(t) >= 4}


def _names_overlap(a: str, b: str) -> bool:
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        # short names: require a folded substring
        fa, fb = fold_for_search(a), fold_for_search(b)
        return bool(fa) and (fa in fb or fb in fa)
    return bool(ta & tb)


def dedup_within_group(places: list[dict]) -> tuple[list[dict], list[dict]]:
    kept: list[dict] = []
    dropped: list[dict] = []
    seen: dict[tuple[float, float], dict] = {}
    for p in places:
        key = coord_key(p["lat"], p["lng"])
        if key in seen:
            dropped.append({**p, "droppedBy": seen[key]["name"], "reason": "same-file coords"})
            continue
        seen[key] = p
        kept.append(p)
    return kept, dropped


def dedup_across_groups(ordered: list[tuple[str, list[dict]]]) -> tuple[list[tuple[str, list[dict]]], list[dict]]:
    seen: dict[tuple[float, float], tuple[str, dict]] = {}
    out: list[tuple[str, list[dict]]] = []
    dropped: list[dict] = []
    for group, places in ordered:
        kept: list[dict] = []
        for p in places:
            key = coord_key(p["lat"], p["lng"])
            if key in seen:
                prev_group, prev = seen[key]
                dropped.append({
                    **p,
                    "droppedBy": prev["name"],
                    "droppedByGroup": prev_group,
                    "reason": "cross-file coords",
                })
                continue
            seen[key] = (group, p)
            kept.append(p)
        out.append((group, kept))
    return out, dropped


def merge_tokusatsu(
    groups: list[tuple[str, list[dict]]],
    tokusatsu: list[dict],
) -> tuple[list[tuple[str, list[dict]]], list[dict], list[dict]]:
    """Attach tokusatsu tags; unmatched rows are appended to their mapped group."""
    index: list[tuple[str, dict]] = []
    by_coord: dict[tuple[float, float], tuple[str, dict]] = {}
    for group, places in groups:
        for p in places:
            index.append((group, p))
            by_coord[coord_key(p["lat"], p["lng"])] = (group, p)

    merged: list[dict] = []
    only: list[dict] = []

    for t in tokusatsu:
        key = coord_key(t["lat"], t["lng"])
        hit = by_coord.get(key)
        if not hit:
            best = None
            best_d = TOKUSATSU_NEAR_M + 1
            for group, p in index:
                d = haversine_m(t["lat"], t["lng"], p["lat"], p["lng"])
                if d <= TOKUSATSU_NEAR_M and d < best_d and (
                    _names_overlap(t["name"], p["name"])
                    or _names_overlap(t["source"], p["source"])
                    or _names_overlap(t["source"], p["name"])
                ):
                    best = (group, p)
                    best_d = d
            hit = best
        if hit:
            _group, p = hit
            p["tokusatsu"] = True
            if t.get("tokusatsuArea"):
                p["tokusatsuArea"] = t["tokusatsuArea"]
            p.setdefault("aliases", []).extend([t["name"], t["source"]])
            extra = [t.get("tokusatsuArea") or "", "tokusatsu"]
            p["_tokusatsu_extra"] = extra
            merged.append({"tokusatsu": t["name"], "into": p["name"], "group": p["group"]})
        else:
            only.append(t)

    group_map = {g: list(places) for g, places in groups}
    order = [g for g, _ in groups]
    unmatched_section: list[dict] = []
    for t in only:
        dest = t.get("group") or (order[0] if order else "Tokyo")
        if dest not in group_map:
            unmatched_section.append(t)
            dest = order[0] if order else "Tokyo"
            t["group"] = dest
        else:
            t["group"] = dest
        t["tokusatsu"] = True
        group_map[dest].append(t)

    rebuilt = [(g, group_map[g]) for g in order]
    return rebuilt, merged, only


def _finish_place(p: dict) -> dict:
    extra = list(p.get("_tokusatsu_extra") or [])
    if p.get("tokusatsu"):
        extra.append("tokusatsu")
        if p.get("tokusatsuArea"):
            extra.append(p["tokusatsuArea"])
    p["id"] = place_id(p["group"], p["lat"], p["lng"], p["source"])
    p["q"] = build_q(
        p["name"],
        p["source"],
        p["group"],
        p.get("area"),
        list(p.get("aliases") or []),
        extra,
    )
    return p


def compact_place(p: dict) -> dict:
    out = {
        "id": p["id"],
        "name": p["name"],
        "lat": p["lat"],
        "lng": p["lng"],
        "url": p["url"],
        "overviewUrl": p["overviewUrl"],
        "q": p["q"],
    }
    if p.get("area"):
        out["area"] = p["area"]
    if p.get("tokusatsu"):
        out["tokusatsu"] = True
        if p.get("tokusatsuArea"):
            out["tokusatsuArea"] = p["tokusatsuArea"]
    return out


def group_bounds(places: list[dict]) -> list[float] | None:
    if not places:
        return None
    lats = [p["lat"] for p in places]
    lngs = [p["lng"] for p in places]
    return [min(lats), min(lngs), max(lats), max(lngs)]


def assemble_payload(
    kml_groups: list[tuple[str, list[dict]]],
    tokusatsu: list[dict],
) -> tuple[dict, dict]:
    within_dropped: list[dict] = []
    cleaned: list[tuple[str, list[dict]]] = []
    for group, places in kml_groups:
        kept, dropped = dedup_within_group(places)
        within_dropped.extend({"group": group, **d} for d in dropped)
        cleaned.append((group, kept))

    across, across_dropped = dedup_across_groups(cleaned)
    merged_groups, tok_merged, tok_only = merge_tokusatsu(across, tokusatsu)

    groups_out: dict[str, list[dict]] = {}
    bounds: dict[str, list[float]] = {}
    order: list[str] = []
    for group, places in merged_groups:
        finished = [_finish_place(p) for p in places]
        groups_out[group] = [compact_place(p) for p in finished]
        b = group_bounds(finished)
        if b:
            bounds[group] = b
        order.append(group)

    payload = {"order": order, "bounds": bounds, "groups": groups_out}

    def needs_help(p: dict) -> bool:
        if p.get("generic"):
            return True
        display = p.get("name") or ""
        latin = re.sub(
            r"[\u3040-\u30ff\u4e00-\u9fff\u3400-\u4dbf\uff66-\uff9f()]",
            " ",
            display,
        )
        return bool(REGIONAL_LATIN_RE.search(latin))

    help_rows = []
    for group, places in merged_groups:
        for p in places:
            if needs_help(p):
                help_rows.append(f"{group} | {p.get('area') or '—'} | {p['name']}")

    report = {
        "within_dropped": within_dropped,
        "across_dropped": across_dropped,
        "tokusatsu_merged": tok_merged,
        "tokusatsu_only": [{"name": t["name"], "group": t.get("group")} for t in tok_only],
        "needs_name_help": help_rows,
        "counts": {g: len(ps) for g, ps in groups_out.items()},
        "tokusatsu_tagged": sum(1 for ps in groups_out.values() for p in ps if p.get("tokusatsu")),
    }
    return payload, report


def format_report(report: dict) -> str:
    lines = []
    counts = report["counts"]
    total = sum(counts.values())
    lines.append(f"Places: {total} in {len(counts)} groups (Tokusatsu is a tag, not a group)")
    for g, n in counts.items():
        lines.append(f"  {g}: {n}")
    lines.append(f"Tokusatsu tagged: {report['tokusatsu_tagged']}")
    lines.append(f"Tokusatsu merged onto existing pins: {len(report['tokusatsu_merged'])}")
    lines.append(f"Tokusatsu-only (new pins): {len(report['tokusatsu_only'])}")
    lines.append(f"Dropped same-file dupes: {len(report['within_dropped'])}")
    for d in report["within_dropped"][:20]:
        lines.append(f"  - {d.get('group')}: {d.get('name')} ({d.get('reason')})")
    lines.append(f"Dropped cross-file dupes: {len(report['across_dropped'])}")
    for d in report["across_dropped"][:30]:
        lines.append(
            f"  - {d.get('group')}: {d.get('name')} kept in {d.get('droppedByGroup')} as {d.get('droppedBy')}"
        )
    lines.append(f"Needs name help (generic / Portuguese-looking): {len(report['needs_name_help'])}")
    for row in report["needs_name_help"][:40]:
        lines.append(f"  - {row}")
    if len(report["needs_name_help"]) > 40:
        lines.append(f"  … {len(report['needs_name_help']) - 40} more")
    return "\n".join(lines) + "\n"


def build_from_dirs(kml_dir: Path, tokusatsu_path: Path | None, overrides_path: Path) -> tuple[dict, dict]:
    overrides = load_overrides(overrides_path)
    kml_groups: list[tuple[str, list[dict]]] = []
    for kml in sorted(kml_dir.glob("*.kml")):
        kml_groups.append(parse_kml_file(kml, overrides))
    tokusatsu: list[dict] = []
    if tokusatsu_path and tokusatsu_path.exists():
        tokusatsu = parse_tokusatsu_md(tokusatsu_path, overrides)
    return assemble_payload(kml_groups, tokusatsu)
