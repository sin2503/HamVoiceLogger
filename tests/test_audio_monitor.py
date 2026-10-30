"""Unit tests for audio device helpers."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from audio_monitor import _as_device_index, format_audio_error, resolve_input_device


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


if __name__ == "__main__":
    unittest.main()
