"""Capture audio and split it into speech-like segments by RMS volume."""

from __future__ import annotations

from queue import Queue
from threading import Lock
from typing import Any

import numpy as np


def refresh_audio_host() -> None:
    """Re-initialize PortAudio so newly connected devices become visible."""
    try:
        import sounddevice as sd
    except ImportError:
        return
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        # Best-effort refresh; callers still query devices afterward.
        pass


def list_input_devices() -> list[tuple[int, str]]:
    """Return ``(index, name)`` for devices that accept microphone input."""
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError(
            "sounddevice is required to capture audio; install requirements.txt"
        ) from exc

    devices: list[tuple[int, str]] = []
    for device in sd.query_devices():
        if int(device.get("max_input_channels") or 0) > 0:
            devices.append((int(device["index"]), str(device["name"])))
    return devices


def _as_device_index(value: Any) -> int | None:
    """Convert sounddevice default/device values to a single int index."""
    if value is None:
        return None
    # sounddevice.default.device is often an _InputOutputPair ([input, output]).
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        try:
            value = value[0]
        except Exception:
            return None
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def resolve_input_device(device: int | str | None = None) -> int | None:
    """Validate a device choice and return a PortAudio input device index.

    ``None`` means system default, but only when a usable default input exists.
    """
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError(
            "sounddevice is required to capture audio; install requirements.txt"
        ) from exc

    inputs = list_input_devices()
    if not inputs:
        raise RuntimeError(
            "入力デバイスが見つかりません。マイクやイヤホンを接続してから、"
            "入力デバイス一覧を更新してもう一度開始してください。"
        )

    if device is None:
        index = _as_device_index(sd.default.device)
        if index is None or index < 0:
            # PortAudio has no default input; fall back to the first capture device.
            return inputs[0][0]
        info = sd.query_devices(index)
        if int(info.get("max_input_channels") or 0) <= 0:
            return inputs[0][0]
        return index

    if isinstance(device, str):
        return device  # sounddevice accepts device name strings

    index = _as_device_index(device)
    if index is None:
        raise RuntimeError(
            "入力デバイスが無効です。接続を確認し、一覧から選び直してください。"
        )
    if index < 0:
        raise RuntimeError(
            "入力デバイスが無効です。接続を確認し、一覧から選び直してください。"
        )
    try:
        info = sd.query_devices(index)
    except Exception as exc:
        raise RuntimeError(
            "選択した入力デバイスを開けません。一覧を更新して選び直してください。"
        ) from exc
    if int(info.get("max_input_channels") or 0) <= 0:
        raise RuntimeError(
            "選択したデバイスは入力に対応していません。マイク側のデバイスを選んでください。"
        )
    return index


def format_audio_error(exc: BaseException) -> str:
    """Return a user-facing Japanese message for common PortAudio failures."""
    text = str(exc).strip() or exc.__class__.__name__
    lowered = text.lower()
    if (
        "error querying device -1" in lowered
        or "error querying device" in lowered
        or "invalid device" in lowered
        or "no default input" in lowered
    ):
        return (
            "入力デバイスを開けませんでした。\n"
            "マイクやイヤホンを接続し、入力デバイスを選び直してから"
            "もう一度「認識開始」してください。"
        )
    return text


class AudioMonitor:
    """Monitor audio input and queue segments after sustained silence.

    Call :meth:`start` to capture from the default input device, then consume
    completed NumPy arrays with :meth:`get_segment`. ``process_audio_frame``
    can also be used directly when audio frames come from another source.
    """

    def __init__(
        self,
        threshold: float = 0.02,
        silence_duration: float = 1.0,
        sample_rate: int = 16_000,
        channels: int = 1,
        device: int | str | None = None,
    ) -> None:
        if threshold < 0:
            raise ValueError("threshold must be non-negative")
        if silence_duration <= 0:
            raise ValueError("silence_duration must be positive")
        if sample_rate <= 0 or channels <= 0:
            raise ValueError("sample_rate and channels must be positive")

        self.threshold = threshold
        self.silence_duration = silence_duration
        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device

        self._segments: Queue[np.ndarray] = Queue()
        self._lock = Lock()
        self._recording = False
        self._silence_elapsed = 0.0
        self._frames: list[np.ndarray] = []
        self._stream: Any | None = None
        self._callback_error: BaseException | None = None

    def start(self) -> None:
        """Start capturing from the selected input device."""
        if self._stream is not None:
            return

        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError(
                "sounddevice is required to capture audio; install requirements.txt"
            ) from exc

        resolved = resolve_input_device(self.device)
        self.device = resolved
        self._callback_error = None

        try:
            stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                device=self.device,
                dtype="float32",
                callback=self._audio_callback,
            )
        except Exception as exc:
            raise RuntimeError(format_audio_error(exc)) from exc

        self._stream = stream
        try:
            stream.start()
        except Exception as exc:
            self._stream = None
            try:
                stream.close()
            except Exception:
                pass
            raise RuntimeError(format_audio_error(exc)) from exc

    def stop(self) -> None:
        """Stop capture and discard any segment that has not finished."""
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

        with self._lock:
            self._reset_segment()

    def get_segment(self, timeout: float | None = None) -> np.ndarray:
        """Return the next completed segment, blocking until one is ready."""
        if self._callback_error is not None:
            exc = self._callback_error
            self._callback_error = None
            raise RuntimeError(format_audio_error(exc)) from exc
        return self._segments.get(timeout=timeout)

    def process_audio_frame(self, frame: np.ndarray) -> None:
        """Process one float audio frame, primarily useful for testing."""
        audio = np.asarray(frame, dtype=np.float32)
        if audio.ndim == 1:
            audio = audio.reshape(-1, 1)
        if audio.ndim != 2 or audio.shape[1] != self.channels:
            raise ValueError(f"frame must have {self.channels} audio channel(s)")
        if audio.shape[0] == 0:
            return
        audio = audio.copy()

        completed_segment = None
        with self._lock:
            volume = float(np.sqrt(np.mean(np.square(audio))))
            if not self._recording:
                if volume < self.threshold:
                    return
                self._recording = True
                self._frames = [audio]
                self._silence_elapsed = 0.0
                return

            self._frames.append(audio)
            if volume < self.threshold:
                self._silence_elapsed += audio.shape[0] / self.sample_rate
                if self._silence_elapsed >= self.silence_duration:
                    completed_segment = np.concatenate(self._frames, axis=0)
                    self._reset_segment()
            else:
                self._silence_elapsed = 0.0

        if completed_segment is not None:
            self._segments.put(completed_segment)

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: Any,
        status: Any,
    ) -> None:
        if status and self._callback_error is None:
            # Keep the first host-side failure; the worker loop surfaces it once.
            self._callback_error = RuntimeError(str(status))
        self.process_audio_frame(indata)

    def _reset_segment(self) -> None:
        self._recording = False
        self._silence_elapsed = 0.0
        self._frames = []

    def __enter__(self) -> AudioMonitor:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.stop()
