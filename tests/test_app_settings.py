"""Unit tests for persisted GUI settings."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app_settings import (
    AppSettings,
    clamp_window,
    format_freq_mode_preset,
    format_geometry,
    load_settings,
    match_device_label,
    normalize_freq_mode_presets,
    normalize_report_presets,
    parse_geometry,
    parse_report_preset,
    save_settings,
)


class AppSettingsTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            original = AppSettings(
                model="moonshine:tiny-ja",
                language="ja",
                beam_size=3,
                threshold=0.05,
                silence_duration=1.5,
                device_index=2,
                device_name="AirPods Pro",
                window_width=1100,
                window_height=800,
                window_x=40,
                window_y=60,
                last_frequency="145.000",
                last_mode="FM",
                freq_mode_presets=[
                    {"frequency": "145.000", "mode": "FM"},
                    {"frequency": "7.100", "mode": "SSB"},
                ],
                report_presets=["59/59", "599/599"],
                time_display="utc",
                default_qsl_sent="未送信",
                default_qsl_received="未受領",
                default_qsl_via="JARL",
                popup_enabled=False,
                popup_width=300,
                popup_height=240,
                popup_x=40,
                popup_y=60,
            )
            save_settings(original, path)
            loaded = load_settings(path)
            self.assertEqual(loaded, original)

    def test_invalid_file_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("{not-json", encoding="utf-8")
            loaded = load_settings(path)
            self.assertEqual(loaded.model, "faster-whisper:small")
            self.assertEqual(loaded.beam_size, 5)

    def test_clamp_window_keeps_on_screen(self) -> None:
        width, height, x, y = clamp_window(
            2000, 2000, -500, -20, screen_width=1280, screen_height=800
        )
        self.assertLessEqual(width, 1280)
        self.assertLessEqual(height, 800)
        self.assertGreaterEqual(width, 900)
        self.assertGreaterEqual(height, 640)
        self.assertIsNotNone(x)
        self.assertIsNotNone(y)
        assert x is not None and y is not None
        self.assertGreaterEqual(y, 0)

    def test_match_device_prefers_name(self) -> None:
        device_map = {
            "システム既定": None,
            "1: Built-in Mic": 1,
            "2: AirPods Pro": 2,
        }
        label = match_device_label(device_map, device_index=1, device_name="AirPods Pro")
        self.assertEqual(label, "2: AirPods Pro")

    def test_parse_and_format_geometry(self) -> None:
        self.assertEqual(parse_geometry("1100x800+40+60"), (1100, 800, 40, 60))
        self.assertEqual(parse_geometry("1024x768-20+10"), (1024, 768, -20, 10))
        self.assertEqual(format_geometry(1100, 800, 40, 60), "1100x800+40+60")
        self.assertEqual(format_geometry(1100, 800, -20, 10), "1100x800-20+10")

    def test_normalize_freq_mode_presets(self) -> None:
        presets = normalize_freq_mode_presets(
            ["145.000 FM", "433.000/FM", {"frequency": "7.100", "mode": "SSB"}]
        )
        self.assertEqual(
            presets,
            [
                {"frequency": "145.000", "mode": "FM"},
                {"frequency": "433.000", "mode": "FM"},
                {"frequency": "7.100", "mode": "SSB"},
            ],
        )
        self.assertEqual(format_freq_mode_preset(presets[0]), "145.000 · FM")

    def test_parse_report_preset(self) -> None:
        self.assertEqual(parse_report_preset("59/57"), ("59", "57"))
        self.assertEqual(parse_report_preset("599"), ("599", "599"))
        self.assertEqual(normalize_report_presets(["59/59", "bad", "59/59"]), ["59/59"])


if __name__ == "__main__":
    unittest.main()
