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
OTAKU_NEAR_M = 150
OTAKU_JA_NEAR_M = 2000  # shared Japanese name still needs to be in the same neighbourhood
OTAKU_GROUP = {
    "Tokyo": "Tokyo", "Saitama": "Kanto", "Ibaraki": "Kanto", "Kanagawa": "Kanto", "Chiba": "Kanto",
    "Tochigi": "Kanto", "Tokyo/Kanagawa": "Kanto", "Fukushima": "Tohoku", "Kansai": "Kansai",
    "Chubu": "Chubu", "Aichi": "Chubu", "Shizuoka": "Chubu", "Mie/Aichi": "Kansai", "Chugoku": "Chugoku",
}
# Reviewed pairs that must never merge: (substring of sheet name, substring of pin name),
# matched case/accent-insensitively. Applies to both the name rule and the Japanese-name rule.
OTAKU_NO_MERGE = [
    ("yanaka ginza", "gelato"),            # KML gelato shop (グルグルジェラート谷中銀座店) is not the street
    ("yanaka ginza", "asatte"),            # manual pin asatte (谷中3-10-14) is a shop, not the street
    ("meiji jingu", "gyoen"),              # inner garden, separate paid ticket
    ("midtown hibiya", "godzilla"),        # 有楽町1-1-2 vs Godzilla Square 有楽町1-2-2
    ("midtown hibiya", "ゴジラ"),
    ("tokyo midtown (", "hibiya"),         # row 40 Path of Light = Roppongi Midtown
    ("path of light", "hibiya"),
]
NAME_STOPWORDS = {
    "park", "station", "museum", "tokyo", "shrine", "temple", "street", "store", "shop", "area",
    "center", "centre", "city", "hall", "main", "the", "and", "garden", "gardens", "town", "road",
    "kyoto", "osaka", "yokohama", "japan", "cafe", "studio", "studios", "exterior",
    "market", "mercado", "parque", "museu",
}

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


