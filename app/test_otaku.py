"""Tests for the Otaku sheet importer and the cross-source dedupe (offline)."""
import tempfile
import unittest
from pathlib import Path

import import_otaku_sheet as imp
from parse_places import assemble_payload, merge_otaku, otaku_names_match, parse_otaku_md


def _pin(name, lat, lng, group="Tokyo", **kw):
    return {"source": name, "name": name, "lat": lat, "lng": lng, "group": group, "area": None,
            "url": "", "overviewUrl": "", "aliases": [], "tokusatsu": False, "tokusatsuArea": None,
            "generic": False, **kw}


def _otaku(name, lat, lng, ja="", region="Tokyo"):
    return {"source": f"{name} {ja}".strip(), "name": f"{name} ({ja})" if ja else name, "ja": ja,
            "lat": lat, "lng": lng, "group": "Tokyo", "area": region, "url": "", "overviewUrl": "",
            "aliases": [name], "tokusatsu": False, "tokusatsuArea": None, "otaku": True,
            "otakuNotes": "Card: test", "otakuCategory": "Otaku", "generic": False, "origin": "otaku"}


class FakeFetcher:
    def __init__(self, answers):
        self.answers, self.calls, self.errors = answers, [], []

    def get_json(self, url):
        self.calls.append(url)
        for key, val in self.answers.items():
            if key in url:
                return val
        return []


class ImporterTests(unittest.TestCase):
    def sheet(self):
        return {
            "cards": [
                {"Place": "Bandai Museum (Omocha-no-machi, Mibu, Tochigi)", "Otaku anchors": "Toys",
                 "Culture / food around": "Utsunomiya gyoza", "Date window": "after Jan 4",
                 "Ticket / booking": "walk-up", "Region": "Tochigi", "Notes": "x", "Owner tip": "Otaku"},
                {"Place": "Meguro–Ebisu–Nakameguro food loop", "Region": "Tokyo", "Owner tip": "Travel"},
                {"Place": "Brand new card", "Region": "Tokyo", "Owner tip": "Otaku"},
            ],
            "anchors": [
                {"Anchor": "Kansai / Kyoto weekday stay", "Status": "MUST", "When": "Jan", "Notes": ""},
            ],
        }

    def test_addons_split_and_link_to_parent(self):
        places, skipped = imp.extract_places(self.sheet())
        names = [p["name"] for p in places]
        self.assertIn("Bandai Museum", names)
        oya = next(p for p in places if p["name"] == "Oya History Museum")
        self.assertIn("Add-on of card", oya["notes"][0])
        bandai = next(p for p in places if p["name"] == "Bandai Museum")
        self.assertIn("Utsunomiya gyoza", bandai["notes"][0])  # generic food stays as text
        self.assertNotIn("Utsunomiya gyoza", names)
        self.assertIn("Booking: walk-up", bandai["notes"][0])
        whys = {s["row"]: s["why"] for s in skipped}
        self.assertIn("not a single locatable place", whys["Meguro–Ebisu–Nakameguro food loop"])
        self.assertIn("new card", whys["Brand new card"])
        self.assertIn("Kansai / Kyoto weekday stay", whys)

    def test_geocode_caches_and_never_invents(self):
        cache = {}
        f = FakeFetcher({"%E5%A4%A7%E8%B0%B7": [{"lat": "36.6", "lon": "139.82", "display_name": "Tochigi"}]})
        self.assertEqual(imp.geocode("大谷資料館", cache, f), (36.6, 139.82))
        n = len(f.calls)
        self.assertEqual(imp.geocode("大谷資料館", cache, f), (36.6, 139.82))
        self.assertEqual(len(f.calls), n)  # cache hit
        self.assertIsNone(imp.geocode("nowhere at all", cache, f))
        self.assertIsNone(imp.geocode("", cache, f))

    def test_region_guard_rejects_homonyms(self):
        self.assertFalse(imp.region_ok("Tokyo", "瀧泉寺, Tsuchiura, Ibaraki Prefecture, Japan"))
        self.assertTrue(imp.region_ok("Tokyo", "Ryusenji, Meguro, Tokyo, Japan"))

    def test_md_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "Otaku.md"
            imp.write_md([{"name": "Nakano Broadway", "ja": "中野ブロードウェイ", "lat": 35.709, "lng": 139.665,
                           "region": "Tokyo", "category": "Otaku", "notes": ["Card: a | b", "Locked"],
                           "links": ["https://x.example/"]}], path)
            rows = parse_otaku_md(path)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertTrue(r["otaku"])
        self.assertEqual(r["group"], "Tokyo")
        self.assertEqual((r["lat"], r["lng"]), (35.709, 139.665))
        self.assertIn("\n", r["otakuNotes"])
        self.assertIn("https://x.example/", r["otakuNotes"])
        self.assertIn("%E4%B8%AD%E9%87%8E", r["url"])  # maps query uses the Japanese name


