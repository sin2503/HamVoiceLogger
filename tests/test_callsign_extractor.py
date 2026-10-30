"""Unit tests for Japanese callsign and phonetic extraction."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from callsign_extractor import (
    decode_phonetics,
    extract_callsigns,
    partially_decode_phonetics,
)


class ExtractCallsignsTests(unittest.TestCase):
    def test_direct_japanese_callsigns(self) -> None:
        text = "QSOはJA1YAAです。次は7L1ABCです"
        self.assertEqual(extract_callsigns(text), ["JA1YAA", "7L1ABC"])

    def test_japanese_phonetics(self) -> None:
        text = "ジュリエット アルファ ワン ヤンキー アルファ アルファ"
        self.assertEqual(extract_callsigns(text), ["JA1YAA"])
        self.assertEqual(decode_phonetics(text), "JA1YAA")

    def test_english_phonetics_juliett(self) -> None:
        text = "JULIETT NOVEMBER ONE UNIFORM QUEBEC SIERRA"
        self.assertEqual(extract_callsigns(text), ["JN1UQS"])

    def test_event_and_full_width_forms(self) -> None:
        self.assertEqual(extract_callsigns("８ｊ１ａｂｃ"), ["8J1ABC"])

    def test_short_and_foreign_calls_are_ignored(self) -> None:
        self.assertEqual(extract_callsigns("JA1AA と W1AW と JS1ABC"), ["JS1ABC"])

    def test_duplicates_keep_first_order(self) -> None:
        self.assertEqual(
            extract_callsigns("JA1YAA again JA1YAA and 7L1ABC"),
            ["JA1YAA", "7L1ABC"],
        )

    def test_partial_decode_keeps_unknown_words(self) -> None:
        text = "今日はよい天気ですね。ジュリエット アルファ ワン ヤンキー アルファ アルファ"
        partial = partially_decode_phonetics(text)
        self.assertIn("JA1YAA", partial)
        self.assertTrue(partial.startswith("今日はよい天気ですね"))

    def test_partial_decode_empty_without_phonetics(self) -> None:
        self.assertEqual(partially_decode_phonetics("雑音だけです"), "")

    def test_glued_digit_phonetics_become_candidates(self) -> None:
        # Whisper often emits "ノベンバー1" without a separator before the digit.
        text = "ジュリエット、ノベンバー1、ユニフォームケベックシエラ。"
        self.assertEqual(decode_phonetics(text), "JN1UQS")
        self.assertEqual(partially_decode_phonetics(text), "JN1UQS")
        self.assertEqual(extract_callsigns(text), ["JN1UQS"])

    def test_partial_regex_match_promoted_to_candidate(self) -> None:
        with patch(
            "callsign_extractor.decode_phonetics",
            return_value="JNUQS",
        ):
            with patch(
                "callsign_extractor.partially_decode_phonetics",
                return_value="JN1UQS",
            ):
                self.assertEqual(extract_callsigns("dummy"), ["JN1UQS"])


if __name__ == "__main__":
    unittest.main()
