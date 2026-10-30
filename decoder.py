"""Local speech-to-text backends and model download helpers."""

from __future__ import annotations

import importlib
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

import numpy as np

ProgressCallback = Callable[[int, int, str], None]

ENGINE_FASTER_WHISPER = "faster-whisper"
ENGINE_WHISPER_CPP = "whisper.cpp"
ENGINE_MOONSHINE = "moonshine"

# Pip package names to install when an optional engine is first used.
ENGINE_PIP_PACKAGES: dict[str, tuple[str, ...]] = {
    ENGINE_FASTER_WHISPER: ("faster-whisper",),
    ENGINE_WHISPER_CPP: ("pywhispercpp",),
    ENGINE_MOONSHINE: ("useful-moonshine-onnx",),
}

# Kept for CLI help / backward-compatible short names (faster-whisper).
WHISPER_MODEL_CHOICES = (
    "tiny",
    "base",
    "small",
    "medium",
    "large-v2",
    "large-v3",
    "turbo",
)


@dataclass(frozen=True)
class ModelSpec:
    key: str
    engine: str
    label: str
    # faster-whisper size, whisper.cpp ggml name, or moonshine repo id
    source: str


def models_root() -> Path:
    return Path.home() / ".ham_voice_logger" / "models"


def _whisper_cpp_dir() -> Path:
    return models_root() / "whisper.cpp"


def _moonshine_dir(label: str) -> Path:
    return models_root() / "moonshine" / label


MODEL_CATALOG: tuple[ModelSpec, ...] = (
    *(
        ModelSpec(
            key=f"{ENGINE_FASTER_WHISPER}:{name}",
            engine=ENGINE_FASTER_WHISPER,
            label=name,
            source=name,
        )
        for name in WHISPER_MODEL_CHOICES
    ),
    *(
        ModelSpec(
            key=f"{ENGINE_WHISPER_CPP}:{name}",
            engine=ENGINE_WHISPER_CPP,
            label=name,
            source=name,
        )
        for name in (
            "tiny",
            "base",
            "small",
            "medium",
            "large-v2",
            "large-v3",
            "large-v3-turbo",
        )
    ),
    # useful-moonshine-onnx loads files from UsefulSensors/moonshine
    # under onnx/merged/<label>/float/. Japanese ONNX is tiny-ja only.
    *(
        ModelSpec(
            key=f"{ENGINE_MOONSHINE}:{name}",
            engine=ENGINE_MOONSHINE,
            label=name,
            source="UsefulSensors/moonshine",
        )
        for name in ("tiny-ja", "tiny", "base")
    ),
)

MOONSHINE_ONNX_FILES = ("encoder_model.onnx", "decoder_model_merged.onnx")

_MODEL_BY_KEY = {spec.key: spec for spec in MODEL_CATALOG}


class ModelManagerError(RuntimeError):
    """Expected model download / lookup error for the GUI."""


def _module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def is_engine_runtime_ready(engine: str) -> bool:
    """Return True when the Python packages for an engine can be imported."""
    if engine == ENGINE_FASTER_WHISPER:
        return _module_available("faster_whisper")
    if engine == ENGINE_WHISPER_CPP:
        return _module_available("pywhispercpp")
    if engine == ENGINE_MOONSHINE:
        return _module_available("moonshine_onnx") or _module_available("moonshine")
    return False


def _pip_install(packages: tuple[str, ...]) -> None:
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        *packages,
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise ModelManagerError(
            f"パッケージのインストールを開始できませんでした: {exc}"
        ) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        if len(detail) > 800:
            detail = detail[-800:]
        raise ModelManagerError(
            "エンジン用パッケージのインストールに失敗しました。\n"
            f"コマンド: {' '.join(command)}\n"
            f"{detail or f'exit code {completed.returncode}'}"
        )