def parse_manual_md(path: Path, overrides: dict | None = None) -> list[tuple[str, dict]]:
    """Parse Locations/Manual.md: hand-added untagged pins -> [(group, place)]."""
    overrides = overrides or {"byName": {}, "byCoord": {}}
    out: list[tuple[str, dict]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("| ---") or line.startswith("| Group "):
            continue
        cols = [c.strip() for c in line.split("|")[1:-1]]
        if len(cols) < 6:
            continue
        group, area, name, ja, coords, url = cols[:6]
        parsed = _parse_coordinates(coords)
        if not parsed or not group or not name:
            continue
        lat, lng = parsed
        source = f"{name} {ja}".strip()
        display = f"{name} ({ja})" if ja else name
        ov = apply_override({"source": source, "name": display, "lat": lat, "lng": lng}, overrides)
        if ov and ov.get("name"):
            display = ov["name"]
        out.append((group, {
            "source": source, "name": display, "ja": ja, "area": area or None, "lat": lat, "lng": lng,
            "group": group,
            "url": url if url.startswith("http") else google_maps_url(ja or name, lat, lng),
            "overviewUrl": google_overview_url(name, group),
            "aliases": [name] + ([ja] if ja else []),
            "tokusatsu": False, "tokusatsuArea": None, "generic": False, "origin": "manual",
        }))
    return out


def parse_otaku_md(path: Path, overrides: dict | None = None) -> list[dict]:
    """Parse Locations/Otaku.md (generated by app/import_otaku_sheet.py)."""
    overrides = overrides or {"byName": {}, "byCoord": {}}
    places: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("| ---") or line.startswith("| Name "):
            continue
        cols = [c.strip() for c in line.split("|")[1:-1]]
        if len(cols) < 7:
            continue
        name, ja, coords, url, region, category, notes = cols[:7]
        parsed = _parse_coordinates(coords)
        if not parsed:
            continue
        lat, lng = parsed
        source = f"{name} {ja}".strip()
        group = OTAKU_GROUP.get(region, "")
        display = f"{name} ({ja})" if ja else name
        ov = apply_override({"source": source, "name": display, "lat": lat, "lng": lng}, overrides)
        if ov and ov.get("name"):
            display = ov["name"]
        places.append({
            "source": source,
            "name": display,
            "ja": ja,
            "area": region or None,
            "lat": lat,
            "lng": lng,
            "group": group,
            "url": url if url.startswith("http") else google_maps_url(ja or name, lat, lng),
            "overviewUrl": google_overview_url(ja or name, group or "Japan"),
            "aliases": [name] + ([ja] if ja else []),
            "tokusatsu": False,
            "tokusatsuArea": None,
            "otaku": True,
            "otakuNotes": notes.replace(" ¶ ", "\n"),
            "otakuCategory": category,
            "generic": False,
            "origin": "otaku",
        })
    return places


def _match_tokens(text: str) -> set[str]:
    return {t for t in fold_for_search(text).split() if len(t) >= 3 and t not in NAME_STOPWORDS and not t.isdigit() and not has_japanese(t)}


def _place_ja(p: dict) -> str:
    return p.get("ja") or japanese_original_name(p.get("source") or "") or japanese_original_name(p.get("name") or "") or ""


def otaku_blocked(a: dict, b: dict) -> bool:
    fa, fb = a["name"].lower(), b["name"].lower()
    fa2, fb2 = fold_for_search(a["name"]), fold_for_search(b["name"])
    return any((x in fa or x in fa2) and (y in fb or y in fb2) for x, y in OTAKU_NO_MERGE)


def otaku_names_match(a: dict, b: dict) -> bool:
    """Normalized-name match: token Jaccard >= 0.5 (or containment, or >= 3 shared tokens) between the sheet name and the pin's name/source.

    Generic pins ("Linha 38") never name-match. Use OTAKU_NO_MERGE for reviewed false positives.
    """
    if b.get("generic") or otaku_blocked(a, b):
        return False
    ta = _match_tokens(a["name"])
    for text in (b["name"], b.get("source") or ""):
        tb = _match_tokens(text)
        if not ta or not tb:
            continue
        if len(ta & tb) / len(ta | tb) >= 0.5 or ta <= tb or tb <= ta or len(ta & tb) >= 3:
            return True
    return False


def merge_otaku(
    groups: list[tuple[str, list[dict]]],
    otaku: list[dict],
) -> tuple[list[tuple[str, list[dict]]], list[dict], list[dict], list[dict]]:
    """Tag existing pins (KML or Tokusatsu) with otaku=True; append the rest to their region group.

    Match = (normalized-name token overlap and <= OTAKU_NEAR_M) or (identical Japanese name and
    <= OTAKU_JA_NEAR_M). Unmatched pins within OTAKU_NEAR_M are reported as ambiguous near-matches.
    Returns (groups, merged, only, ambiguous).
    """
    index = [(g, p) for g, ps in groups for p in ps]
    merged: list[dict] = []
    only: list[dict] = []
    ambiguous: list[dict] = []
    for o in otaku:
        o_ja = _place_ja(o)
        best = None
        best_d = float("inf")
        near: list[tuple[float, dict]] = []
        for g, p in index:
            d = haversine_m(o["lat"], o["lng"], p["lat"], p["lng"])
            if d > OTAKU_JA_NEAR_M:
                continue
            p_ja = _place_ja(p)
            ok = (d <= OTAKU_NEAR_M and otaku_names_match(o, p)) or (
                bool(o_ja) and o_ja == p_ja and not otaku_blocked(o, p))
            if ok and d < best_d:
                best, best_d = p, d
            elif d <= OTAKU_NEAR_M:
                near.append((d, p))
        if best is not None:
            best["otaku"] = True
            best["otakuNotes"] = "\n".join(x for x in (best.get("otakuNotes"), o["otakuNotes"]) if x)
            best["otakuCategory"] = best.get("otakuCategory") or o.get("otakuCategory")
            best.setdefault("aliases", []).extend([o["name"]] + list(o.get("aliases") or []))
            kind = "tokusatsu" if best.get("tokusatsu") else "kml"
            if best.get("origin") == "otaku":
                kind = "otaku"
            merged.append({"otaku": o["name"], "into": best["name"], "group": best["group"],
                           "kind": kind, "distance_m": round(best_d)})
        else:
            only.append(o)
            index.append((o.get("group"), o))
            for d, p in sorted(near, key=lambda x: x[0])[:3]:
                ambiguous.append({"otaku": o["name"], "near": p["name"], "group": p["group"],
                                  "distance_m": round(d)})
    group_map = {g: list(ps) for g, ps in groups}
    order = [g for g, _ in groups]
    for o in only:
        dest = o.get("group") if o.get("group") in group_map else (order[0] if order else "Tokyo")
        o["group"] = dest
        group_map[dest].append(o)
    return [(g, group_map[g]) for g in order], merged, only, ambiguous


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
    if p.get("otaku"):
        extra.append("otaku")
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
    if p.get("otaku"):
        out["otaku"] = True
        if p.get("otakuNotes"):
            out["otakuNotes"] = p["otakuNotes"]
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
    otaku: list[dict] | None = None,
) -> tuple[dict, dict]:
    within_dropped: list[dict] = []
    cleaned: list[tuple[str, list[dict]]] = []
    for group, places in kml_groups:
        kept, dropped = dedup_within_group(places)
        within_dropped.extend({"group": group, **d} for d in dropped)
        cleaned.append((group, kept))

    across, across_dropped = dedup_across_groups(cleaned)
    merged_groups, tok_merged, tok_only = merge_tokusatsu(across, tokusatsu)
    merged_groups, otaku_merged, otaku_only, otaku_ambiguous = merge_otaku(merged_groups, otaku or [])

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
        "otaku_merged": otaku_merged,
        "otaku_only": [{"name": o["name"], "group": o.get("group")} for o in otaku_only],
        "otaku_ambiguous": otaku_ambiguous,
        "otaku_tagged": sum(1 for ps in groups_out.values() for p in ps if p.get("otaku")),
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
    if "otaku_merged" in report:
        om = report["otaku_merged"]
        lines.append(f"Otaku tagged: {report['otaku_tagged']}")
        lines.append(f"Otaku merged onto existing pins: {len(om)} "
                     f"(KML {sum(1 for m in om if m['kind'] == 'kml')}, "
                     f"Tokusatsu {sum(1 for m in om if m['kind'] == 'tokusatsu')}, "
                     f"other sheet rows {sum(1 for m in om if m['kind'] == 'otaku')})")
        for m in om:
            lines.append(f"  = {m['otaku']} -> {m['group']}: {m['into']} [{m['kind']}, {m['distance_m']} m]")
        lines.append(f"Otaku-only (new pins): {len(report['otaku_only'])}")
        lines.append(f"Otaku ambiguous near-matches NOT merged: {len(report['otaku_ambiguous'])}")
        for a in report["otaku_ambiguous"]:
            lines.append(f"  ? {a['otaku']} ~ {a['group']}: {a['near']} ({a['distance_m']} m)")
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