class OfficialColumnTests(unittest.TestCase):
    def test_official_columns_override_name_link_and_geocode_query(self):
        sheet = {"cards": [{
            "Place": "Hatsumode — Meguro Fudoson or Hikawa", "Region": "Tokyo", "Owner tip": "Travel",
            "official_name_en": "Kami-Meguro Hikawa Shrine", "official_name_ja": "上目黒氷川神社",
            "address": "〒153-0044 東京都目黒区大橋2-16-21 / 2-16-21 Ohashi, Meguro-ku",
            "maps_url": "https://www.google.com/maps/search/?api=1&query=x"}],
            "anchors": [{"Anchor": "Hatsumode temple", "Status": "Want", "When": "Jan", "Notes": ""}]}
        places, _ = imp.extract_places(sheet)
        hikawa = next(p for p in places if p["ja"] == "上目黒氷川神社")
        self.assertEqual(hikawa["name"], "Kami-Meguro Hikawa Shrine")
        self.assertEqual(hikawa["query"], "東京都目黒区大橋2-16-21")
        self.assertEqual(hikawa["query_kind"], "address")
        self.assertEqual(hikawa["maps_url"], "https://www.google.com/maps/search/?api=1&query=x")
        self.assertTrue(any("Locked anchor" in n for n in hikawa["notes"]))  # anchor still resolves
        fudoson = next(p for p in places if p["name"].startswith("Meguro Fudoson"))
        self.assertNotIn("official", fudoson)

    def test_address_query_cleanup(self):
        self.assertEqual(imp.address_query("〒101-0021 東京都千代田区外神田1-15-16 秋葉原ラジオ会館6F / Akihabara"),
                         "東京都千代田区外神田1-15-16")
        self.assertEqual(imp.address_query("埼玉県川越市幸町 周辺 / around Saiwai-cho"), "埼玉県川越市幸町")
        self.assertEqual(imp.address_query("〒107-0062 東京都港区南青山4-23-6 2F / 2F"), "東京都港区南青山4-23-6")

    def test_coords_from_maps_url(self):
        self.assertEqual(imp.coords_from_maps_url("https://www.google.com/maps/@35.6581,139.7017,17z"), (35.6581, 139.7017))
        self.assertEqual(imp.coords_from_maps_url(
            "https://www.google.com/maps/search/?api=1&query=Foo,35.1,139.2"), (35.1, 139.2))
        self.assertIsNone(imp.coords_from_maps_url(
            "https://www.google.com/maps/search/?api=1&query=%E6%98%8E%E6%B2%BB%E7%A5%9E%E5%AE%AE%201-1"))

    def test_address_geocode_uses_gsi(self):
        f = FakeFetcher({"msearch.gsi.go.jp": [{"geometry": {"coordinates": [139.688, 35.652]},
                                               "properties": {"title": "東京都目黒区大橋二丁目"}}]})
        self.assertEqual(imp.geocode("東京都目黒区大橋2-16-21", {}, f, "address"), (35.652, 139.688))
        self.assertTrue(all("gsi" in c for c in f.calls))


class ManualPlacesTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parent.parent

    def test_asatte_is_a_separate_untagged_pin(self):
        from parse_places import build_from_dirs
        payload, report = build_from_dirs(self.ROOT / "KML", self.ROOT / "Locations" / "Tokusatsu.md",
                                          self.ROOT / "app" / "name_overrides.json",
                                          self.ROOT / "Locations" / "Otaku.md", self.ROOT / "Locations" / "Manual.md")
        tokyo = payload["groups"]["Tokyo"]
        asatte = [p for p in tokyo if p["name"].startswith("asatte")]
        gelato = [p for p in tokyo if p["name"] == "Guru Guru Gelato Yanaka Ginza"]
        street = [p for p in tokyo if "谷中ぎんざ" in p["q"] or p["name"] == "Yanaka Ginza"]
        self.assertEqual(len(asatte), 1)
        self.assertEqual(len(gelato), 1)
        self.assertEqual(asatte[0]["area"], gelato[0]["area"])
        self.assertNotIn("otaku", asatte[0])
        self.assertNotIn("otaku", gelato[0])
        self.assertTrue(street and all(s["id"] not in (asatte[0]["id"], gelato[0]["id"]) for s in street))
        self.assertNotIn("asatte", " ".join(m["into"] for m in report["otaku_merged"]))
        self.assertIn("asatte%E3%81%AE", asatte[0]["url"])