def ensure_engine_runtime(
    engine: str,
    progress_callback: ProgressCallback | None = None,
) -> None:
    """Install optional engine packages into the current interpreter if needed."""
    packages = ENGINE_PIP_PACKAGES.get(engine)
    if not packages:
        return
    if is_engine_runtime_ready(engine):
        return
    if progress_callback is not None:
        progress_callback(0, 0, f"{engine} 用パッケージをインストール中")
    _pip_install(packages)
    importlib.invalidate_caches()
    if not is_engine_runtime_ready(engine):
        raise ModelManagerError(
            f"{engine} 用パッケージ（{', '.join(packages)}）を入れましたが、"
            "読み込めませんでした。アプリを再起動してからもう一度試してください。"
        )
    if progress_callback is not None:
        progress_callback(1, 1, f"{engine} 用パッケージの準備が完了しました")


def normalize_model_key(model_name: str) -> str:
    """Accept catalog keys or legacy faster-whisper short names."""
    name = model_name.strip()
    if not name:
        return name
    if name in _MODEL_BY_KEY:
        return name
    legacy = f"{ENGINE_FASTER_WHISPER}:{name}"
    if legacy in _MODEL_BY_KEY:
        return legacy
    path = Path(name).expanduser()
    if path.exists():
        return str(path)
    return name


def get_model_spec(model_key: str) -> ModelSpec | None:
    return _MODEL_BY_KEY.get(normalize_model_key(model_key))


def format_model_choice(model_key: str) -> str:
    spec = get_model_spec(model_key)
    if spec is None:
        return model_key
    return f"{spec.engine} · {spec.label}"


def parse_model_choice(choice: str) -> str:
    """Map a combo label or raw key back to a catalog key."""
    text = choice.strip()
    if " · " in text:
        engine, label = text.split(" · ", 1)
        key = f"{engine}:{label}"
        if key in _MODEL_BY_KEY:
            return key
    return normalize_model_key(text)


def _faster_whisper_repo_id(model_name: str) -> str | None:
    try:
        from faster_whisper.utils import _MODELS
    except ImportError as exc:
        raise ModelManagerError(
            "faster-whisper is required; install requirements.txt"
        ) from exc
    if "/" in model_name:
        return model_name
    return _MODELS.get(model_name)


def _whisper_cpp_filename(label: str) -> str:
    return f"ggml-{label}.bin"


def _whisper_cpp_path(label: str) -> Path:
    return _whisper_cpp_dir() / _whisper_cpp_filename(label)


def _is_faster_whisper_downloaded(source: str) -> bool:
    path = Path(source).expanduser()
    if path.exists() and path.is_dir() and (path / "model.bin").exists():
        return True
    try:
        from faster_whisper.utils import download_model
    except ImportError:
        return False
    try:
        download_model(source, local_files_only=True)
        return True
    except Exception:
        return False


def _is_moonshine_downloaded(label: str) -> bool:
    directory = _moonshine_dir(label)
    return all((directory / name).is_file() for name in MOONSHINE_ONNX_FILES)


def is_model_downloaded(model_name: str) -> bool:
    """Return True when the named model is available locally."""
    path = Path(model_name).expanduser()
    if path.exists() and path.is_dir() and (path / "model.bin").exists():
        return True
    if path.exists() and path.is_file() and path.suffix.lower() in {".bin", ".gguf"}:
        return True

    key = normalize_model_key(model_name)
    spec = get_model_spec(key)
    if spec is None:
        return _is_faster_whisper_downloaded(model_name)

    if spec.engine == ENGINE_FASTER_WHISPER:
        return _is_faster_whisper_downloaded(spec.source)
    if spec.engine == ENGINE_WHISPER_CPP:
        return _whisper_cpp_path(spec.label).is_file()
    if spec.engine == ENGINE_MOONSHINE:
        return _is_moonshine_downloaded(spec.label)
    return False


def list_model_statuses(
    model_names: tuple[str, ...] | None = None,
) -> list[dict[str, str]]:
    """Return download status rows for the managed model list."""
    specs = MODEL_CATALOG
    if model_names is not None:
        keys = {normalize_model_key(name) for name in model_names}
        specs = tuple(spec for spec in MODEL_CATALOG if spec.key in keys)
    rows = []
    for spec in specs:
        rows.append(
            {
                "key": spec.key,
                "name": spec.key,
                "engine": spec.engine,
                "label": spec.label,
                "status": "取得済み" if is_model_downloaded(spec.key) else "未取得",
            }
        )
    return rows


