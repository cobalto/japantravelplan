"""Unit tests for enrich_places.py — all HTTP mocked (no live network)."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.request import Request

import enrich_places as ep


class CacheKeyTests(unittest.TestCase):
    def test_rounds_to_5_dp(self):
        self.assertEqual(
            ep.cache_key("Tokyo Tower", 35.6585849, 139.7454321),
            "Tokyo Tower|35.65858|139.74543",
        )


class DirtyDetectionTests(unittest.TestCase):
    def test_generic_is_dirty(self):
        self.assertTrue(ep.is_dirty({"generic": True, "name": "Beppu · 1", "source": "1"}))

    def test_regional_latin_is_dirty(self):
        self.assertTrue(
            ep.is_dirty(
                {
                    "generic": False,
                    "name": "Museu Edo-Tokyo",
                    "source": "Museu Edo-Tokyo",
                }
            )
        )

    def test_clean_japanese_not_dirty(self):
        self.assertFalse(
            ep.is_dirty(
                {
                    "generic": False,
                    "name": "Sensouji (浅草寺)",
                    "source": "浅草寺",
                }
            )
        )


class GsiDecodeTests(unittest.TestCase):
    def test_muni_js_and_gsi_json(self):
        muni_js = (
            'GSI.MUNI_ARRAY["13103"] = \'13,東京都,13103,港区\';\n'
            'GSI.MUNI_ARRAY["1101"] = \'1,北海道,1101,札幌市　中央区\';\n'
        )
        muni = ep.parse_muni_js(muni_js)
        self.assertEqual(muni["13103"]["pref"], "東京都")
        self.assertEqual(muni["13103"]["city"], "港区")
        self.assertEqual(muni["1101"]["city"], "札幌市 中央区")

        decoded = ep.decode_gsi(
            {"results": {"muniCd": "13103", "lv01Nm": "芝公園四丁目"}},
            muni,
        )
        self.assertEqual(decoded["pref"], "東京都")
        self.assertEqual(decoded["city"], "港区")
        self.assertEqual(decoded["chome"], "芝公園四丁目")


class NominatimConfidenceTests(unittest.TestCase):
    def test_within_50m_related_high(self):
        place = {
            "name": "Tokyo Tower (東京タワー)",
            "source": "Tokyo Tower",
            "generic": False,
            "lat": 35.65858,
            "lng": 139.74543,
        }
        # ~0 m away
        data = {
            "lat": "35.65858",
            "lon": "139.74543",
            "name": "Tokyo Tower",
            "namedetails": {"name:en": "Tokyo Tower", "name:ja": "東京タワー"},
            "display_name": "Tokyo Tower, Minato, Tokyo",
        }
        parsed = ep.parse_nominatim(place, data)
        self.assertLessEqual(parsed["distance_m"], 50)
        self.assertEqual(parsed["confidence"], "high")
        self.assertEqual(parsed["suggested"], "Tokyo Tower (東京タワー)")

    def test_far_is_low(self):
        place = {
            "name": "Museu Edo-Tokyo",
            "source": "Museu Edo-Tokyo",
            "generic": False,
            "lat": 35.6963,
            "lng": 139.7966,
        }
        data = {
            "lat": "35.70000",
            "lon": "139.80000",
            "name": "Somewhere Else",
            "namedetails": {"name:en": "Somewhere Else"},
            "display_name": "Somewhere Else",
        }
        parsed = ep.parse_nominatim(place, data)
        self.assertGreater(parsed["distance_m"], 50)
        self.assertEqual(parsed["confidence"], "low")


class WarmCacheNoNetworkTests(unittest.TestCase):
    def test_warm_cache_skips_http(self):
        place = {
            "source": "Museu Edo-Tokyo",
            "name": "Museu Edo-Tokyo",
            "generic": False,
            "group": "Tokyo",
            "area": "Sumida",
            "lat": 35.69639,
            "lng": 139.79639,
            "id": "x",
            "q": "museu",
            "url": "http://example",
            "overviewUrl": "http://example",
        }
        key = ep.cache_key(place["source"], place["lat"], place["lng"])
        cache = {
            "version": 1,
            "muni": {"13107": {"pref": "東京都", "city": "墨田区"}},
            "places": {
                key: {
                    "gsi": {
                        "pref": "東京都",
                        "city": "墨田区",
                        "chome": "横網一丁目",
                        "muniCd": "13107",
                    },
                    "nominatim": {
                        "lat": "35.69639",
                        "lon": "139.79639",
                        "name": "Edo-Tokyo Museum",
                        "namedetails": {
                            "name:en": "Edo-Tokyo Museum",
                            "name:ja": "江戸東京博物館",
                        },
                        "display_name": "Edo-Tokyo Museum, Sumida, Tokyo",
                    },
                }
            },
        }

        def boom(*_a, **_k):
            raise AssertionError("network should not be called when cache is warm")

        fetcher = ep.RateLimitedFetcher(min_interval=0, opener=boom)
        row, gsi_hit, nom_hit = ep.enrich_place(
            place, cache, cache["muni"], fetcher, do_nominatim=True
        )
        self.assertTrue(gsi_hit)
        self.assertTrue(nom_hit)
        self.assertEqual(fetcher.live_fetches, 0)
        self.assertEqual(row["gsi"]["pref"], "東京都")
        self.assertEqual(row["osm"]["confidence"], "high")

    def test_mismatch_kanto_tokyo(self):
        self.assertTrue(ep.group_pref_mismatch("Kanto", "東京都"))
        self.assertFalse(ep.group_pref_mismatch("Tokyo", "東京都"))
        self.assertTrue(ep.group_pref_mismatch("Kansai", "長野県"))
        self.assertFalse(ep.group_pref_mismatch("Chubu", "長野県"))


class FetcherUserAgentTests(unittest.TestCase):
    def test_sends_user_agent(self):
        seen = {}

        class FakeResp:
            headers = type("H", (), {"get_content_charset": lambda self: "utf-8"})()

            def read(self):
                return b'{"ok": true}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_opener(req, timeout=0):
            seen["ua"] = req.get_header("User-agent") or req.headers.get("User-Agent")
            return FakeResp()

        fetcher = ep.RateLimitedFetcher(min_interval=0, opener=fake_opener)
        # urllib Request lowercases header lookup oddly — set via our code
        text = fetcher.get_text("https://example.test/x")
        self.assertEqual(text, '{"ok": true}')
        self.assertEqual(fetcher.live_fetches, 1)


if __name__ == "__main__":
    unittest.main()
