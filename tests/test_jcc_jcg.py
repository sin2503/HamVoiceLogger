"""Unit tests for JCC/JCG code lookup."""

from __future__ import annotations

import unittest

from jcc_jcg import lookup_station_code, normalize_station_code, search_station_codes


class JccJcgTests(unittest.TestCase):
    def test_normalize_fullwidth_digits(self) -> None:
        self.assertEqual(normalize_station_code("１００１０６"), "100106")

    def test_tokyo_ward_and_city(self) -> None:
        ward = lookup_station_code("100106")
        assert ward is not None
        self.assertEqual(ward.qth, "東京都台東区")
        self.assertIn("区", ward.label)

        city = lookup_station_code("1002")
        assert city is not None
        self.assertEqual(city.qth, "東京都八王子市")

    def test_ordinance_ward_uses_parent_city(self) -> None:
        entry = lookup_station_code("010101")
        assert entry is not None
        self.assertEqual(entry.qth, "札幌市中央区")

    def test_gun_code(self) -> None:
        entry = lookup_station_code("01002")
        assert entry is not None
        self.assertEqual(entry.qth, "北海道足寄郡")
        self.assertIn("JCG", entry.label)

    def test_unknown_code(self) -> None:
        self.assertIsNone(lookup_station_code("999999"))

    def test_search_by_name(self) -> None:
        hits = search_station_codes("台東")
        self.assertTrue(hits)
        self.assertEqual(hits[0].code, "100106")


if __name__ == "__main__":
    unittest.main()
