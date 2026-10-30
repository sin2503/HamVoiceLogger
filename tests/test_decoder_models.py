"""Unit tests for multi-engine model catalog and download helpers."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from decoder import (
    ENGINE_FASTER_WHISPER,
    ENGINE_MOONSHINE,
    ENGINE_WHISPER_CPP,
    MODEL_CATALOG,
    ModelManagerError,
    download_model,
    ensure_engine_runtime,
    format_byte_size,
    format_model_choice,
    is_model_downloaded,
    list_downloaded_models,
    list_model_statuses,
    normalize_model_key,
    parse_model_choice,
)


class ModelHelperTests(unittest.TestCase):
    def test_normalize_accepts_legacy_short_names(self) -> None:
        self.assertEqual(normalize_model_key("small"), "faster-whisper:small")
        self.assertEqual(
            normalize_model_key("whisper.cpp:tiny"),
            "whisper.cpp:tiny",
        )

    def test_format_and_parse_choice(self) -> None:
        key = "faster-whisper:small"
        label = format_model_choice(key)
        self.assertEqual(label, "faster-whisper · small")
        self.assertEqual(parse_model_choice(label), key)
        self.assertEqual(parse_model_choice("small"), key)

    def test_local_directory_counts_as_downloaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "custom-model"
            model_dir.mkdir()
            (model_dir / "model.bin").write_bytes(b"x")
            self.assertTrue(is_model_downloaded(str(model_dir)))

    def test_missing_local_directory_is_not_downloaded(self) -> None:
        with patch(
            "faster_whisper.utils.download_model", side_effect=FileNotFoundError
        ):
            self.assertFalse(is_model_downloaded("tiny"))

    def test_list_statuses_covers_all_engines(self) -> None:
        wanted = {
            "faster-whisper:small",
            "whisper.cpp:tiny",
            "moonshine:tiny-ja",
        }

        with patch(
            "decoder.is_model_downloaded",
            side_effect=lambda name: name in wanted,
        ):
            statuses = {row["key"]: row for row in list_model_statuses()}
            downloaded = list_downloaded_models()

        self.assertEqual(len(statuses), len(MODEL_CATALOG))
        self.assertEqual(statuses["faster-whisper:small"]["status"], "取得済み")
        self.assertEqual(statuses["faster-whisper:tiny"]["status"], "未取得")
        self.assertEqual(statuses["whisper.cpp:tiny"]["engine"], ENGINE_WHISPER_CPP)
        self.assertEqual(statuses["moonshine:tiny-ja"]["engine"], ENGINE_MOONSHINE)
        self.assertEqual(
            set(downloaded),
            wanted,
        )
        self.assertTrue(
            all(row["engine"] for row in statuses.values())
        )
        self.assertIn(ENGINE_FASTER_WHISPER, {row["engine"] for row in statuses.values()})

    def test_download_rejects_unknown_name(self) -> None:
        with self.assertRaises(ModelManagerError):
            download_model("not-a-real-model")

    def test_ensure_engine_runtime_installs_when_missing(self) -> None:
        reports: list[str] = []
        with patch("decoder.is_engine_runtime_ready", side_effect=[False, True]):
            with patch("decoder._pip_install") as pip_install:
                ensure_engine_runtime(
                    ENGINE_MOONSHINE,
                    progress_callback=lambda _c, _t, desc: reports.append(desc),
                )
        pip_install.assert_called_once_with(("useful-moonshine-onnx",))
        self.assertTrue(any("インストール中" in item for item in reports))

    def test_download_faster_whisper_uses_snapshot(self) -> None:
        reports: list[tuple[int, int, str]] = []

        def fake_snapshot_download(*_args, tqdm_class=None, **_kwargs):
            bar = tqdm_class(total=100, desc="Fetching 1 files")
            bar.update(40)
            bar.close()
            return "/cache/small"

        with patch("decoder.ensure_engine_runtime"):
            with patch(
                "decoder._faster_whisper_repo_id",
                return_value="Systran/faster-whisper-small",
            ):
                with patch(
                    "huggingface_hub.snapshot_download",
                    side_effect=fake_snapshot_download,
                ):
                    path = download_model(
                        "small",
                        progress_callback=lambda current, total, desc: reports.append(
                            (current, total, desc)
                        ),
                    )

        self.assertEqual(path, "/cache/small")
        self.assertTrue(reports)
        self.assertEqual(reports[-1][:2], (40, 100))

    def test_download_whisper_cpp_writes_ggml(self) -> None:
        reports: list[tuple[int, int, str]] = []
        payload = b"ggml-bytes"

        class FakeResponse:
            headers = {"Content-Length": str(len(payload))}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, size: int = -1):
                if not hasattr(self, "_buf"):
                    self._buf = io.BytesIO(payload)
                return self._buf.read(size)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("decoder.ensure_engine_runtime"):
                with patch("decoder.models_root", return_value=root):
                    with patch("decoder.urlopen", return_value=FakeResponse()):
                        path = download_model(
                            "whisper.cpp:tiny",
                            progress_callback=lambda current, total, desc: reports.append(
                                (current, total, desc)
                            ),
                        )
                destination = Path(path)
                self.assertTrue(destination.is_file())
                self.assertEqual(destination.name, "ggml-tiny.bin")
                self.assertEqual(destination.read_bytes(), payload)
                self.assertTrue(reports)
                self.assertEqual(reports[-1][0], len(payload))

    def test_download_moonshine_uses_local_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_snapshot_download(*_args, local_dir=None, **_kwargs):
                dest = Path(local_dir)
                float_dir = dest / "onnx" / "merged" / "tiny-ja" / "float"
                float_dir.mkdir(parents=True, exist_ok=True)
                (float_dir / "encoder_model.onnx").write_bytes(b"enc")
                (float_dir / "decoder_model_merged.onnx").write_bytes(b"dec")
                return str(dest)

            with patch("decoder.ensure_engine_runtime"):
                with patch("decoder.models_root", return_value=root):
                    with patch(
                        "huggingface_hub.snapshot_download",
                        side_effect=fake_snapshot_download,
                    ):
                        path = download_model("moonshine:tiny-ja")
                    self.assertTrue((Path(path) / "encoder_model.onnx").is_file())
                    self.assertTrue(
                        (Path(path) / "decoder_model_merged.onnx").is_file()
                    )
                    self.assertTrue(is_model_downloaded("moonshine:tiny-ja"))

    def test_format_byte_size(self) -> None:
        self.assertEqual(format_byte_size(512), "512 B")
        self.assertEqual(format_byte_size(1536), "1.5 KB")
        self.assertEqual(format_byte_size(5 * 1024 * 1024), "5.0 MB")


if __name__ == "__main__":
    unittest.main()