class DedupeTests(unittest.TestCase):
    def test_reviewed_blocks(self):
        hib = _otaku("Tokyo Midtown Hibiya (Hibiya Magic Time Illumination)", 35.6741, 139.7596, "東京ミッドタウン日比谷")
        sq = _pin("Hibiya Godzilla Square (Yurakucho)", 35.6735, 139.7590, tokusatsu=True)
        out, merged, only, amb = merge_otaku([("Tokyo", [sq])], [hib])
        self.assertEqual(merged, [])
        self.assertTrue(otaku_names_match(_otaku("Tokyo Midtown", 0, 0), _pin("Tokyo Midtown Tower", 0, 0)))
        self.assertFalse(otaku_names_match(_otaku("Tokyo Midtown", 0, 0, "東京ミッドタウン"),
                                           _pin("Tokyo Midtown Hibiya (東京ミッドタウン日比谷)", 0, 0)))
        self.assertFalse(otaku_names_match(_otaku("Yanaka Ginza shopping street", 0, 0, "谷中ぎんざ"),
                                           _pin("Guru Guru Gelato Yanaka Ginza", 0, 0)))
        self.assertFalse(otaku_names_match(_otaku("Meiji Jingu (Meiji Shrine)", 0, 0), _pin("Meiji Jingu Gyoen", 0, 0)))

    def test_kawagoe_override_makes_one_pin(self):
        from parse_places import display_name_for, load_overrides
        ov = load_overrides(Path(__file__).resolve().parent / "name_overrides.json")
        name, aliases = display_name_for("Kurazukuri no Machinami / Warehouse District", None, ov, 35.9231413, 139.4828819)
        self.assertEqual(name, "Kawagoe Ichibangai (川越一番街)")
        kml = _pin(name, 35.9231413, 139.4828819, "Kanto", aliases=aliases)
        o = _otaku("Kawagoe Ichibangai (Kurazukuri warehouse street)", 35.9233, 139.4830, "川越一番街（蔵造りの町並み）", "Saitama")
        out, merged, only, amb = merge_otaku([("Kanto", [kml])], [o])
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(out[0][1]), 1)

    def test_merges_onto_kml_and_tokusatsu(self):
        kml = _pin("Kinuta Park", 35.6370, 139.6360)
        tok = _pin("Ajinomoto Stadium (Chofu)", 35.6650, 139.5270, tokusatsu=True)
        groups = [("Tokyo", [kml, tok])]
        o1 = _otaku("Kinuta Park", 35.6371, 139.6361, "砧公園")
        o2 = _otaku("Ajinomoto Stadium", 35.6643, 139.5275, "味の素スタジアム")
        out, merged, only, amb = merge_otaku(groups, [o1, o2])
        self.assertEqual({m["kind"] for m in merged}, {"kml", "tokusatsu"})
        self.assertEqual(only, [])
        self.assertTrue(kml["otaku"] and tok["otaku"] and tok["tokusatsu"])
        self.assertEqual(len(out[0][1]), 2)  # one entry per real place

    def test_shared_japanese_name_merges_even_if_latin_differs(self):
        kml = _pin("Sensouji (浅草寺)", 35.7148, 139.7967)
        out, merged, only, amb = merge_otaku([("Tokyo", [kml])], [_otaku("Senso-ji Temple", 35.7160, 139.7970, "浅草寺")])
        self.assertEqual(len(merged), 1)

    def test_far_or_unrelated_not_merged_but_reported(self):
        cafe = _pin("Coffee Zingaro", 35.70924, 139.66569)
        far = _pin("Nakano Broadway", 35.80, 139.66)
        out, merged, only, amb = merge_otaku([("Tokyo", [cafe, far])], [_otaku("Nakano Broadway", 35.70925, 139.66570)])
        self.assertEqual(merged, [])
        self.assertEqual(len(only), 1)
        self.assertEqual(amb[0]["near"], "Coffee Zingaro")
        self.assertEqual(len(out[0][1]), 3)

    def test_generic_and_stopword_only_overlap_do_not_match(self):
        o = _otaku("Meguro Fudoson", 35.0, 139.0)
        self.assertFalse(otaku_names_match(o, _pin("Meguro · Linha 38", 35.0, 139.0, generic=True)))
        self.assertFalse(otaku_names_match(_otaku("Kinuta Park", 0, 0), _pin("Shinjuku Central Park", 0, 0)))

    def test_payload_has_otaku_flags(self):
        payload, report = assemble_payload([("Tokyo", [_pin("Kinuta Park", 35.637, 139.636)])], [],
                                           [_otaku("Kinuta Park", 35.637, 139.636), _otaku("Nakano Broadway", 35.709, 139.665)])
        places = payload["groups"]["Tokyo"]
        self.assertEqual(len(places), 2)
        self.assertTrue(all(p["otaku"] for p in places))
        self.assertTrue(all("otaku" in p["q"] for p in places))
        self.assertEqual(report["otaku_tagged"], 2)


if __name__ == "__main__":
    unittest.main()