def list_downloaded_models(
    model_names: tuple[str, ...] | None = None,
) -> list[str]:
    """Return managed model keys that are already downloaded."""
    return [row["key"] for row in list_model_statuses(model_names) if row["status"] == "取得済み"]


def format_byte_size(num_bytes: int) -> str:
    """Return a short human-readable byte size."""
    value = float(max(0, num_bytes))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _progress_tqdm_class(progress_callback: ProgressCallback | None) -> Any:
    from tqdm.auto import tqdm

    class ProgressTqdm(tqdm):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["disable"] = True
            super().__init__(*args, **kwargs)
            self._emit()

        def update(self, n: float | None = 1) -> bool | None:
            before = int(getattr(self, "n", 0) or 0)
            result = super().update(n)
            after = int(getattr(self, "n", 0) or 0)
            if n and after == before:
                self.n = before + int(n)
            self._emit()
            return result

        def refresh(self, *args: Any, **kwargs: Any) -> None:
            super().refresh(*args, **kwargs)
            self._emit()

        def _emit(self) -> None:
            if progress_callback is None:
                return
            total = int(getattr(self, "total", 0) or 0)
            current = int(getattr(self, "n", 0) or 0)
            description = str(getattr(self, "desc", "") or "").strip()
            progress_callback(current, total, description)

    return ProgressTqdm


def _download_faster_whisper(
    source: str, progress_callback: ProgressCallback | None
) -> str:
    repo_id = _faster_whisper_repo_id(source)
    if repo_id is None:
        raise ModelManagerError(f"未対応の faster-whisper モデルです: {source}")
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise ModelManagerError(
            "huggingface_hub が必要です。requirements.txt をインストールしてください。"
        ) from exc
    try:
        return snapshot_download(
            repo_id,
            local_files_only=False,
            allow_patterns=[
                "config.json",
                "preprocessor_config.json",
                "model.bin",
                "tokenizer.json",
                "vocabulary.*",
            ],
            tqdm_class=_progress_tqdm_class(progress_callback),
        )
    except Exception as exc:
        raise ModelManagerError(f"モデルのダウンロードに失敗しました: {exc}") from exc


def _download_url_with_progress(
    url: str,
    destination: Path,
    progress_callback: ProgressCallback | None,
    description: str,
) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    request = Request(url, headers={"User-Agent": "HAMVoiceLogger/0.1"})
    try:
        with urlopen(request, timeout=60) as response:
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            if progress_callback is not None:
                progress_callback(0, total, description)
            with partial.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback is not None:
                        progress_callback(downloaded, total, description)
    except Exception as exc:
        if partial.exists():
            partial.unlink()
        raise ModelManagerError(f"モデルのダウンロードに失敗しました: {exc}") from exc
    partial.replace(destination)
    if progress_callback is not None:
        progress_callback(total or downloaded, total or downloaded, "Download complete")
    return str(destination)


def _download_whisper_cpp(
    label: str, progress_callback: ProgressCallback | None
) -> str:
    destination = _whisper_cpp_path(label)
    if destination.is_file():
        return str(destination)
    url = (
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
        + _whisper_cpp_filename(label)
    )
    return _download_url_with_progress(
        url,
        destination,
        progress_callback,
        f"Downloading whisper.cpp {label}",
    )


