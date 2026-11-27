"""Unit tests for audio device helpers."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from audio_monitor import (
    AudioMonitor,
    _as_device_index,
    format_audio_error,
    level_fraction,
    normalize_segment,
    resolve_input_device,
    rms_from_fraction,
)


class _FakeInputOutputPair:
    def __init__(self, values):
        self._values = values

    def __getitem__(self, index):
        return self._values[index]


class AudioHelperTests(unittest.TestCase):
    def test_format_device_minus_one(self) -> None:
        message = format_audio_error(RuntimeError("Error querying device -1"))
        self.assertIn("入力デバイス", message)
        self.assertNotIn("device -1", message)

    def test_as_device_index_accepts_input_output_pair(self) -> None:
        self.assertEqual(_as_device_index(_FakeInputOutputPair([2, 3])), 2)
        self.assertEqual(_as_device_index([4, 5]), 4)
        self.assertEqual(_as_device_index(7), 7)

    def test_resolve_falls_back_when_default_missing(self) -> None:
        fake_devices = [
            {"index": 0, "name": "Speaker", "max_input_channels": 0},
            {"index": 1, "name": "Mic", "max_input_channels": 1},
        ]

        class FakeSd:
            class default:
                device = _FakeInputOutputPair([-1, 0])

            @staticmethod
            def query_devices(index=None):
                if index is None:
                    return fake_devices
                return fake_devices[index]

        with patch.dict("sys.modules", {"sounddevice": FakeSd}):
            with patch("audio_monitor.list_input_devices", return_value=[(1, "Mic")]):
                self.assertEqual(resolve_input_device(None), 1)

    def test_level_fraction_uses_decibels(self) -> None:
        self.assertEqual(level_fraction(0.0), 0.0)
        self.assertAlmostEqual(level_fraction(0.01), 1 / 3, places=2)
        self.assertEqual(level_fraction(1.0), 1.0)

    def test_rms_from_fraction_matches_level_fraction(self) -> None:
        for rms in (0.001, 0.005, 0.01, 0.02, 0.5):
            self.assertAlmostEqual(rms_from_fraction(level_fraction(rms)), rms, places=4)

    def test_quiet_frame_still_reports_level(self) -> None:
        seen: list[tuple[float, bool]] = []
        monitor = AudioMonitor(
            threshold=0.5,
            on_level=lambda rms, recording: seen.append((rms, recording)),
            level_interval=0,
        )
        monitor.process_audio_frame(np.full((160, 1), 0.01, dtype=np.float32))
        self.assertEqual(len(seen), 1)
        self.assertAlmostEqual(seen[0][0], 0.01, places=4)
        self.assertFalse(seen[0][1])
        self.assertTrue(monitor._segments.empty())

    def test_continuous_audio_splits_without_silence(self) -> None:
        monitor = AudioMonitor(
            threshold=0.01,
            silence_duration=5,
            sample_rate=16_000,
            max_segment_duration=0.2,
        )
        frame = np.full((1600, 1), 0.2, dtype=np.float32)
        monitor.process_audio_frame(frame)
        self.assertTrue(monitor._segments.empty())
        monitor.process_audio_frame(frame)
        segment = monitor._segments.get_nowait()
        self.assertEqual(segment.shape[0], 3200)

    def test_normalize_lifts_quiet_audio_only(self) -> None:
        quiet = np.full((8, 1), 0.05, dtype=np.float32)
        self.assertAlmostEqual(float(np.max(normalize_segment(quiet))), 0.5, places=4)
        silence = np.zeros((8, 1), dtype=np.float32)
        self.assertEqual(float(np.max(normalize_segment(silence))), 0.0)


if __name__ == "__main__":
    unittest.main()