def add_manual_places(kml_groups: list[tuple[str, list[dict]]], manual: list[tuple[str, dict]]) -> None:
    """Append Manual.md pins to their group (as if they were KML placemarks)."""
    by_group = {g: ps for g, ps in kml_groups}
    for group, place in manual:
        if group not in by_group:
            raise ValueError(f"Manual.md: unknown group {group!r} for {place['name']}")
        by_group[group].append(place)


def build_from_dirs(
    kml_dir: Path,
    tokusatsu_path: Path | None,
    overrides_path: Path,
    otaku_path: Path | None = None,
    manual_path: Path | None = None,
) -> tuple[dict, dict]:
    overrides = load_overrides(overrides_path)
    kml_groups: list[tuple[str, list[dict]]] = []
    for kml in sorted(kml_dir.glob("*.kml")):
        kml_groups.append(parse_kml_file(kml, overrides))
    if manual_path and manual_path.exists():
        add_manual_places(kml_groups, parse_manual_md(manual_path, overrides))
    tokusatsu: list[dict] = []
    if tokusatsu_path and tokusatsu_path.exists():
        tokusatsu = parse_tokusatsu_md(tokusatsu_path, overrides)
    otaku: list[dict] = []
    if otaku_path and otaku_path.exists():
        otaku = parse_otaku_md(otaku_path, overrides)
    return assemble_payload(kml_groups, tokusatsu, otaku)