def _download_moonshine(
    label: str, repo_id: str, progress_callback: ProgressCallback | None
) -> str:
    """Download Moonshine ONNX weights compatible with useful-moonshine-onnx."""
    destination = _moonshine_dir(label)
    destination.mkdir(parents=True, exist_ok=True)
    if _is_moonshine_downloaded(label):
        return str(destination)
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise ModelManagerError(
            "huggingface_hub が必要です。requirements.txt をインストールしてください。"
        ) from exc

    staging = destination / ".staging"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    try:
        snapshot_download(
            repo_id,
            allow_patterns=[f"onnx/merged/{label}/float/*.onnx"],
            local_dir=str(staging),
            local_files_only=False,
            tqdm_class=_progress_tqdm_class(progress_callback),
        )
        float_dir = staging / "onnx" / "merged" / label / "float"
        for name in MOONSHINE_ONNX_FILES:
            source_path = float_dir / name
            if not source_path.is_file():
                raise ModelManagerError(
                    f"Moonshine ONNX ファイルが見つかりません: {label}/{name}\n"
                    "このモデル名は useful-moonshine-onnx 非対応の可能性があります。"
                )
            shutil.copy2(source_path, destination / name)
    except ModelManagerError:
        raise
    except Exception as exc:
        raise ModelManagerError(f"モデルのダウンロードに失敗しました: {exc}") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    if not _is_moonshine_downloaded(label):
        raise ModelManagerError(
            f"Moonshine モデル「{label}」の ONNX ファイルを取得できませんでした。"
        )
    return str(destination)


def download_model(
    model_name: str,
    progress_callback: ProgressCallback | None = None,
) -> str:
    """Download a managed model into the local cache.

    For optional engines, installs the runtime package first when missing.
    """
    key = normalize_model_key(model_name)
    if not key:
        raise ModelManagerError("ダウンロードするモデル名を指定してください。")
    spec = get_model_spec(key)
    if spec is None:
        raise ModelManagerError(
            f"未対応のモデル名です: {model_name}\n"
            "設定画面の一覧から選んでください。"
        )
    ensure_engine_runtime(spec.engine, progress_callback=progress_callback)
    if spec.engine == ENGINE_FASTER_WHISPER:
        return _download_faster_whisper(spec.source, progress_callback)
    if spec.engine == ENGINE_WHISPER_CPP:
        return _download_whisper_cpp(spec.label, progress_callback)
    if spec.engine == ENGINE_MOONSHINE:
        return _download_moonshine(spec.label, spec.source, progress_callback)
    raise ModelManagerError(f"未対応のエンジンです: {spec.engine}")


def download_whisper_model(
    model_name: str,
    progress_callback: ProgressCallback | None = None,
) -> str:
    """Backward-compatible alias for download_model()."""
    return download_model(model_name, progress_callback=progress_callback)


def _mono_waveform(audio: np.ndarray) -> np.ndarray:
    waveform = np.asarray(audio, dtype=np.float32)
    if waveform.ndim == 2:
        waveform = waveform.mean(axis=1)
    if waveform.ndim != 1 or waveform.size == 0:
        raise ValueError("audio must contain at least one sample")
    return waveform


