import tempfile
import unittest
from pathlib import Path

from normalize_names import fold_for_search, normalize_place_name
from parse_places import (
    assemble_payload,
    is_generic_name,
    parse_kml_file,
    parse_tokusatsu_header,
    parse_tokusatsu_md,
)


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


KML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{doc}</name>
    {body}
  </Document>
</kml>
"""

FOLDER = """
    <Folder>
      <name>{folder}</name>
      {marks}
    </Folder>
"""

MARK = """
      <Placemark>
        <name>{name}</name>
        <Point><coordinates>{lng},{lat},0</coordinates></Point>
      </Placemark>
"""


class GenericNameTests(unittest.TestCase):
    def test_generics(self):
        for n in ("1", "2", "1.1", "Hospedagem 1", "Linha 34", "Mountain", "Park", "Photo point"):
            self.assertTrue(is_generic_name(n), n)

    def test_not_generic(self):
        for n in ("Tokyo Tower", "Sensoji", "Beppu · 1"):
            self.assertFalse(is_generic_name(n), n)


class NormalizeTests(unittest.TestCase):
    def test_salao_showa(self):
        out = normalize_place_name("Salão Showa 美遊ヘアスタジオ")
        self.assertIn("美遊ヘアスタジオ", out)
        self.assertNotIn("Salão", out)

    def test_hakoviva(self):
        out = normalize_place_name("ハコビバ（HAKOVIVA）")
        self.assertEqual(out, "HAKOVIVA (ハコビバ)")

    def test_fold_accents(self):
        self.assertIn("toquio", fold_for_search("Torre de Tóquio"))


class KmlFolderTests(unittest.TestCase):
    def test_folder_area_and_generic_prefix(self):
        body = FOLDER.format(
            folder="BEPPU",
            marks=MARK.format(name="1", lng="131.5", lat="33.3"),
        )
        with tempfile.TemporaryDirectory() as td:
            path = write(Path(td) / "kyushu.kml", KML_TEMPLATE.format(doc="[6] Kyushu", body=body))
            group, places = parse_kml_file(path)
            self.assertEqual(group, "Kyushu")
            self.assertEqual(len(places), 1)
            self.assertEqual(places[0]["area"], "Beppu")
            self.assertEqual(places[0]["name"], "Beppu · 1")
            self.assertTrue(places[0]["generic"])


class DedupAndTokusatsuTests(unittest.TestCase):
    def test_cross_file_keeps_earlier_group(self):
        tokyo_body = FOLDER.format(
            folder="Minato",
            marks=MARK.format(name="Torre de Tóquio", lng="139.74543", lat="35.65858"),
        )
        kanto_body = FOLDER.format(
            folder="Outras",
            marks=MARK.format(name="Torre de Tóquio", lng="139.74543", lat="35.65858"),
        )
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            tokyo = write(t / "TOKYO.kml", KML_TEMPLATE.format(doc="TOKYO", body=tokyo_body))
            kanto = write(t / "kanto.kml", KML_TEMPLATE.format(doc="[1] Kanto", body=kanto_body))
            g1, p1 = parse_kml_file(tokyo)
            g2, p2 = parse_kml_file(kanto)
            payload, report = assemble_payload([(g1, p1), (g2, p2)], [])
            self.assertEqual(len(payload["groups"]["Tokyo"]), 1)
            self.assertEqual(len(payload["groups"]["Kanto"]), 0)
            self.assertEqual(len(report["across_dropped"]), 1)
            self.assertIn("area", payload["groups"]["Tokyo"][0])
            self.assertEqual(payload["groups"]["Tokyo"][0]["area"], "Minato")

    def test_same_file_coord_dedup(self):
        marks = MARK.format(name="A", lng="139.0", lat="35.0") + MARK.format(
            name="A copy", lng="139.0", lat="35.0"
        )
        body = FOLDER.format(folder="X", marks=marks)
        with tempfile.TemporaryDirectory() as td:
            path = write(Path(td) / "t.kml", KML_TEMPLATE.format(doc="TOKYO", body=body))
            g, places = parse_kml_file(path)
            payload, report = assemble_payload([(g, places)], [])
            self.assertEqual(len(payload["groups"]["Tokyo"]), 1)
            self.assertEqual(len(report["within_dropped"]), 1)

    def test_tokusatsu_merge_and_only(self):
        tokyo_body = FOLDER.format(
            folder="Shinjuku",
            marks=MARK.format(name="Gracery", lng="139.702", lat="35.694"),
        )
        md = """# Tokusatsu
| Name | Coordinates | Google Maps |
| --- | --- | --- |
| **TOKYO — Godzilla** | | |
| Godzilla Head — Gracery Hotel Shinjuku (Kabukicho) | 139.702,35.694,0 | https://example.com/g |
| **OKINAWA** | | |
| Hamahiga Island coast | 127.972,26.345,0 | https://example.com/h |
"""
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            kml = write(t / "TOKYO.kml", KML_TEMPLATE.format(doc="TOKYO", body=tokyo_body))
            tok = write(t / "Tokusatsu.md", md)
            g, places = parse_kml_file(kml)
            ts = parse_tokusatsu_md(tok)
            payload, report = assemble_payload([(g, places)], ts)
            tokyo = payload["groups"]["Tokyo"]
            tagged = [p for p in tokyo if p.get("tokusatsu")]
            self.assertGreaterEqual(len(tagged), 1)
            self.assertTrue(any(p.get("tokusatsuArea") == "TOKYO — Godzilla" for p in tagged))
            # Okinawa unmatched → Kyushu group does not exist, falls back to first group
            self.assertEqual(len(report["tokusatsu_only"]), 1)
            self.assertTrue(any("tokusatsu" in p["q"] for p in tokyo if p.get("tokusatsu")))
            self.assertNotIn("Tokusatsu", payload["order"])

    def test_tokusatsu_header_parse(self):
        area, key = parse_tokusatsu_header("**TOKYO — Ultraman**")
        self.assertEqual(key, "TOKYO")
        self.assertIn("Ultraman", area)


class OverrideTests(unittest.TestCase):
    def test_override_applied_from_file(self):
        overrides = {
            "byName": {
                "Torre de Tóquio": {
                    "name": "Tokyo Tower (東京タワー)",
                    "aliases": ["Torre de Tóquio"],
                }
            },
            "byCoord": {},
        }
        body = FOLDER.format(
            folder="Minato",
            marks=MARK.format(name="Torre de Tóquio", lng="139.74543", lat="35.65858"),
        )
        with tempfile.TemporaryDirectory() as td:
            path = write(Path(td) / "TOKYO.kml", KML_TEMPLATE.format(doc="TOKYO", body=body))
            _g, places = parse_kml_file(path, overrides)
            self.assertEqual(places[0]["name"], "Tokyo Tower (東京タワー)")
            payload, _ = assemble_payload([(_g, places)], [])
            q = payload["groups"]["Tokyo"][0]["q"]
            self.assertIn("tokyo", q)
            self.assertIn("tower", q)
            self.assertIn("東京タワー", q)
            self.assertIn("toquio", q)


if __name__ == "__main__":
    unittest.main()
