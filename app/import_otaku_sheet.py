#!/usr/bin/env python3
"""Import trip sheet (Japan_Trip_Trump_Cards.xlsx) into Locations/Otaku.md.

    python3 app/import_otaku_sheet.py [path/to/Japan_Trip_Trump_Cards.xlsx]

Reads the 'Trump cards' and 'Locked anchors' tabs ('How to use' is ignored), splits
bundled cards into real, locatable places (see CARD_PLACES), geocodes names with
Nominatim search (+ GSI address search fallback) at >=1 req/s with a git-tracked cache
(app/otaku_geocode_cache.json), and writes Locations/Otaku.md. Coordinates are never
invented: rows that cannot be resolved are listed in app/otaku-import-report.md and
left out of the map. Generic mentions (food loops, "Utsunomiya gyoza") stay as text in
the parent card's notes.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

APP = Path(__file__).resolve().parent
ROOT = APP.parent
sys.path.insert(0, str(APP))

# Where the trip sheet lives. Change it here, or set OTAKU_SHEET / pass a path on the CLI.
DEFAULT_XLSX = Path(os.environ.get("OTAKU_SHEET", "/workspace/Japan_Trip_Trump_Cards.xlsx"))

# Sheet notes sometimes carry data-maintenance remarks (merge/keep-separate hints for
# whoever imports the sheet). Those are for the importer, not for travellers, so any
# note segment matching this pattern is dropped from the popup text.
MAINTENANCE_NOTE = re.compile(
    r"merge|keep separate|separate place|unverified|see report|duplicate|other lists?",
    re.I)


def clean_note(text: str) -> str:
    """Drop maintenance-only segments (split on ' / ' and sentence ends) from a note."""
    parts = re.split(r"\s+/\s+|(?<=[.!?])\s+", text or "")
    return " / ".join(x.strip() for x in parts if x.strip() and not MAINTENANCE_NOTE.search(x))
OUT_MD = ROOT / "Locations" / "Otaku.md"
CACHE = APP / "otaku_geocode_cache.json"
REPORT = APP / "otaku-import-report.md"
NOMINATIM = ("https://nominatim.openstreetmap.org/search?q={q}&format=jsonv2"
             "&limit=1&countrycodes=jp&accept-language=en")
GSI_SEARCH = "https://msearch.gsi.go.jp/address-search/AddressSearch?q={q}"

# Card (prefix of the 'Place' cell) -> real places inside it: (name, japanese, geocode query).
# A query starting with "addr:" is a Japanese address geocoded with GSI address search.
# An empty list = not a locatable spot (event-free loop / logistics); kept only in the report.
CARD_PLACES: dict[str, list[tuple[str, str, str]]] = {
    "Akihabara (Mandarake": [("Akihabara Electric Town", "秋葉原電気街", "秋葉原駅")],
    "Nakano Broadway": [("Nakano Broadway", "中野ブロードウェイ", "中野ブロードウェイ")],
    "Shinjuku — SE Cafe": [
        ("Square Enix Cafe & Shop Shinjuku", "スクウェア・エニックス カフェ", "Square Enix Cafe Shinjuku"),
        ("Godzilla Store Tokyo (Shinjuku Marui Annex)", "ゴジラ・ストア", "新宿マルイ アネックス"),
        ("Godzilla Head — Gracery Hotel Shinjuku", "ホテルグレイスリー新宿", "ホテルグレイスリー新宿")],
    "Setagaya — Kinuta Park": [
        ("Kinuta Park", "砧公園", "砧公園"),
        ("Soshigaya-Okura Ultraman Street", "祖師ヶ谷大蔵", "祖師ヶ谷大蔵駅"),
        ("Toho Studios", "東宝スタジオ", "東宝スタジオ")],
    "Odaiba / Ariake": [("Tokyo Big Sight", "東京ビッグサイト", "東京ビッグサイト")],
    "Shimokitazawa": [("Shimokitazawa", "下北沢", "下北沢駅")],
    "Meguro–Ebisu–Nakameguro": [],
    "Yanaka / Nezu": [("Yanaka Ginza", "谷中銀座", "Yanaka Ginza, Taito, Tokyo")],
    "Hatsumode — Meguro Fudoson": [
        ("Meguro Fudoson (Ryusenji)", "目黒不動尊", "Ryusenji, Meguro, Tokyo"),
        ("Meguro Hikawa Shrine", "", "")],  # "Hikawa" is ambiguous -> unresolved
    "Hatsumode — Meiji Jingu": [("Meiji Jingu", "明治神宮", "明治神宮")],
    "Omiya Railway Museum": [("Railway Museum (Omiya)", "鉄道博物館", "鉄道博物館 さいたま")],
    "Tsukuba JAXA": [("JAXA Tsukuba Space Center — Space Dome", "筑波宇宙センター", "筑波宇宙センター")],
    "Yokohama — Business Park": [
        ("Yokohama Business Park", "横浜ビジネスパーク", "横浜ビジネスパーク"),
        ("Yokohama Chinatown", "横浜中華街", "横浜中華街")],
    "Kamakura + Enoshima": [
        ("Cape Inamuragasaki", "稲村ヶ崎", "Inamuragasaki, Kamakura"),
        ("Enoshima", "江の島", "江の島")],
    "Sagamihara — JAXA ISAS": [
        ("JAXA Sagamihara Campus (ISAS)", "宇宙科学研究所", "Institute of Space and Astronautical Science, Sagamihara"),
        ("Sagamihara City Museum", "相模原市立博物館", "相模原市立博物館")],
    "Sukagawa Ultraman Street": [
        ("Sukagawa Ultraman Street", "須賀川 ウルトラマン通り", "addr:福島県須賀川市中町"),
        ("Sukagawa Tokusatsu Archive Center / tette", "須賀川市民交流センター tette", "須賀川市民交流センター"),
        # hook says "Archive if open": separate building from tette
        ("Sukagawa Tokusatsu Archive Center", "須賀川特撮アーカイブセンター", "addr:福島県須賀川市柱田字中地前22")],
    "Uji — Nintendo Museum": [("Nintendo Museum", "ニンテンドーミュージアム", "ニンテンドーミュージアム")],
    "Kyoto — Toei Uzumasa": [("Toei Kyoto Studio Park (Uzumasa Eigamura)", "東映太秦映画村", "東映太秦映画村")],
    "Osaka — Den Den Town": [("Den Den Town (Nipponbashi)", "でんでんタウン", "Ota Road, Naniwa, Osaka")],
    "Nagoya — SCMaglev": [
        ("SCMaglev and Railway Park", "リニア・鉄道館", "リニア・鉄道館"),
        ("Osu Shopping District", "Osu Kannon, Nagoya", "Osu Kannon, Nagoya")],
    "Kobe (harbor": [("Kobe Harborland", "神戸ハーバーランド", "神戸ハーバーランド")],
    "Okayama — Korakuen": [("Korakuen", "後楽園", "岡山後楽園")],
    "Wonder Festival 2027": [("Makuhari Messe", "幕張メッセ", "幕張メッセ")],
    "Kagurazaka": [("Kagurazaka", "神楽坂", "神楽坂駅")],
    "Jiyugaoka": [("Jiyugaoka", "自由が丘", "自由が丘駅")],
    "Kawagoe Koedo": [("Kurazukuri no Machinami (Kawagoe)", "川越 蔵造りの町並み", "川越 一番街")],
    "Kyoto — Nishiki Market": [("Nishiki Market", "錦市場", "錦市場")],
    "Kasugai — Ultra Heroes": [("Kasugai Ultra Heroes THE LIVE venue", "", "")],  # venue not named
    "Sagamiko Illumillion": [("Sagamiko Pleasure Forest (Illumillion)", "さがみ湖リゾート プレジャーフォレスト", "Sagamiko Resort Pleasure Forest")],
    "Ikebukuro — Animate": [
        ("Animate Ikebukuro Main Store", "アニメイト池袋本店", "アニメイト池袋本店"),
        ("Otome Road", "乙女ロード", "乙女ロード")],
    "Shibuya — Nintendo TOKYO": [("Nintendo TOKYO (Shibuya PARCO)", "ニンテンドートウキョウ", "渋谷PARCO")],
    "Oizumi-Gakuen — Toei": [("Toei Tokyo Studio", "東映東京撮影所", "東映東京撮影所")],
    "Gundam Base Tokyo": [("The Gundam Base Tokyo (DiverCity Tokyo Plaza)", "ダイバーシティ東京", "ダイバーシティ東京 プラザ")],
    "Yellow Submarine": [("Yellow Submarine Akihabara", "イエローサブマリン秋葉原", "")],
    "Kodomonokuni (Yokohama)": [("Kodomo no Kuni", "こどもの国", "こどもの国 横浜")],
    "Chofu — Ajinomoto Stadium": [("Ajinomoto Stadium", "味の素スタジアム", "味の素スタジアム")],
    "Bandai Hobby Center": [("Bandai Hobby Center", "バンダイホビーセンター", "バンダイホビーセンター")],
    "Tokorozawa Aviation": [("Tokorozawa Aviation Memorial Park", "所沢航空記念公園", "所沢航空記念公園")],
    "Tokyo Midtown": [("Tokyo Midtown", "東京ミッドタウン", "東京ミッドタウン")],
    "Marunouchi Illumination": [("Marunouchi Naka-dori", "丸の内仲通り", "丸の内仲通り")],
    "Roppongi Hills Keyakizaka": [("Keyakizaka Street (Roppongi Hills)", "けやき坂", "けやき坂 六本木")],
    "Hibiya Magic Time": [("Tokyo Midtown Hibiya", "東京ミッドタウン日比谷", "東京ミッドタウン日比谷")],
    "Yebisu Garden Place": [("Yebisu Garden Place", "恵比寿ガーデンプレイス", "恵比寿ガーデンプレイス")],
    "Yomiuriland Jewellumination": [("Yomiuriland", "よみうりランド", "よみうりランド")],
    "Ashikaga Flower Park": [("Ashikaga Flower Park", "あしかがフラワーパーク", "あしかがフラワーパーク")],
    "Nabana no Sato": [("Nabana no Sato", "なばなの里", "なばなの里")],
    "Shibamata — Taishakuten": [("Shibamata Taishakuten", "柴又帝釈天", "柴又帝釈天")],
    "Hamarikyu Gardens": [("Hamarikyu Gardens", "浜離宮恩賜庭園", "浜離宮恩賜庭園")],
    "Ippodo Tea Aoyama": [("Ippodo Tea Aoyama", "一保堂茶舗 青山", "")],
    "Nezu Museum": [("Nezu Museum", "根津美術館", "根津美術館")],
    "Edo-Tokyo Open-Air": [("Edo-Tokyo Open Air Architectural Museum", "江戸東京たてもの園", "江戸東京たてもの園")],
    "Jimbocho": [("Jimbocho Book Town", "神保町", "神保町駅")],
    "Kichijoji — Inokashira": [
        ("Inokashira Park", "井の頭恩賜公園", "井の頭恩賜公園"),
        ("Harmonica Yokocho", "ハモニカ横丁", "ハモニカ横丁")],
    "Sawara Little Edo": [("Sawara Old Town", "佐原の町並み", "佐原駅")],
    "FF 40th Exhibition": [("Tokyo National Museum — Hyokeikan", "東京国立博物館 表慶館", "表慶館")],
    "FMA × Yomi no Tsugai": [("Hankyu Umeda Main Store", "阪急うめだ本店", "阪急うめだ本店")],
    "Ultra Heroes EXPO 2027": [("Tokyo Dome City Prism Hall", "プリズムホール", "東京ドームシティ プリズムホール")],
    "Super Hero Festival 2027": [("Yokohama Arena", "横浜アリーナ", "横浜アリーナ")],
    "Kamen Rider Super Live 2027": [("Omiya Sonic City", "大宮ソニックシティ", "大宮ソニックシティ")],
    "Sumaura Sanjo Yuen": [("Sumaura Sanjo Yuen", "須磨浦山上遊園", "須磨浦山上遊園")],
    "Bandai Museum": [
        ("Bandai Museum", "バンダイミュージアム", "バンダイミュージアム 壬生"),
        ("Oya History Museum", "大谷資料館", "大谷資料館"),
        ("Mibu Toy Museum", "壬生町おもちゃ博物館", "壬生町おもちゃ博物館")],
}

# Multi-place cards: which sub-place the row's official_name_*/address/maps_url columns describe.
OFFICIAL_TARGET = {
    "Hatsumode — Meguro Fudoson": "Meguro Hikawa Shrine",
    "Sukagawa Ultraman Street": "Sukagawa Tokusatsu Archive Center / tette",
}

# Locked anchors (prefix) -> place names above that they refer to (status/when merged into notes).
ANCHOR_PLACES: dict[str, list[str]] = {
    "C109 Comic Market": ["Tokyo Big Sight"],
    "Nintendo Museum Uji": ["Nintendo Museum"],
    "Sukagawa Ultraman": ["Sukagawa Ultraman Street", "Sukagawa Tokusatsu Archive Center / tette"],
    "Omiya Railway Museum": ["Railway Museum (Omiya)"],
    "JAXA Tsukuba": ["JAXA Tsukuba Space Center — Space Dome"],
    "SE Cafe Shinjuku": ["Square Enix Cafe & Shop Shinjuku"],
    "Jan 1 Animate": ["Akihabara Electric Town", "Animate Ikebukuro Main Store"],
    "Hatsumode temple": ["Meguro Fudoson (Ryusenji)", "Meguro Hikawa Shrine", "Meiji Jingu"],
    "Ultra Heroes THE LIVE": ["Kasugai Ultra Heroes THE LIVE venue"],
    "Sagamiko Illumillion": ["Sagamiko Pleasure Forest (Illumillion)"],
    "Yomiuriland": ["Yomiuriland"],
    "Nabana no Sato": ["Nabana no Sato"],
    "Roppongi Hills": ["Keyakizaka Street (Roppongi Hills)"],
    "Marunouchi Illumination": ["Marunouchi Naka-dori"],
    "FF 40th": ["Tokyo National Museum — Hyokeikan"],
    "FMA × Yomi": ["Hankyu Umeda Main Store"],
    "Ultra Heroes EXPO": ["Tokyo Dome City Prism Hall"],
    "Toei Uzumasa": ["Toei Kyoto Studio Park (Uzumasa Eigamura)"],
    "Kansai / Kyoto weekday": [],
    "JAXA Sagamihara": ["JAXA Sagamihara Campus (ISAS)", "Sagamihara City Museum"],
}

URL_RE = re.compile(r"https?://[^\s·)]+")


def _cell(v) -> str:
    return re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""


def _lookup(table: dict, cell: str):
    for prefix, val in table.items():
        if cell.startswith(prefix):
            return val
    return None


def read_sheet(xlsx: Path) -> dict:
    """Return {'cards': [row dicts], 'anchors': [row dicts]} from the workbook."""
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    out = {}
    for tab, key in (("Trump cards", "cards"), ("Locked anchors", "anchors")):
        rows = list(wb[tab].iter_rows(values_only=True))
        head = [_cell(h) for h in rows[0]]
        out[key] = [dict(zip(head, map(_cell, r))) for r in rows[1:] if r and _cell(r[0])]
    return out


def card_notes(row: dict) -> str:
    bits = []
    for label, col in (("Hook", "Otaku anchors"), ("Food/culture", "Culture / food around"),
                       ("When", "Date window"), ("Booking", "Ticket / booking"),
                       ("Pace", "Pace"), ("From Meguro", "From Meguro (approx)"),
                       ("How", "Main lines / how"), ("Notes", "Notes")):
        v = row.get(col, "")
        if col == "Notes":
            v = clean_note(v)
        if v and v.lower() not in ("none", "none required", "-", "—"):
            bits.append(f"{label}: {v}")
    return " · ".join(bits)


def extract_places(sheet: dict) -> tuple[list[dict], list[dict]]:
    """Return (places, skipped). Places are keyed by name and carry card + anchor notes."""
    places: dict[str, dict] = {}
    skipped: list[dict] = []
    aliases: dict[str, str] = {}  # CARD_PLACES name -> official name (anchors keep resolving)
    for row in sheet["cards"]:
        card = row.get("Place", "")
        spec = _lookup(CARD_PLACES, card)
        if spec is None:
            skipped.append({"tab": "Trump cards", "row": card, "why": "no CARD_PLACES mapping (new card?)"})
            spec = [(re.split(r"\s+[—(]", card)[0].strip(), "", re.split(r"\s+[—(]", card)[0].strip())]
        if not spec:
            skipped.append({"tab": "Trump cards", "row": card, "why": "not a single locatable place"})
            continue
        notes = card_notes(row)
        links = URL_RE.findall(" ".join(row.values()))
        official = official_fields(row)
        target = None
        if official:
            target = OFFICIAL_TARGET.get(next((k for k in OFFICIAL_TARGET if card.startswith(k)), ""), spec[0][0])
            if len(spec) > 1 and not any(card.startswith(k) for k in OFFICIAL_TARGET):
                target = spec[0][0]
        for i, (name, ja, query) in enumerate(spec):
            kind = "address" if query.startswith("addr:") else "name"
            query = query[5:] if kind == "address" else query
            p = places.setdefault(name, {"name": name, "ja": ja, "query": query, "query_kind": kind,
                                         "region": row.get("Region", ""),
                                         "category": row.get("Owner tip", ""), "cards": [], "notes": [], "links": []})
            if official and name == target:
                p.update(official)
                aliases[name] = p["name"]
            p["cards"].append(card)
            prefix = f"Card: {card}" if i == 0 else f"Add-on of card “{card}”"
            p["notes"].append(f"{prefix} · {notes}" if notes else prefix)
            p["links"].extend(l for l in links if l not in p["links"])
    for row in sheet["anchors"]:
        anchor = row.get("Anchor", "")
        names = _lookup(ANCHOR_PLACES, anchor)
        if names is None:
            skipped.append({"tab": "Locked anchors", "row": anchor, "why": "no ANCHOR_PLACES mapping (new anchor?)"})
            continue
        if not names:
            skipped.append({"tab": "Locked anchors", "row": anchor, "why": "logistics block, not a place"})
            continue
        txt = f"Locked anchor “{anchor}”: {row.get('Status','')} · When: {row.get('When','')} · {clean_note(row.get('Notes',''))}"
        links = URL_RE.findall(" ".join(row.values()))
        for n in names:
            p = places.get(n) or places.get(aliases.get(n, ""))
            if p is None:
                skipped.append({"tab": "Locked anchors", "row": anchor, "why": f"maps to unknown place {n}"})
                continue
            p["anchor"] = True
            p["notes"].append(txt)
            p["links"].extend(l for l in links if l not in p["links"])
    out = list(places.values())
    for p in out:
        p.setdefault("key", p["name"])
    return out, skipped


def coords_from_maps_url(url: str) -> tuple[float, float] | None:
    """Lat/lng embedded in a Google Maps URL (@lat,lng / query=...,lat,lng / q=lat,lng), else None."""
    u = urllib.parse.unquote(url or "")
    m = (re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", u)
         or re.search(r"[?&](?:query|q|ll|destination)=(?:[^&]*?,)?(-?\d{1,2}\.\d+),\s*(-?\d{2,3}\.\d+)(?:&|$)", u))
    if not m:
        return None
    lat, lng = float(m.group(1)), float(m.group(2))
    return (lat, lng) if 20 <= lat <= 46 and 122 <= lng <= 154 else None


def address_query(address: str) -> str:
    """Japanese part of 'JP / EN' address, minus postcode, building/floor and '周辺 (...)' notes."""
    a = address.split(" / ")[0]
    a = re.sub(r"〒\s*\d{3}-?\d{4}\s*", "", a)
    a = re.sub(r"\s*周辺.*$", "", a)
    a = re.sub(r"\s+\S*(?:\d+F|\d+階)$", "", a)  # trailing building + floor
    a = re.sub(r"\s*\d+F$", "", a)
    return a.strip()


def official_fields(row: dict) -> dict:
    """Fields from official_name_en / official_name_ja / address / maps_url when present."""
    en, ja = row.get("official_name_en", ""), row.get("official_name_ja", "")
    addr, url = row.get("address", ""), row.get("maps_url", "")
    if not (en or ja or addr or url):
        return {}
    out: dict = {"official": True}
    if en:
        out["name"] = en
    if ja:
        out["ja"] = ja
    if addr:
        out["address"] = addr
        out["query"] = address_query(addr)
        out["query_kind"] = "address"
    if url:
        out["maps_url"] = url
        ll = coords_from_maps_url(url)
        if ll:
            out["url_coords"] = ll
    return out


REGION_PREFS = {
    "Tokyo": ["Tokyo"], "Saitama": ["Saitama"], "Ibaraki": ["Ibaraki"], "Kanagawa": ["Kanagawa"],
    "Fukushima": ["Fukushima"], "Kansai": ["Kyoto", "Osaka", "Hyogo", "Nara"], "Chubu": ["Aichi", "Gifu", "Shizuoka"],
    "Chugoku": ["Okayama", "Hiroshima"], "Chiba": ["Chiba"], "Aichi": ["Aichi"], "Shizuoka": ["Shizuoka"],
    "Tochigi": ["Tochigi"], "Tokyo/Kanagawa": ["Tokyo", "Kanagawa"], "Mie/Aichi": ["Mie", "Aichi"],
}
PREF_JA = {"Tokyo": "東京", "Saitama": "埼玉", "Ibaraki": "茨城", "Kanagawa": "神奈川", "Fukushima": "福島",
           "Kyoto": "京都", "Osaka": "大阪", "Hyogo": "兵庫", "Nara": "奈良", "Aichi": "愛知", "Gifu": "岐阜",
           "Shizuoka": "静岡", "Okayama": "岡山", "Hiroshima": "広島", "Chiba": "千葉", "Tochigi": "栃木", "Mie": "三重"}


def region_ok(region: str, label: str) -> bool:
    """Reject geocodes that land in the wrong prefecture (homonyms like 瀧泉寺 in Ibaraki)."""
    prefs = REGION_PREFS.get(region)
    if not prefs or not label:
        return True
    return any(p in label or PREF_JA[p] in label for p in prefs)


def _gsi(query: str, fetcher):
    data = fetcher.get_json(GSI_SEARCH.format(q=urllib.parse.quote(query)))
    if data:
        lng, lat = data[0]["geometry"]["coordinates"][:2]
        return {"lat": round(lat, 6), "lng": round(lng, 6), "via": "gsi",
                "label": data[0].get("properties", {}).get("title", "")}
    return None


def geocode(query: str, cache: dict, fetcher, kind: str = "name") -> tuple[float, float] | None:
    if not query:
        return None
    if query in cache:
        hit = cache[query]
        return (hit["lat"], hit["lng"]) if hit else None
    if kind == "address":  # GSI is the authoritative Japanese address geocoder
        res = _gsi(query, fetcher)
        if res is None and fetcher.errors:
            return None
        cache[query] = res
        return (res["lat"], res["lng"]) if res else None
    res = None
    data = fetcher.get_json(NOMINATIM.format(q=urllib.parse.quote(query)))
    if data:
        res = {"lat": round(float(data[0]["lat"]), 6), "lng": round(float(data[0]["lon"]), 6),
               "via": "nominatim", "label": data[0].get("display_name", "")[:120]}
    else:
        data = fetcher.get_json(GSI_SEARCH.format(q=urllib.parse.quote(query)))
        if data:
            lng, lat = data[0]["geometry"]["coordinates"][:2]
            res = {"lat": round(lat, 6), "lng": round(lng, 6), "via": "gsi",
                   "label": data[0].get("properties", {}).get("title", "")}
    if data is None and fetcher.errors:
        return None  # network failure: don't cache a negative
    cache[query] = res
    return (res["lat"], res["lng"]) if res else None


def _md(s: str) -> str:
    return s.replace("|", "/").replace("\n", " ")


def write_md(places: list[dict], path: Path) -> None:
    from normalize_names import google_maps_url
    lines = ["# Otaku sheet places",
             "",
             "Generated by `python3 app/import_otaku_sheet.py` from "
             "Japan_Trip_Trump_Cards.xlsx — do not edit by hand; edit the sheet (or CARD_PLACES) and re-run.",
             "",
             "| Name | Japanese | Coordinates | Google Maps | Region | Category | Notes |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for p in places:
        q = re.sub(r"[（(].*?[）)]", "", p["ja"]).strip() or p["name"]
        url = p.get("maps_url") or google_maps_url(q, p["lat"], p["lng"])
        notes = " ¶ ".join(p["notes"])
        if p.get("address"):
            notes = f"Address: {p['address']} ¶ " + notes
        if p["links"]:
            notes += " ¶ Links: " + " ".join(p["links"])
        lines.append(f"| {_md(p['name'])} | {_md(p['ja'])} | {p['lng']},{p['lat']},0 | {url} | "
                     f"{_md(p['region'])} | {_md(p['category'])} | {_md(notes)} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(xlsx: Path, fetcher=None) -> dict:
    from enrich_places import RateLimitedFetcher
    fetcher = fetcher or RateLimitedFetcher()
    sheet = read_sheet(xlsx)
    places, skipped = extract_places(sheet)
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    resolved, unresolved = [], []
    for p in places:
        if p.get("url_coords"):
            p["lat"], p["lng"] = p["url_coords"]
            p["via"] = "maps_url coordinates"
            resolved.append(p)
            continue
        ll = geocode(p["query"], cache, fetcher, p.get("query_kind", "name"))
        if ll and not region_ok(p["region"], cache[p["query"]]["label"]):
            p["why"] = f"geocode outside {p['region']}: {cache[p['query']]['label'][:60]}"
            ll = None
        if ll:
            p["lat"], p["lng"] = ll
            resolved.append(p)
        else:
            unresolved.append(p)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    write_md(resolved, OUT_MD)
    rep = ["# Otaku sheet import report", "",
           f"- Sheet rows: {len(sheet['cards'])} trump cards + {len(sheet['anchors'])} locked anchors",
           f"- Places extracted: {len(places)} · geocoded: {len(resolved)} · unresolved: {len(unresolved)}",
           "", "## Unresolved (no coordinates; not on the map)"]
    rep += [f"- {p['name']} ({p['ja'] or '—'}) — from {p['cards'][0]}" + (f" ({p['why']})" if p.get('why') else "") for p in unresolved] or ["- none"]
    rep += ["", "## Rows skipped / needing a mapping"]
    rep += [f"- [{s['tab']}] {s['row']} — {s['why']}" for s in skipped] or ["- none"]
    rep += ["", "## Geocode sources"]
    rep += [f"- {p['name']}: " + (p["via"] if p.get("via") else
            f"{cache[p['query']]['via']} ({p.get('query_kind', 'name')}: {p['query']}) — {cache[p['query']]['label']}")
            for p in resolved]
    REPORT.write_text("\n".join(rep) + "\n", encoding="utf-8")
    return {"cards": len(sheet["cards"]), "anchors": len(sheet["anchors"]), "places": len(places),
            "resolved": len(resolved), "unresolved": [p["name"] for p in unresolved], "skipped": skipped}


if __name__ == "__main__":
    xlsx = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_XLSX
    r = run(xlsx)
    print(f"Otaku import: {r['cards']} cards + {r['anchors']} anchors -> {r['places']} places, "
          f"{r['resolved']} geocoded, {len(r['unresolved'])} unresolved")
    for n in r["unresolved"]:
        print(f"  unresolved: {n}")
    for s in r["skipped"]:
        print(f"  skipped [{s['tab']}]: {s['row'][:60]} — {s['why']}")