def _write_temp_wav(waveform: np.ndarray, sample_rate: int) -> str:
    pcm = np.clip(waveform, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    handle.close()
    with wave.open(handle.name, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())
    return handle.name


class SpeechDecoder:
    """Transcribe completed 16 kHz audio segments with a selected backend."""

    def __init__(
        self,
        model_size: str = "small",
        language: str = "ja",
        device: str = "cpu",
        compute_type: str = "int8",
        beam_size: int = 5,
        local_files_only: bool = True,
    ) -> None:
        self.language = language
        self.beam_size = beam_size
        self.model_key = normalize_model_key(model_size)
        self.spec = get_model_spec(self.model_key)
        self._backend = self.spec.engine if self.spec else ENGINE_FASTER_WHISPER
        self._model: Any = None
        self._moonshine_module: Any = None

        if local_files_only and not is_model_downloaded(self.model_key):
            raise RuntimeError(
                f"モデル「{format_model_choice(self.model_key)}」が未取得です。"
                "設定画面からダウンロードしてください。"
            )

        engine = self._backend
        try:
            ensure_engine_runtime(engine)
        except ModelManagerError as exc:
            raise RuntimeError(str(exc)) from exc

        if self.spec is None or self.spec.engine == ENGINE_FASTER_WHISPER:
            self._init_faster_whisper(device, compute_type, local_files_only)
        elif self.spec.engine == ENGINE_WHISPER_CPP:
            self._init_whisper_cpp()
        elif self.spec.engine == ENGINE_MOONSHINE:
            self._init_moonshine()
        else:
            raise RuntimeError(f"未対応のエンジンです: {self.spec.engine}")

    def _init_faster_whisper(
        self, device: str, compute_type: str, local_files_only: bool
    ) -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is required for transcription; install requirements.txt"
            ) from exc
        source = self.spec.source if self.spec else self.model_key
        self._model = WhisperModel(
            source,
            device=device,
            compute_type=compute_type,
            local_files_only=local_files_only,
        )

    def _init_whisper_cpp(self) -> None:
        try:
            from pywhispercpp.model import Model
        except ImportError as exc:
            raise RuntimeError(
                "whisper.cpp 用パッケージ（pywhispercpp）を読み込めません。"
                "設定画面でモデルを再ダウンロードするか、アプリを再起動してください。"
            ) from exc
        assert self.spec is not None
        model_path = _whisper_cpp_path(self.spec.label)
        if not model_path.is_file():
            raise RuntimeError(
                f"whisper.cpp モデルが見つかりません: {model_path}\n"
                "設定画面からダウンロードしてください。"
            )
        self._model = Model(
            str(model_path),
            language=self.language,
            print_realtime=False,
            print_progress=False,
        )

    def _init_moonshine(self) -> None:
        assert self.spec is not None
        model_dir = _moonshine_dir(self.spec.label)
        if not _is_moonshine_downloaded(self.spec.label):
            raise RuntimeError(
                f"Moonshine モデルが見つかりません: {model_dir}\n"
                "設定画面からダウンロードしてください。"
                "※ Moonshine は ONNX（encoder / decoder）が必要です。"
            )
        try:
            import moonshine_onnx as moonshine  # type: ignore
            from moonshine_onnx import MoonshineOnnxModel  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "Moonshine 用パッケージ（useful-moonshine-onnx）を読み込めません。"
                "設定画面でモデルを再ダウンロードするか、アプリを再起動してください。"
            ) from exc
        self._moonshine_module = moonshine
        self._model = MoonshineOnnxModel(
            models_dir=str(model_dir),
            model_name=self.spec.label,
        )

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16_000) -> str:
        """Return the recognized text for a mono or multichannel audio segment."""
        if sample_rate != 16_000:
            raise ValueError("audio must be sampled at 16000 Hz")
        waveform = _mono_waveform(audio)
        if self._backend == ENGINE_FASTER_WHISPER:
            return self._transcribe_faster_whisper(waveform)
        if self._backend == ENGINE_WHISPER_CPP:
            return self._transcribe_whisper_cpp(waveform, sample_rate)
        if self._backend == ENGINE_MOONSHINE:
            return self._transcribe_moonshine(waveform, sample_rate)
        raise RuntimeError(f"未対応のエンジンです: {self._backend}")

    def _transcribe_faster_whisper(self, waveform: np.ndarray) -> str:
        segments, _ = self._model.transcribe(
            waveform,
            language=self.language,
            beam_size=self.beam_size,
            condition_on_previous_text=False,
            vad_filter=False,
        )
        return "".join(segment.text for segment in segments).strip()

    def _transcribe_whisper_cpp(self, waveform: np.ndarray, sample_rate: int) -> str:
        wav_path = _write_temp_wav(waveform, sample_rate)
        try:
            segments = self._model.transcribe(wav_path, language=self.language)
            return "".join(getattr(segment, "text", str(segment)) for segment in segments).strip()
        finally:
            Path(wav_path).unlink(missing_ok=True)

    def _transcribe_moonshine(self, waveform: np.ndarray, sample_rate: int) -> str:
        wav_path = _write_temp_wav(waveform, sample_rate)
        try:
            module = self._moonshine_module
            if module is None:
                raise RuntimeError("Moonshine が初期化されていません。")
            result = module.transcribe(wav_path, self._model)
            if isinstance(result, (list, tuple)):
                return " ".join(str(item) for item in result).strip()
            return str(result).strip()
        finally:
            Path(wav_path).unlink(missing_ok=True)
