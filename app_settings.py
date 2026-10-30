"""Persist GUI preferences under ~/.ham_voice_logger/settings.json."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from time_format import normalize_time_display

_GEOMETRY_RE = re.compile(
    r"^(?P<width>\d+)x(?P<height>\d+)(?:(?P<x>[+-]\d+)(?P<y>[+-]\d+))?$"
)


DEFAULT_MODEL = "faster-whisper:small"
DEFAULT_LANGUAGE = "ja"
DEFAULT_BEAM_SIZE = 5
DEFAULT_THRESHOLD = 0.02
DEFAULT_SILENCE_DURATION = 1.0
DEFAULT_MODE = "FM"
DEFAULT_FREQ_MODE_PRESETS: list[dict[str, str]] = [
    {"frequency": "145.000", "mode": "FM"},
    {"frequency": "433.000", "mode": "FM"},
    {"frequency": "7.100", "mode": "SSB"},
    {"frequency": "14.200", "mode": "SSB"},
    {"frequency": "21.300", "mode": "SSB"},
    {"frequency": "28.500", "mode": "SSB"},
]
DEFAULT_REPORT_PRESETS: list[str] = ["59/59", "59/57", "599/599"]
DEFAULT_TIME_DISPLAY = "both"
DEFAULT_QSL_SENT = "未設定"
DEFAULT_QSL_RECEIVED = "未設定"
DEFAULT_QSL_VIA = ""

_QSL_SENT_CHOICES = ("未設定", "未送信", "送信待ち", "送信済", "無効")
_QSL_RECEIVED_CHOICES = ("未設定", "未受領", "受領済", "請求中", "無効")


def normalize_qsl_sent_label(value: str | None) -> str:
    text = (value or "").strip()
    return text if text in _QSL_SENT_CHOICES else DEFAULT_QSL_SENT


def normalize_qsl_received_label(value: str | None) -> str:
    text = (value or "").strip()
    return text if text in _QSL_RECEIVED_CHOICES else DEFAULT_QSL_RECEIVED


@dataclass
class AppSettings:
    model: str = DEFAULT_MODEL
    language: str = DEFAULT_LANGUAGE
    beam_size: int = DEFAULT_BEAM_SIZE
    threshold: float = DEFAULT_THRESHOLD
    silence_duration: float = DEFAULT_SILENCE_DURATION
    device_index: int | None = None
    device_name: str = ""
    window_width: int | None = None
    window_height: int | None = None
    window_x: int | None = None
    window_y: int | None = None
    last_frequency: str = ""
    last_mode: str = DEFAULT_MODE
    freq_mode_presets: list[dict[str, str]] = field(
        default_factory=lambda: [dict(item) for item in DEFAULT_FREQ_MODE_PRESETS]
    )
    report_presets: list[str] = field(
        default_factory=lambda: list(DEFAULT_REPORT_PRESETS)
    )
    time_display: str = DEFAULT_TIME_DISPLAY
    default_qsl_sent: str = DEFAULT_QSL_SENT
    default_qsl_received: str = DEFAULT_QSL_RECEIVED
    default_qsl_via: str = DEFAULT_QSL_VIA
    popup_enabled: bool = True
    popup_width: int | None = None
    popup_height: int | None = None
    popup_x: int | None = None
    popup_y: int | None = None


def settings_path() -> Path:
    return Path.home() / ".ham_voice_logger" / "settings.json"


def _as_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().casefold()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def clamp_window(
    width: int | None,
    height: int | None,
    x: int | None,
    y: int | None,
    screen_width: int,
    screen_height: int,
    min_width: int = 900,
    min_height: int = 640,
) -> tuple[int, int, int | None, int | None]:
    """Return a geometry that fits the current screen."""
    max_w = max(min_width, screen_width - 40)
    max_h = max(min_height, screen_height - 80)
    if width is None or width < min_width:
        width = min(1024, max_w)
    else:
        width = min(max(width, min_width), max_w)
    if height is None or height < min_height:
        height = min(900, max_h)
    else:
        height = min(max(height, min_height), max_h)

    if x is None or y is None:
        return width, height, None, None

    # Keep enough of the window on-screen to drag it back.
    x = min(max(x, -width + 80), max(0, screen_width - 80))
    y = min(max(y, 0), max(0, screen_height - 80))
    return width, height, x, y


def format_freq_mode_preset(preset: dict[str, str]) -> str:
    frequency = str(preset.get("frequency") or "").strip()
    mode = str(preset.get("mode") or DEFAULT_MODE).strip() or DEFAULT_MODE
    return f"{frequency} · {mode}"


def normalize_freq_mode_presets(raw: Any) -> list[dict[str, str]]:
    """Normalize frequency/mode presets from settings JSON."""
    if raw is None:
        return [dict(item) for item in DEFAULT_FREQ_MODE_PRESETS]
    if not isinstance(raw, list):
        return [dict(item) for item in DEFAULT_FREQ_MODE_PRESETS]

    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        frequency = ""
        mode = DEFAULT_MODE
        if isinstance(item, dict):
            frequency = str(item.get("frequency") or "").strip()
            mode = str(item.get("mode") or DEFAULT_MODE).strip() or DEFAULT_MODE
        elif isinstance(item, str):
            text = item.strip()
            if "·" in text:
                frequency, _, mode = text.partition("·")
            elif "/" in text:
                frequency, _, mode = text.partition("/")
            else:
                parts = text.split()
                if len(parts) >= 2:
                    frequency, mode = parts[0], parts[1]
                else:
                    frequency = text
            frequency = frequency.strip()
            mode = mode.strip() or DEFAULT_MODE
        if not frequency:
            continue
        key = (frequency, mode)
        if key in seen:
            continue
        seen.add(key)
        result.append({"frequency": frequency, "mode": mode})
        if len(result) >= 40:
            break
    return result or [dict(item) for item in DEFAULT_FREQ_MODE_PRESETS]


def normalize_report_presets(raw: Any) -> list[str]:
    """Normalize report presets like ``59/59``."""
    if raw is None:
        return list(DEFAULT_REPORT_PRESETS)
    if not isinstance(raw, list):
        return list(DEFAULT_REPORT_PRESETS)

    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        if parse_report_preset(text) is None:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= 20:
            break
    return result or list(DEFAULT_REPORT_PRESETS)


def _is_report_token(value: str) -> bool:
    text = value.strip()
    if not text or len(text) > 5:
        return False
    if not any(ch.isdigit() for ch in text):
        return False
    return all(ch.isalnum() for ch in text)


def parse_report_preset(value: str) -> tuple[str, str] | None:
    """Return (received, sent) from ``59/59`` or a single ``59``."""
    text = str(value or "").strip()
    if not text:
        return None
    if "/" in text:
        left, _, right = text.partition("/")
        received = left.strip()
        sent = right.strip()
        if _is_report_token(received) and _is_report_token(sent):
            return received, sent
        return None
    if _is_report_token(text):
        return text, text
    return None


def load_settings(path: Path | None = None) -> AppSettings:
    """Load settings from disk, or defaults when missing/invalid."""
    target = path or settings_path()
    if not target.is_file():
        return AppSettings()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return AppSettings()
    if not isinstance(raw, dict):
        return AppSettings()

    beam = _as_int(raw.get("beam_size"), DEFAULT_BEAM_SIZE) or DEFAULT_BEAM_SIZE
    beam = max(1, min(beam, 10))
    threshold = max(0.0, _as_float(raw.get("threshold"), DEFAULT_THRESHOLD))
    silence = _as_float(raw.get("silence_duration"), DEFAULT_SILENCE_DURATION)
    if silence <= 0:
        silence = DEFAULT_SILENCE_DURATION

    model = str(raw.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    language = str(raw.get("language") or DEFAULT_LANGUAGE).strip() or DEFAULT_LANGUAGE
    device_name = str(raw.get("device_name") or "").strip()
    device_index = raw.get("device_index")
    if device_index is not None:
        device_index = _as_int(device_index, None)
    last_mode = str(raw.get("last_mode") or DEFAULT_MODE).strip() or DEFAULT_MODE

    return AppSettings(
        model=model,
        language=language,
        beam_size=beam,
        threshold=threshold,
        silence_duration=silence,
        device_index=device_index,
        device_name=device_name,
        window_width=_as_int(raw.get("window_width")),
        window_height=_as_int(raw.get("window_height")),
        window_x=_as_int(raw.get("window_x")),
        window_y=_as_int(raw.get("window_y")),
        last_frequency=str(raw.get("last_frequency") or "").strip(),
        last_mode=last_mode,
        freq_mode_presets=normalize_freq_mode_presets(raw.get("freq_mode_presets")),
        report_presets=normalize_report_presets(raw.get("report_presets")),
        time_display=normalize_time_display(str(raw.get("time_display") or "")),
        default_qsl_sent=normalize_qsl_sent_label(
            str(raw.get("default_qsl_sent") or "")
        ),
        default_qsl_received=normalize_qsl_received_label(
            str(raw.get("default_qsl_received") or "")
        ),
        default_qsl_via=str(raw.get("default_qsl_via") or "").strip(),
        popup_enabled=_as_bool(raw.get("popup_enabled"), True),
        popup_width=_as_int(raw.get("popup_width")),
        popup_height=_as_int(raw.get("popup_height")),
        popup_x=_as_int(raw.get("popup_x")),
        popup_y=_as_int(raw.get("popup_y")),
    )


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    """Write settings atomically to disk."""
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(settings)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(target)


def format_geometry(
    width: int,
    height: int,
    x: int | None = None,
    y: int | None = None,
) -> str:
    if x is None or y is None:
        return f"{width}x{height}"
    x_sign = "+" if x >= 0 else ""
    y_sign = "+" if y >= 0 else ""
    return f"{width}x{height}{x_sign}{x}{y_sign}{y}"


def parse_geometry(
    value: str,
) -> tuple[int, int, int | None, int | None] | None:
    """Parse a Tk ``wm_geometry`` string like ``1100x800+40+60``."""
    match = _GEOMETRY_RE.match(value.strip())
    if match is None:
        return None
    width = int(match.group("width"))
    height = int(match.group("height"))
    x_raw = match.group("x")
    y_raw = match.group("y")
    x = int(x_raw) if x_raw is not None else None
    y = int(y_raw) if y_raw is not None else None
    return width, height, x, y


def match_device_label(
    device_map: dict[str, int | None],
    device_index: int | None,
    device_name: str,
) -> str:
    """Pick a combobox label from saved device index/name."""
    if "システム既定" not in device_map:
        device_map = {"システム既定": None, **device_map}

    if device_name:
        for label, index in device_map.items():
            if label == "システム既定":
                continue
            # Labels look like "2: TakahisaのAirPods Pro"
            name_part = label.split(": ", 1)[-1]
            if device_name == name_part or device_name in name_part:
                return label

    if device_index is not None:
        prefix = f"{device_index}:"
        for label in device_map:
            if label.startswith(prefix):
                return label

    return "システム既定"
