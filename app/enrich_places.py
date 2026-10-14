#!/usr/bin/env python3
"""Optional GSI/Nominatim enrich for dirty place names.

Proposals only. Writes app/enrich_cache.json + app/enrich-proposals.md.
Default rebuild (build-places.py) stays offline and never imports this module's network path.

Usage:
  python3 app/enrich_places.py
  python3 app/enrich_places.py --limit 5
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from normalize_names import has_japanese
from parse_places import (
    REGIONAL_LATIN_RE,
    dedup_across_groups,
    dedup_within_group,
    haversine_m,
    load_overrides,
    merge_tokusatsu,
    parse_kml_file,
    parse_tokusatsu_md,
    _finish_place,
)

APP = Path(__file__).resolve().parent
ROOT = APP.parent
KML_DIR = ROOT / "KML"
LOC_DIR = ROOT / "Locations"
OVERRIDES = APP / "name_overrides.json"
CACHE_PATH = APP / "enrich_cache.json"
PROPOSALS_PATH = APP / "enrich-proposals.md"

USER_AGENT = "japantravelplan/cobalto.dev"
GSI_REVERSE = (
    "https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress"
    "?lon={lng}&lat={lat}"
)
GSI_MUNI_JS = "https://maps.gsi.go.jp/js/muni.js"
NOMINATIM_REVERSE = (
    "https://nominatim.openstreetmap.org/reverse"
    "?lat={lat}&lon={lng}&format=jsonv2&namedetails=1"
    "&accept-language=en,ja&zoom=18"
)

# Prefer ≥1 s between requests to the same host (Nominatim policy is strict).
MIN_HOST_INTERVAL = 1.05
OSM_NEAR_M = 50.0
HTTP_TIMEOUT = 30
MAX_RETRIES = 3

GROUP_PREFS: dict[str, set[str]] = {
    "Tokyo": {"東京都"},
    "Kanto": {
        "茨城県",
        "栃木県",
        "群馬県",
        "埼玉県",
        "千葉県",
        "神奈川県",
        "東京都",  # known Tokyo-in-Kanto leftovers — still flag via kanto_tokyo_ok
    },
    "Chubu": {
        "新潟県",
        "富山県",
        "石川県",
        "福井県",
        "山梨県",
        "長野県",
        "岐阜県",
        "静岡県",
        "愛知県",
    },
    "Kansai": {
        "三重県",
        "滋賀県",
        "京都府",
        "大阪府",
        "兵庫県",
        "奈良県",
        "和歌山県",
    },
    "Chugoku": {"鳥取県", "島根県", "岡山県", "広島県", "山口県"},
    "Shikoku": {"徳島県", "香川県", "愛媛県", "高知県"},
    "Kyushu": {
        "福岡県",
        "佐賀県",
        "長崎県",
        "熊本県",
        "大分県",
        "宮崎県",
        "鹿児島県",
        "沖縄県",
    },
    "Tohoku": {"青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県"},
    "Hokkaido": {"北海道"},
}

# Pref name prefixes without 都/府/県 for fuzzy matching of PLAN lists.
PREF_ALIASES = {
    "新潟": "新潟県",
    "富山": "富山県",
    "石川": "石川県",
    "福井": "福井県",
    "山梨": "山梨県",
    "長野": "長野県",
    "岐阜": "岐阜県",
    "静岡": "静岡県",
    "愛知": "愛知県",
    "三重": "三重県",
    "滋賀": "滋賀県",
    "京都": "京都府",
    "大阪": "大阪府",
    "兵庫": "兵庫県",
    "奈良": "奈良県",
    "和歌山": "和歌山県",
    "鳥取": "鳥取県",
    "島根": "島根県",
    "岡山": "岡山県",
    "広島": "広島県",
    "山口": "山口県",
    "徳島": "徳島県",
    "香川": "香川県",
    "愛媛": "愛媛県",
    "高知": "高知県",
    "福岡": "福岡県",
    "佐賀": "佐賀県",
    "長崎": "長崎県",
    "熊本": "熊本県",
    "大分": "大分県",
    "宮崎": "宮崎県",
    "鹿児島": "鹿児島県",
    "沖縄": "沖縄県",
    "青森": "青森県",
    "岩手": "岩手県",
    "宮城": "宮城県",
    "秋田": "秋田県",
    "山形": "山形県",
    "福島": "福島県",
    "茨城": "茨城県",
    "栃木": "栃木県",
    "群馬": "群馬県",
    "埼玉": "埼玉県",
    "千葉": "千葉県",
    "神奈川": "神奈川県",
}


def cache_key(source: str, lat: float, lng: float) -> str:
    return f"{source}|{lat:.5f}|{lng:.5f}"


def is_dirty(place: dict) -> bool:
    """Day-one dirty = generic OR Portuguese/regional Latin (needs-name-help).

    Full no-Japanese set is thousands of pins; PLAN says only include that
    when it is still hundreds. So we stick to the build-report help list.
    """
    if place.get("generic"):
        return True
    display = place.get("name") or ""
    latin = re.sub(
        r"[\u3040-\u30ff\u4e00-\u9fff\u3400-\u4dbf\uff66-\uff9f()]",
        " ",
        display,
    )
    return bool(REGIONAL_LATIN_RE.search(latin))


def lacks_japanese(place: dict) -> bool:
    return not has_japanese(place.get("name") or "") and not has_japanese(
        place.get("source") or ""
    )


def load_finished_places() -> list[dict]:
    overrides = load_overrides(OVERRIDES)
    kml_groups = [parse_kml_file(k, overrides) for k in sorted(KML_DIR.glob("*.kml"))]
    tokusatsu: list[dict] = []
    tok_path = LOC_DIR / "Tokusatsu.md"
    if tok_path.exists():
        tokusatsu = parse_tokusatsu_md(tok_path, overrides)

    cleaned: list[tuple[str, list[dict]]] = []
    for group, places in kml_groups:
        kept, _ = dedup_within_group(places)
        cleaned.append((group, kept))
    across, _ = dedup_across_groups(cleaned)
    merged, _, _ = merge_tokusatsu(across, tokusatsu)

    out: list[dict] = []
    for _group, places in merged:
        for p in places:
            out.append(_finish_place(p))
    return out


def load_cache(path: Path = CACHE_PATH) -> dict:
    if not path.exists():
        return {"version": 1, "muni": {}, "places": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("version", 1)
    data.setdefault("muni", {})
    data.setdefault("places", {})
    return data


def save_cache(cache: dict, path: Path = CACHE_PATH) -> None:
    path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class RateLimitedFetcher:
    """HTTP GET with per-host rate limit, retries, and User-Agent."""

    def __init__(self, min_interval: float = MIN_HOST_INTERVAL, opener=None):
        self.min_interval = min_interval
        self._last: dict[str, float] = {}
        self.opener = opener  # injectable for tests
        self.live_fetches = 0
        self.errors: list[str] = []

    def _host(self, url: str) -> str:
        # crude but enough for our fixed hosts
        m = re.match(r"https?://([^/]+)", url)
        return m.group(1) if m else url

    def _wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is None:
            return
        delay = self.min_interval - (time.monotonic() - last)
        if delay > 0:
            time.sleep(delay)

    def get_text(self, url: str, *, cache_hit: bool = False) -> str | None:
        if cache_hit:
            return None
        host = self._host(url)
        last_err = None
        for attempt in range(1, MAX_RETRIES + 1):
            self._wait(host)
            req = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            )
            try:
                if self.opener:
                    with self.opener(req, timeout=HTTP_TIMEOUT) as resp:
                        body = resp.read()
                        charset = resp.headers.get_content_charset() or "utf-8"
                else:
                    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                        body = resp.read()
                        charset = resp.headers.get_content_charset() or "utf-8"
                self._last[host] = time.monotonic()
                self.live_fetches += 1
                return body.decode(charset, errors="replace")
            except urllib.error.HTTPError as e:
                self._last[host] = time.monotonic()
                last_err = e
                if e.code == 429 or e.code >= 500:
                    time.sleep(min(8, 2 ** attempt))
                    continue
                self.errors.append(f"{url} -> HTTP {e.code}")
                return None
            except Exception as e:  # noqa: BLE001 — network is messy
                self._last[host] = time.monotonic()
                last_err = e
                time.sleep(min(8, 2 ** attempt))
        self.errors.append(f"{url} -> {last_err}")
        return None

    def get_json(self, url: str) -> dict | list | None:
        text = self.get_text(url)
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            self.errors.append(f"{url} -> bad JSON ({e})")
            return None


def parse_muni_js(text: str) -> dict[str, dict[str, str]]:
    """Parse GSI muni.js into {muniCd: {pref, city}}."""
    out: dict[str, dict[str, str]] = {}
    for m in re.finditer(
        r'GSI\.MUNI_ARRAY\["(\d+)"\]\s*=\s*\'([^\']*)\'',
        text,
    ):
        code = m.group(1)
        parts = m.group(2).split(",")
        if len(parts) < 4:
            continue
        pref = parts[1].strip()
        city = parts[3].strip().replace("\u3000", " ")
        out[code] = {"pref": pref, "city": city}
    return out


def ensure_muni(cache: dict, fetcher: RateLimitedFetcher) -> dict[str, dict[str, str]]:
    if cache.get("muni"):
        return cache["muni"]
    text = fetcher.get_text(GSI_MUNI_JS)
    if not text:
        return {}
    parsed = parse_muni_js(text)
    cache["muni"] = parsed
    return parsed


def decode_gsi(result: dict, muni: dict[str, dict[str, str]]) -> dict:
    """Turn GSI LonLatToAddress JSON into pref/city/chome."""
    results = result.get("results") if isinstance(result, dict) else None
    if not results:
        return {"pref": None, "city": None, "chome": None, "muniCd": None, "raw": result}
    muni_cd = str(results.get("muniCd") or "").lstrip("0") or str(results.get("muniCd") or "")
    # GSI returns muniCd without leading zero issues for 5-digit codes; keep as given.
    muni_cd = str(results.get("muniCd") or "")
    info = muni.get(muni_cd) or muni.get(muni_cd.lstrip("0")) or {}
    return {
        "pref": info.get("pref"),
        "city": info.get("city"),
        "chome": results.get("lv01Nm"),
        "muniCd": muni_cd,
        "raw": results,
    }


def group_pref_mismatch(group: str, pref: str | None) -> bool:
    if not pref:
        return False
    allowed = GROUP_PREFS.get(group)
    if not allowed:
        return False
    if pref in allowed:
        # Kanto + 東京都 is allowed but flagged as leftover (still a mismatch signal).
        if group == "Kanto" and pref == "東京都":
            return True
        return False
    return True


def suggest_area(place: dict, gsi: dict) -> str | None:
    area = place.get("area") or ""
    if "Outras" in area or "outras" in area.lower() or "atra" in area.lower():
        city = gsi.get("city")
        if city:
            # Prefer ward/city without leading city duplication: "横浜市 中区" → "中区" ok as-is
            return city
    return None


def osm_confidence(
    place: dict,
    osm_name: str | None,
    distance_m: float | None,
    namedetails: dict | None,
) -> str:
    if distance_m is None or osm_name is None or not str(osm_name).strip():
        return "low"
    if distance_m > OSM_NEAR_M:
        return "low"
    nd = namedetails or {}
    has_ja = bool(nd.get("name:ja") or has_japanese(osm_name))
    # Generics / numbered waypoints: never high confidence from OSM alone.
    if place.get("generic"):
        return "med" if has_ja and distance_m <= OSM_NEAR_M else "low"
    # Name relatedness: fold simple tokens
    def tokens(s: str) -> set[str]:
        s = s.casefold()
        s = re.sub(r"[^a-z0-9\u3040-\u30ff\u4e00-\u9fff]+", " ", s)
        return {t for t in s.split() if len(t) >= 3}

    cur = tokens(place.get("name") or "") | tokens(place.get("source") or "")
    osm_toks = tokens(osm_name) | tokens(nd.get("name:en") or "") | tokens(nd.get("name:ja") or "")
    related = bool(cur & osm_toks)
    # Portuguese museu/etc often won't token-overlap with Japanese OSM — treat
    # close POI with a real name as medium unless clearly related.
    if related and has_ja:
        return "high"
    if related:
        return "med"
    if has_ja and distance_m <= 25:
        return "med"
    if distance_m <= OSM_NEAR_M and osm_name.strip():
        return "low"
    return "low"


def format_suggested_name(namedetails: dict | None, osm_name: str | None) -> str | None:
    nd = namedetails or {}
    en = (nd.get("name:en") or "").strip()
    ja = (nd.get("name:ja") or "").strip()
    if en and ja:
        return f"{en} ({ja})"
    if ja and osm_name and not has_japanese(osm_name):
        return f"{osm_name} ({ja})"
    if ja and en:
        return f"{en} ({ja})"
    if ja:
        # romaji left for human / pykakasi later — still useful as 漢字 hint
        return f"— ({ja})"
    if en:
        return en
    if osm_name and str(osm_name).strip():
        return osm_name.strip()
    return None


def parse_nominatim(place: dict, data: dict) -> dict:
    if not data:
        return {
            "name": None,
            "distance_m": None,
            "confidence": "low",
            "suggested": None,
            "display_name": None,
            "namedetails": None,
            "lat": None,
            "lng": None,
        }
    nd = data.get("namedetails") or {}
    osm_name = (data.get("name") or nd.get("name") or "").strip() or None
    try:
        olat = float(data["lat"])
        olng = float(data["lon"])
        dist = haversine_m(place["lat"], place["lng"], olat, olng)
    except (KeyError, TypeError, ValueError):
        olat = olng = None
        dist = None
    conf = osm_confidence(place, osm_name, dist, nd if isinstance(nd, dict) else None)
    suggested = format_suggested_name(nd if isinstance(nd, dict) else None, osm_name)
    return {
        "name": osm_name,
        "distance_m": None if dist is None else round(dist, 1),
        "confidence": conf,
        "suggested": suggested,
        "display_name": data.get("display_name"),
        "namedetails": nd if isinstance(nd, dict) else None,
        "lat": olat,
        "lng": olng,
        "category": data.get("category"),
        "type": data.get("type"),
    }


def recommend_action(row: dict) -> str:
    if row.get("mismatch"):
        return "needs human"
    conf = (row.get("osm") or {}).get("confidence")
    suggested = (row.get("osm") or {}).get("suggested")
    if conf == "high" and suggested:
        return "accept"
    if conf == "med" and suggested and not row["place"].get("generic"):
        return "needs human"
    if row["place"].get("generic"):
        return "skip"
    if suggested and conf == "low":
        return "needs human"
    return "needs human"


def enrich_place(
    place: dict,
    cache: dict,
    muni: dict[str, dict[str, str]],
    fetcher: RateLimitedFetcher,
    *,
    do_nominatim: bool,
) -> tuple[dict, bool, bool]:
    """Returns (row, gsi_cache_hit, nominatim_cache_hit)."""
    key = cache_key(place["source"], place["lat"], place["lng"])
    entry = cache["places"].setdefault(key, {})
    gsi_hit = "gsi" in entry and entry["gsi"] is not None
    nom_hit = "nominatim" in entry

    if not gsi_hit:
        url = GSI_REVERSE.format(lat=place["lat"], lng=place["lng"])
        raw = fetcher.get_json(url)
        if raw is not None:
            entry["gsi"] = decode_gsi(raw, muni)
            entry["gsi_fetched_at"] = datetime.now(timezone.utc).isoformat()
        else:
            entry["gsi"] = {
                "pref": None,
                "city": None,
                "chome": None,
                "muniCd": None,
                "error": "fetch failed",
            }
            entry["gsi_fetched_at"] = datetime.now(timezone.utc).isoformat()
        gsi_hit = False
    else:
        # Refresh pref/city if muni table was empty at first fetch.
        g = entry["gsi"]
        if g and not g.get("pref") and g.get("muniCd") and muni:
            info = muni.get(str(g["muniCd"])) or {}
            if info:
                g["pref"] = info.get("pref")
                g["city"] = info.get("city")

    gsi = entry.get("gsi") or {}
    osm_info = None
    nom_was_hit = True
    if do_nominatim:
        if not nom_hit:
            url = NOMINATIM_REVERSE.format(lat=place["lat"], lng=place["lng"])
            raw = fetcher.get_json(url)
            if raw is not None and isinstance(raw, dict):
                entry["nominatim"] = raw
            else:
                entry["nominatim"] = {"error": "fetch failed"}
            entry["nominatim_fetched_at"] = datetime.now(timezone.utc).isoformat()
            nom_was_hit = False
        osm_raw = entry.get("nominatim") or {}
        if isinstance(osm_raw, dict) and "error" not in osm_raw:
            osm_info = parse_nominatim(place, osm_raw)
        else:
            osm_info = parse_nominatim(place, {})
            osm_info["error"] = (osm_raw or {}).get("error", "missing")
    else:
        nom_was_hit = True  # skipped, count as non-fetch

    mismatch = group_pref_mismatch(place["group"], gsi.get("pref"))
    suggested_area = suggest_area(place, gsi)
    row = {
        "place": place,
        "key": key,
        "gsi": gsi,
        "osm": osm_info,
        "mismatch": mismatch,
        "suggested_area": suggested_area,
    }
    row["action"] = recommend_action(row)
    return row, gsi_hit, nom_was_hit if do_nominatim else True


def sort_rows(rows: list[dict]) -> list[dict]:
    def score(r: dict) -> tuple:
        conf = (r.get("osm") or {}).get("confidence") or "low"
        conf_rank = {"high": 0, "med": 1, "low": 2}.get(conf, 3)
        return (
            0 if r.get("mismatch") else 1,
            conf_rank,
            r["place"]["group"],
            r["place"]["name"],
        )

    return sorted(rows, key=score)


def md_escape(text: str | None) -> str:
    if text is None:
        return "—"
    return str(text).replace("|", "\\|").replace("\n", " ")


def write_proposals(rows: list[dict], path: Path = PROPOSALS_PATH) -> None:
    lines = [
        "# Enrich proposals (Phase A2)",
        "",
        "Generated by `python3 app/enrich_places.py`. **Proposals only** — copy accepted",
        "names into `app/name_overrides.json` by hand. Coordinates are not moved.",
        "",
        f"Rows: {len(rows)}. Sort: group↔GSI mismatches and high-confidence OSM first.",
        "",
        "| display | source | group / area / lat,lng | suggested Romaji (漢字) |"
        " GSI locality | mismatch? | OSM name + m + conf | action |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        p = r["place"]
        gsi = r.get("gsi") or {}
        osm = r.get("osm") or {}
        locality = " / ".join(
            x for x in [gsi.get("pref"), gsi.get("city"), gsi.get("chome")] if x
        ) or "—"
        if r.get("suggested_area"):
            locality += f" (suggest area: {r['suggested_area']})"
        osm_cell = "—"
        if osm:
            parts = []
            if osm.get("name"):
                parts.append(str(osm["name"]))
            elif osm.get("display_name"):
                parts.append(str(osm["display_name"]).split(",")[0])
            if osm.get("distance_m") is not None:
                parts.append(f"{osm['distance_m']} m")
            if osm.get("confidence"):
                parts.append(osm["confidence"])
            if osm.get("error"):
                parts.append(f"err:{osm['error']}")
            osm_cell = " · ".join(parts) if parts else "—"
        coords = f"{p['lat']:.5f},{p['lng']:.5f}"
        where = f"{p['group']} / {p.get('area') or '—'} / {coords}"
        suggested = (osm or {}).get("suggested") or "—"
        lines.append(
            "| "
            + " | ".join(
                [
                    md_escape(p.get("name")),
                    md_escape(p.get("source")),
                    md_escape(where),
                    md_escape(suggested),
                    md_escape(locality),
                    "yes" if r.get("mismatch") else "no",
                    md_escape(osm_cell),
                    md_escape(r.get("action")),
                ]
            )
            + " |"
        )
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    actions = {}
    for r in rows:
        actions[r["action"]] = actions.get(r["action"], 0) + 1
    for k, v in sorted(actions.items()):
        lines.append(f"- `{k}`: {v}")
    mismatches = [r for r in rows if r.get("mismatch")]
    lines.append(f"- group vs GSI mismatches: {len(mismatches)}")
    high = [
        r
        for r in rows
        if (r.get("osm") or {}).get("confidence") == "high"
    ]
    lines.append(f"- high-confidence OSM: {len(high)}")
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(*, limit: int | None = None, fetcher: RateLimitedFetcher | None = None) -> dict:
    fetcher = fetcher or RateLimitedFetcher()
    places = load_finished_places()
    dirty = [p for p in places if is_dirty(p)]
    if limit is not None:
        dirty = dirty[:limit]

    cache = load_cache()
    muni = ensure_muni(cache, fetcher)
    if muni:
        save_cache(cache)  # persist muni early

    rows: list[dict] = []
    cache_hits = 0
    live = 0
    t0 = time.monotonic()
    total = len(dirty)

    print(f"Dirty subset: {total} (of {len(places)} places)", flush=True)

    for i, place in enumerate(dirty, 1):
        need_nom = lacks_japanese(place) or not has_japanese(place.get("name") or "")
        # Nominatim only when display still lacks Japanese (PLAN).
        need_nom = not has_japanese(place.get("name") or "")
        row, gsi_hit, nom_hit = enrich_place(
            place, cache, muni, fetcher, do_nominatim=need_nom
        )
        rows.append(row)
        # Count cache: both required fetches hit.
        hits_this = 0
        needed = 1 + (1 if need_nom else 0)
        if gsi_hit:
            hits_this += 1
        if need_nom and nom_hit:
            hits_this += 1
        elif not need_nom:
            pass
        if hits_this == needed:
            cache_hits += 1
        else:
            live += 1

        if i % 10 == 0 or i == total:
            elapsed = time.monotonic() - t0
            rate = elapsed / i if i else 1
            eta = rate * (total - i)
            print(
                f"  [{i}/{total}] cache-warm rows≈{cache_hits} "
                f"partial/live≈{live} http_gets={fetcher.live_fetches} "
                f"elapsed={elapsed:.0f}s ETA≈{eta:.0f}s",
                flush=True,
            )
            save_cache(cache)

    rows = sort_rows(rows)
    write_proposals(rows)
    save_cache(cache)

    high = sum(1 for r in rows if (r.get("osm") or {}).get("confidence") == "high")
    needs = sum(1 for r in rows if r.get("action") == "needs human")
    accept = sum(1 for r in rows if r.get("action") == "accept")
    skip = sum(1 for r in rows if r.get("action") == "skip")
    mismatches = [r for r in rows if r.get("mismatch")]

    summary = {
        "dirty": total,
        "cache_warm_rows": cache_hits,
        "live_or_partial_rows": live,
        "http_gets": fetcher.live_fetches,
        "high_confidence": high,
        "accept": accept,
        "needs_human": needs,
        "skip": skip,
        "mismatches": len(mismatches),
        "errors": list(fetcher.errors),
        "elapsed_s": round(time.monotonic() - t0, 1),
        "proposals": str(PROPOSALS_PATH),
        "cache": str(CACHE_PATH),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if mismatches:
        print("Notable mismatches (group vs GSI pref):", flush=True)
        for r in mismatches[:25]:
            p = r["place"]
            print(
                f"  - {p['group']} | {p.get('area') or '—'} | {p['name']} "
                f"→ GSI { (r.get('gsi') or {}).get('pref') } / {(r.get('gsi') or {}).get('city')}",
                flush=True,
            )
    if fetcher.errors:
        print(f"API errors ({len(fetcher.errors)}):", flush=True)
        for e in fetcher.errors[:20]:
            print(f"  - {e}", flush=True)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Optional GSI/Nominatim enrich (proposals only)")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N dirty rows")
    args = parser.parse_args(argv)
    run(limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
