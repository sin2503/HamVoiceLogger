"""Cross-platform desktop interface for recognition and QSO logging."""

from __future__ import annotations

import atexit
import math
import queue
import sys
import threading
import tkinter as tk
import unicodedata
from datetime import date, datetime, time as datetime_time
from tkinter import filedialog, messagebox, ttk
from typing import Any

from tkcalendar import Calendar

from audio_monitor import (
    AudioMonitor,
    format_audio_error,
    list_input_devices,
    refresh_audio_host,
    resolve_input_device,
)
from adif_export import export_adif, export_adx
from adif_import import import_adif
from hamlog_csv_export import export_hamlog_csv
from hamlog_csv_import import import_hamlog_csv
from jcc_jcg import lookup_station_code, normalize_station_code, search_station_codes
from time_format import (
    TIME_DISPLAY_MODES,
    format_dual_hint,
    format_timestamp,
    is_japan_entity,
    normalize_time_display,
)
from app_settings import (
    AppSettings,
    clamp_window,
    format_freq_mode_preset,
    format_geometry,
    load_settings,
    match_device_label,
    normalize_freq_mode_presets,
    normalize_qsl_received_label,
    normalize_qsl_sent_label,
    normalize_report_presets,
    parse_geometry,
    parse_report_preset,
    save_settings,
)
from callbook import (
    CallbookError,
    HamQTHClient,
    load_hamqth_credentials,
    lookup_public_dxcc,
    save_hamqth_credentials,
)
from callsign_extractor import extract_callsigns, partially_decode_phonetics
from decoder import (
    ModelManagerError,
    SpeechDecoder,
    download_model,
    ensure_engine_runtime,
    format_byte_size,
    format_model_choice,
    get_model_spec,
    is_model_downloaded,
    list_downloaded_models,
    list_model_statuses,
    normalize_model_key,
    parse_model_choice,
)
from log_store import LogStore

_QSL_SENT_CODES = {
    "未設定": "",
    "未送信": "N",
    "送信待ち": "Q",
    "送信済": "Y",
    "無効": "I",
}
_QSL_RECEIVED_CODES = {
    "未設定": "",
    "未受領": "N",
    "受領済": "Y",
    "請求中": "R",
    "無効": "I",
}
_QSL_SENT_LABELS = {value: label for label, value in _QSL_SENT_CODES.items()}
_QSL_RECEIVED_LABELS = {value: label for label, value in _QSL_RECEIVED_CODES.items()}


def _is_valid_rs(text: str) -> bool:
    """Phone RS: R=1..5, S=1..9 → 11..19, 21..29, …, 51..59 (not 60)."""
    return len(text) == 2 and text[0] in "12345" and text[1] in "123456789"


def _is_valid_rst(text: str) -> bool:
    """CW RST: R=1..5, S=1..9, T=1..9 → 111..599 with those digit rules."""
    return (
        len(text) == 3
        and text[0] in "12345"
        and text[1] in "123456789"
        and text[2] in "123456789"
    )


def _step_rs(text: str, delta: int) -> str:
    readability = int(text[0])
    strength = int(text[1])
    strength += 1 if delta > 0 else -1
    if strength > 9:
        if readability >= 5:
            return "59"
        readability += 1
        strength = 1
    elif strength < 1:
        if readability <= 1:
            return "11"
        readability -= 1
        strength = 9
    return f"{readability}{strength}"


def _step_rst(text: str, delta: int) -> str:
    readability = int(text[0])
    strength = int(text[1])
    tone = int(text[2])
    tone += 1 if delta > 0 else -1
    if tone > 9:
        tone = 1
        strength += 1
        if strength > 9:
            strength = 1
            if readability >= 5:
                return "599"
            readability += 1
    elif tone < 1:
        tone = 9
        strength -= 1
        if strength < 1:
            strength = 9
            if readability <= 1:
                return "111"
            readability -= 1
    return f"{readability}{strength}{tone}"


def nudge_report_value(value: str, delta: int) -> str:
    """Raise/lower RS (11-59) or RST (111-599) by one valid step.

    60 is not a valid phone report (S max is 9). Non-numeric text is left as-is.
    """
    text = unicodedata.normalize("NFKC", value or "").strip()
    if not text:
        return "59"
    if not text.isdigit():
        return text
    if _is_valid_rs(text):
        return _step_rs(text, delta)
    if _is_valid_rst(text):
        return _step_rst(text, delta)
    # Invalid numeric (e.g. 60, 00): snap toward a usable report.
    if len(text) >= 3:
        return "599" if delta > 0 else "111"
    return "59" if delta > 0 else "11"


_HELP_THRESHOLD = (
    "音量しきい値（RMS）\n"
    "マイク音量がこの値を超えると「発話開始」とみなします。\n"
    "小さいほど敏感で雑音も拾いやすく、大きいほど大きな音だけを切り出します。\n"
    "目安: 0.02 前後"
)
_HELP_SILENCE = (
    "無音終了（秒）\n"
    "発話開始後、音量がしきい値未満のままこの秒数続くと発話終了として切り出し、\n"
    "文字起こしに回します。\n"
    "短いと一文が途切れやすく、長いと反応が遅くなります。\n"
    "目安: 1.0 秒"
)
_HELP_BEAM = (
    "ビームサイズ\n"
    "faster-whisper / whisper.cpp が文字起こし時に同時に探る候補の幅です。\n"
    "大きいほど精度が上がりやすい一方、処理は遅くなります。\n"
    "Moonshine では使いません。目安: 5"
)
_HELP_MODEL = (
    "認識モデル\n"
    "取得済みモデルのみ選択できます（faster-whisper / whisper.cpp / Moonshine）。\n"
    "未取得のモデルは「設定」→「認識モデル」からダウンロードしてください。\n"
    "whisper.cpp / Moonshine は初回取得時に必要なパッケージも自動で入れます。\n"
    "大きいモデルほど精度は上がりやすく、速度は下がります。"
)


class HoverTip:
    """Show a help balloon as an overlay on the main window.

    A separate Toplevel is avoided because macOS dark mode often remaps
    colors there and makes the tip text unreadable.
    """

    _active: "HoverTip | None" = None

    def __init__(self, widget: tk.Misc, text: str, delay_ms: int = 350) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id: str | None = None
        self._tip: tk.Frame | None = None
        self._pinned = False
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<Button-1>", self._toggle_pin, add="+")

    def _host(self) -> tk.Misc:
        return self.widget.winfo_toplevel()

    def _schedule(self, _event: tk.Event[Any] | None = None) -> None:
        if self._pinned:
            return
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self) -> None:
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _on_leave(self, _event: tk.Event[Any] | None = None) -> None:
        if self._pinned:
            return
        self._hide()

    def _toggle_pin(self, _event: tk.Event[Any] | None = None) -> str:
        if self._pinned and self._tip is not None:
            self._pinned = False
            self._hide()
            return "break"
        self._pinned = True
        self._show()
        return "break"

    def _hide(self, _event: tk.Event[Any] | None = None) -> None:
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None
        if HoverTip._active is self:
            HoverTip._active = None
        self._pinned = False

    def _show(self) -> None:
        if not self.widget.winfo_exists():
            return
        if HoverTip._active is not None and HoverTip._active is not self:
            HoverTip._active._hide()
        if self._tip is not None:
            return

        host = self._host()
        host.update_idletasks()
        x = self.widget.winfo_rootx() - host.winfo_rootx() + 8
        y = (
            self.widget.winfo_rooty()
            - host.winfo_rooty()
            + self.widget.winfo_height()
            + 4
        )

        tip = tk.Frame(
            host,
            background="#fff8e1",
            highlightbackground="#146b58",
            highlightthickness=1,
            bd=0,
            padx=10,
            pady=8,
        )
        title, _, body = self.text.partition("\n")
        tk.Label(
            tip,
            text=title,
            justify="left",
            background="#fff8e1",
            foreground="#174b3e",
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor="w")
        if body:
            tk.Label(
                tip,
                text=body.lstrip("\n"),
                justify="left",
                background="#fff8e1",
                foreground="#20332d",
                font=("TkDefaultFont", 10),
                wraplength=340,
            ).pack(anchor="w", pady=(4, 0))
        tip.place(x=max(8, x), y=max(8, y))
        tip.lift()
        self._tip = tip
        HoverTip._active = self


class CandidatePopup:
    """Small always-on-top window for live transcript and callsign picks."""

    def __init__(self, app: "VoiceLoggerApp") -> None:
        self.app = app
        self.window: tk.Toplevel | None = None
        self._transcript_var = tk.StringVar(value="認識結果を待っています")
        self._status_var = tk.StringVar(value="")
        self._button_frame: ttk.Frame | None = None
        self._buttons: list[ttk.Button] = []
        self._closing = False

    @property
    def visible(self) -> bool:
        return (
            self.window is not None
            and bool(self.window.winfo_exists())
            and self.window.state() == "normal"
        )

    def show(self) -> None:
        if self.window is not None and self.window.winfo_exists():
            try:
                self.window.deiconify()
                self.window.lift()
                self.window.attributes("-topmost", True)
            except tk.TclError:
                self.window = None
            else:
                return
        self._build()

    def hide(self, *, destroy: bool = False) -> None:
        if self.window is None or not self.window.winfo_exists():
            self.window = None
            return
        self._capture_geometry()
        if destroy:
            self._closing = True
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None
            self._button_frame = None
            self._buttons.clear()
            self._closing = False
            return
        try:
            self.window.withdraw()
        except tk.TclError:
            self.window = None

    def clear(self) -> None:
        self._transcript_var.set("認識結果を待っています")
        self._status_var.set("")
        for button in self._buttons:
            button.destroy()
        self._buttons.clear()

    def present_result(
        self,
        text: str,
        callsigns: list[str],
        partial: str,
    ) -> None:
        if not self.app.settings.popup_enabled:
            return
        self.show()
        snippet = text.strip().replace("\n", " ")
        if len(snippet) > 160:
            snippet = snippet[:157] + "…"
        self._transcript_var.set(snippet or "（空の認識結果）")
        for button in self._buttons:
            button.destroy()
        self._buttons.clear()
        if not self._button_frame:
            return
        if callsigns:
            self._status_var.set("クリックでコールサインを確定")
            for callsign in callsigns[:8]:
                button = ttk.Button(
                    self._button_frame,
                    text=callsign,
                    style="Accent.TButton",
                )
                button.configure(
                    command=lambda call=callsign, transcript=text, item=button: (
                        self.app._confirm_candidate(call, transcript, item)
                    ),
                )
                button.pack(fill="x", pady=2)
                self._buttons.append(button)
        elif partial:
            self._status_var.set(f"一部解読: {partial}")
        else:
            self._status_var.set("コールサイン候補なし")

    def mark_saved(self, callsign: str) -> None:
        for button in self._buttons:
            if button.cget("text") == callsign or button.cget("text").startswith(
                f"{callsign} "
            ):
                button.configure(text=f"{callsign}  ·  保存済み", state="disabled")

    def _build(self) -> None:
        win = tk.Toplevel(self.app.root)
        win.title("候補ポップアップ")
        win.configure(background="#edf2ef")
        try:
            win.attributes("-topmost", True)
        except tk.TclError:
            pass
        win.minsize(260, 180)
        win.protocol("WM_DELETE_WINDOW", self._on_user_close)
        win.bind("<Configure>", self._on_configure, add="+")

        outer = ttk.Frame(win, padding=10)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="最新の文字起こし", style="Muted.TLabel").pack(anchor="w")
        transcript = ttk.Label(
            outer,
            textvariable=self._transcript_var,
            wraplength=280,
            justify="left",
        )
        transcript.pack(anchor="w", fill="x", pady=(2, 8))
        ttk.Label(outer, text="コールサイン候補", style="Muted.TLabel").pack(anchor="w")
        self._button_frame = ttk.Frame(outer)
        self._button_frame.pack(fill="both", expand=True, pady=(4, 4))
        ttk.Label(
            outer,
            textvariable=self._status_var,
            style="Muted.TLabel",
            wraplength=280,
        ).pack(anchor="w")
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(
            actions,
            text="メインへ",
            command=self._focus_main,
        ).pack(side="left")
        ttk.Button(
            actions,
            text="閉じる",
            command=self._on_user_close,
        ).pack(side="right")

        self.window = win
        self._apply_geometry()

    def _default_geometry(self) -> tuple[int, int, int, int]:
        screen_w = self.app.root.winfo_screenwidth()
        screen_h = self.app.root.winfo_screenheight()
        width = 320
        height = 260
        x = max(20, screen_w - width - 40)
        y = max(40, screen_h - height - 80)
        return width, height, x, y

    def _apply_geometry(self) -> None:
        if self.window is None:
            return
        settings = self.app.settings
        width, height, x, y = self._default_geometry()
        if settings.popup_width and settings.popup_height:
            width, height = settings.popup_width, settings.popup_height
        if settings.popup_x is not None and settings.popup_y is not None:
            x, y = settings.popup_x, settings.popup_y
        try:
            self.window.geometry(f"{width}x{height}+{x}+{y}")
        except tk.TclError:
            pass

    def _capture_geometry(self) -> None:
        if self.window is None or not self.window.winfo_exists():
            return
        try:
            geometry = self.window.wm_geometry()
        except tk.TclError:
            return
        parsed = parse_geometry(geometry)
        if parsed is None:
            return
        width, height, x, y = parsed
        self.app.settings.popup_width = width
        self.app.settings.popup_height = height
        self.app.settings.popup_x = x
        self.app.settings.popup_y = y

    def _on_configure(self, _event: tk.Event | None = None) -> None:
        if self._closing or self.window is None:
            return
        self._capture_geometry()

    def _on_user_close(self) -> None:
        self.app._set_popup_enabled(False)

    def _focus_main(self) -> None:
        try:
            self.app.root.deiconify()
            self.app.root.lift()
            self.app.root.focus_force()
            self.app.notebook.select(self.app.monitor_tab)
        except tk.TclError:
            pass
    def __init__(
        self,
        events: queue.Queue[tuple[str, Any]],
        stop_event: threading.Event,
        model_size: str,
        language: str,
        beam_size: int,
        threshold: float,
        silence_duration: float,
        device: int | None,
    ) -> None:
        super().__init__(daemon=True)
        self.events = events
        self.stop_event = stop_event
        self.model_size = model_size
        self.language = language
        self.beam_size = beam_size
        self.threshold = threshold
        self.silence_duration = silence_duration
        self.device = device

    def run(self) -> None:
        monitor: AudioMonitor | None = None
        try:
            self.events.put(
                ("status", f"モデルを準備中: {format_model_choice(self.model_size)}")
            )
            spec = get_model_spec(self.model_size)
            if spec is not None:

                def on_engine_progress(
                    _current: int, _total: int, description: str
                ) -> None:
                    if description:
                        self.events.put(("status", description))

                ensure_engine_runtime(
                    spec.engine, progress_callback=on_engine_progress
                )
            decoder = SpeechDecoder(
                model_size=self.model_size,
                language=self.language,
                beam_size=self.beam_size,
            )
            monitor = AudioMonitor(
                threshold=self.threshold,
                silence_duration=self.silence_duration,
                device=self.device,
            )
            monitor.start()
            self.events.put(("status", "認識中 · マイクを監視中"))
            while not self.stop_event.is_set():
                try:
                    audio = monitor.get_segment(timeout=0.25)
                except queue.Empty:
                    continue

                if self.stop_event.is_set():
                    break
                duration = audio.shape[0] / float(monitor.sample_rate)
                self.events.put(
                    ("status", f"認識中 · 文字起こし中（{duration:.1f}秒）")
                )
                text = decoder.transcribe(audio, sample_rate=monitor.sample_rate)
                if self.stop_event.is_set():
                    break
                if text:
                    self.events.put(
                        (
                            "transcript",
                            {
                                "time": datetime.now().astimezone().strftime("%H:%M:%S"),
                                "text": text,
                                "callsigns": extract_callsigns(text),
                                "partial": partially_decode_phonetics(text),
                            },
                        )
                    )
                self.events.put(("status", "認識中 · マイクを監視中"))
        except Exception as exc:
            self.events.put(("error", format_audio_error(exc)))
        finally:
            if monitor is not None:
                try:
                    monitor.stop()
                except Exception:
                    pass
            self.events.put(("stopped", None))


class ClockTimePicker(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        initial_time: tuple[int, int, int],
        on_confirm: Any,
    ) -> None:
        super().__init__(parent)
        self.title("開始時刻を選択")
        self.transient(parent)
        self.resizable(False, False)
        self.on_confirm = on_confirm
        self.values = {
            "hour": initial_time[0],
            "minute": initial_time[1],
            "second": initial_time[2],
        }
        self.active_field = "hour"
        self.center = 150
        self.radius = 112

        display = ttk.Frame(self, padding=(16, 12, 16, 4))
        display.pack(fill="x")
        self.field_buttons: dict[str, ttk.Button] = {}
        for index, (field, label) in enumerate(
            (("hour", "時"), ("minute", "分"), ("second", "秒"))
        ):
            button = ttk.Button(
                display,
                text=self._format_value(field),
                command=lambda selected=field: self._set_active_field(selected),
            )
            button.pack(side="left", expand=True, fill="x")
            self.field_buttons[field] = button
            ttk.Label(display, text=label).pack(side="left", padx=(2, 8))
            if index < 2:
                ttk.Label(display, text=":", style="Section.TLabel").pack(side="left")

        self.canvas = tk.Canvas(
            self,
            width=300,
            height=300,
            background="#f7faf8",
            highlightthickness=0,
        )
        self.canvas.pack(padx=12, pady=8)
        self.canvas.bind("<Button-1>", self._on_dial_click)

        actions = ttk.Frame(self, padding=(12, 0, 12, 12))
        actions.pack(fill="x")
        ttk.Button(actions, text="キャンセル", command=self.destroy).pack(side="right")
        ttk.Button(
            actions,
            text="時刻を選択",
            style="Accent.TButton",
            command=self._confirm,
        ).pack(side="right", padx=(0, 8))

        self._refresh_display()
        self._draw_dial()
        self.grab_set()

    def _format_value(self, field: str) -> str:
        return f"{self.values[field]:02d}"

    def _set_active_field(self, field: str) -> None:
        self.active_field = field
        self._refresh_display()
        self._draw_dial()

    def _refresh_display(self) -> None:
        for field, button in self.field_buttons.items():
            button.configure(
                text=self._format_value(field),
                style="Accent.TButton" if field == self.active_field else "TButton",
            )

    def _draw_dial(self) -> None:
        self.canvas.delete("all")
        center = self.center
        radius = self.radius
        self.canvas.create_oval(
            center - radius,
            center - radius,
            center + radius,
            center + radius,
            fill="#e7efeb",
            outline="#aebdb6",
        )

        count = 24 if self.active_field == "hour" else 60
        label_step = 1 if self.active_field == "hour" else 5
        selected = self.values[self.active_field]
        for value in range(count):
            angle = math.radians(value * 360 / count - 90)
            inner_radius = radius - 9 if value % label_step == 0 else radius - 4
            x1 = center + math.cos(angle) * inner_radius
            y1 = center + math.sin(angle) * inner_radius
            x2 = center + math.cos(angle) * radius
            y2 = center + math.sin(angle) * radius
            self.canvas.create_line(
                x1,
                y1,
                x2,
                y2,
                fill="#688078" if value % label_step == 0 else "#b9c7c1",
                width=2 if value % label_step == 0 else 1,
            )
            if value % label_step == 0:
                label_radius = radius - 25
                label_x = center + math.cos(angle) * label_radius
                label_y = center + math.sin(angle) * label_radius
                if value == selected:
                    self.canvas.create_oval(
                        label_x - 14,
                        label_y - 14,
                        label_x + 14,
                        label_y + 14,
                        fill="#146b58",
                        outline="",
                    )
                self.canvas.create_text(
                    label_x,
                    label_y,
                    text=f"{value:02d}",
                    fill="#ffffff" if value == selected else "#20332d",
                    font=("TkDefaultFont", 9, "bold" if value == selected else "normal"),
                )

        angle = math.radians(selected * 360 / count - 90)
        hand_x = center + math.cos(angle) * (radius - 44)
        hand_y = center + math.sin(angle) * (radius - 44)
        self.canvas.create_line(center, center, hand_x, hand_y, fill="#146b58", width=2)
        self.canvas.create_oval(
            center - 5, center - 5, center + 5, center + 5, fill="#146b58", outline=""
        )

    def _on_dial_click(self, event: tk.Event[Any]) -> None:
        dx = event.x - self.center
        dy = event.y - self.center
        if math.hypot(dx, dy) < 28:
            return
        angle = (math.degrees(math.atan2(dy, dx)) + 90) % 360
        count = 24 if self.active_field == "hour" else 60
        value = round(angle * count / 360) % count
        self._select_dial_value(value)

    def _select_dial_value(self, value: int) -> None:
        field_order = ("hour", "minute", "second")
        self.values[self.active_field] = value
        index = field_order.index(self.active_field)
        if index < len(field_order) - 1:
            self.active_field = field_order[index + 1]
        self._refresh_display()
        self._draw_dial()

    def _confirm(self) -> None:
        self.on_confirm(self.values["hour"], self.values["minute"], self.values["second"])
        self.destroy()


class VoiceLoggerApp:
    def __init__(
        self,
        root: tk.Tk,
        model_size: str | None = None,
        language: str | None = None,
        beam_size: int | None = None,
    ) -> None:
        self.root = root
        self.settings = load_settings()
        preferred_model = model_size or self.settings.model or "faster-whisper:small"
        self.model_size = normalize_model_key(preferred_model)
        self.language = (language or self.settings.language or "ja").strip() or "ja"
        self.beam_size = (
            beam_size if beam_size is not None else self.settings.beam_size
        )
        self.store = LogStore()
        self.active_qso_id: int | None = None
        self.editing_qso_id: int | None = None
        self._editing_ended_at: str | None = None
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.stop_event: threading.Event | None = None
        self.worker: RecognitionWorker | None = None
        self.callbook_data: dict[str, str] = {}
        self.hamqth_client: HamQTHClient | None = None
        self.callbook_lookup_thread: threading.Thread | None = None
        self.close_requested = False
        self._history_after_id: str | None = None
        self._candidate_buttons: list[ttk.Widget] = []
        self.candidate_popup = CandidatePopup(self)
        self._device_map: dict[str, int | None] = {"システム既定": None}
        self._model_download_thread: threading.Thread | None = None
        self._model_download_name: str | None = None
        self._model_download_progress: queue.Queue[tuple[int, int, str]] = queue.Queue()
        self._model_download_results: queue.Queue[tuple[str, str | None]] = queue.Queue()
        self._model_download_last: tuple[int, int, str] | None = None
        self._settings_window: tk.Toplevel | None = None
        self._model_settings_ui: dict[str, Any] | None = None
        self._poll_busy = False
        self._pending_error_dialog: str | None = None
        self._persist_after_id: str | None = None
        self._settings_saved = False
        # Avoid saving the pre-map minsize over the restored geometry.
        self._geometry_persist_enabled = False

        self._configure_window()
        self._build_ui()
        self._load_audio_devices(refresh_host=False)
        self._apply_saved_device()
        self._refresh_history()
        self._refresh_candidate_history()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        try:
            # macOS Cmd+Q / Dock Quit bypasses WM_DELETE_WINDOW otherwise.
            self.root.createcommand("tk::mac::Quit", self._on_close)
        except tk.TclError:
            pass
        self.root.bind("<Configure>", self._on_root_configure, add="+")
        atexit.register(self._persist_settings_atexit)
        # geometry() before the window is mapped is unreliable on macOS.
        self.root.after_idle(self._restore_window_geometry)
        self.root.after(100, self._poll_events)

    def _configure_window(self) -> None:
        self.root.title("HAM Voice Logger")
        self.root.minsize(900, 640)
        width, height = self._default_window_size()
        self.root.geometry(format_geometry(width, height))
        self.root.configure(background="#edf2ef")

        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background="#edf2ef")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("TLabel", background="#edf2ef", foreground="#20332d")
        style.configure("Muted.TLabel", background="#edf2ef", foreground="#61716b")
        style.configure(
            "Title.TLabel",
            background="#edf2ef",
            font=("TkDefaultFont", 19, "bold"),
            foreground="#174b3e",
        )
        style.configure(
            "Section.TLabel",
            background="#edf2ef",
            font=("TkDefaultFont", 11, "bold"),
            foreground="#174b3e",
        )
        style.configure(
            "Accent.TButton",
            background="#146b58",
            foreground="#ffffff",
            padding=(12, 8),
        )
        style.map(
            "Accent.TButton",
            background=[("active", "#0f5849"), ("disabled", "#9aa9a3")],
        )
        style.configure(
            "Stop.TButton",
            background="#b44d3b",
            foreground="#ffffff",
            padding=(12, 8),
        )
        style.map("Stop.TButton", background=[("active", "#963d30")])
        style.configure(
            "Treeview",
            rowheight=27,
            background="#ffffff",
            fieldbackground="#ffffff",
        )
        style.configure("Treeview.Heading", font=("TkDefaultFont", 9, "bold"))
        style.configure("TNotebook", background="#edf2ef", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(12, 8))
        style.configure(
            "Help.TLabel",
            background="#edf2ef",
            foreground="#146b58",
            font=("TkDefaultFont", 10, "bold"),
        )

    def _default_window_size(self) -> tuple[int, int]:
        """Choose a startup size that fits common screens and ultrawides.

        Width stays near the tuned layout (~1024). Height scales with the
        display so a 3440x1440 ultrawide gets more vertical room while a
        1080p / laptop screen stays usable.
        """
        screen_w = max(self.root.winfo_screenwidth(), 1024)
        screen_h = max(self.root.winfo_screenheight(), 720)
        width = min(1024, max(900, screen_w - 80))
        if screen_h >= 1400:
            height = min(1200, int(screen_h * 0.82))
        elif screen_h >= 1000:
            height = min(1000, int(screen_h * 0.85))
        else:
            height = max(640, int(screen_h * 0.90))
        return width, height

    def _restore_window_geometry(self) -> None:
        """Apply saved geometry after the window has been created/mapped."""
        screen_w = max(self.root.winfo_screenwidth(), 1024)
        screen_h = max(self.root.winfo_screenheight(), 720)
        saved = self.settings
        if saved.window_width and saved.window_height:
            width, height, x, y = clamp_window(
                saved.window_width,
                saved.window_height,
                saved.window_x,
                saved.window_y,
                screen_w,
                screen_h,
            )
            self.root.geometry(format_geometry(width, height, x, y))
        # Wait for layout/Configure noise to settle before persisting size.
        self.root.after(750, self._enable_geometry_persist)

    def _enable_geometry_persist(self) -> None:
        self._geometry_persist_enabled = True

    def _read_window_geometry(self) -> tuple[int, int, int | None, int | None] | None:
        """Return outer window geometry from Tk (more reliable than winfo_*)."""
        try:
            if not self.root.winfo_exists():
                return None
            parsed = parse_geometry(self.root.wm_geometry())
        except tk.TclError:
            return None
        if parsed is None:
            return None
        width, height, x, y = parsed
        if width < 200 or height < 200:
            return None
        return width, height, x, y

    def _apply_saved_device(self) -> None:
        if not hasattr(self, "device_combo"):
            return
        label = match_device_label(
            self._device_map,
            self.settings.device_index,
            self.settings.device_name,
        )
        if label in self._device_map:
            self.device_var.set(label)
            self.device_combo.set(label)

    def _add_help_marker(
        self,
        parent: ttk.Frame,
        label_text: str,
        help_text: str,
        **grid_kwargs: Any,
    ) -> ttk.Frame:
        frame = ttk.Frame(parent)
        frame.grid(**grid_kwargs)
        ttk.Label(frame, text=label_text).pack(side="left")
        marker = ttk.Label(frame, text=" ?", style="Help.TLabel", cursor="question_arrow")
        marker.pack(side="left")
        HoverTip(marker, help_text)
        return frame

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        header = ttk.Frame(self.root, padding=(20, 16, 20, 10))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="HAM VOICE LOGGER", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.status_var = tk.StringVar(value="停止中")
        ttk.Label(header, textvariable=self.status_var, style="Muted.TLabel").grid(
            row=0, column=1, padx=(18, 0), sticky="w"
        )
        self.popup_enabled_var = tk.BooleanVar(value=bool(self.settings.popup_enabled))
        self.popup_toggle = ttk.Checkbutton(
            header,
            text="候補ポップアップ",
            variable=self.popup_enabled_var,
            command=self._on_popup_toggle,
        )
        self.popup_toggle.grid(row=0, column=2, padx=(8, 0), sticky="e")
        self.settings_button = ttk.Button(
            header,
            text="設定",
            command=self._open_settings,
        )
        self.settings_button.grid(row=0, column=3, padx=(8, 8), sticky="e")
        self.recognition_button = ttk.Button(
            header,
            text="認識開始",
            style="Accent.TButton",
            command=self._toggle_recognition,
        )
        self.recognition_button.grid(row=0, column=4, sticky="e")

        settings = ttk.Frame(self.root, padding=(20, 0, 20, 12))
        settings.grid(row=1, column=0, sticky="ew")
        # Keep trailing space flexible so comboboxes do not stretch with the window.
        settings.columnconfigure(6, weight=1)

        ttk.Label(settings, text="入力デバイス").grid(row=0, column=0, padx=(0, 8), sticky="w")
        self.device_var = tk.StringVar(value="システム既定")
        self.device_combo = ttk.Combobox(
            settings, textvariable=self.device_var, state="readonly", width=36
        )
        self.device_combo.configure(
            postcommand=lambda: self._load_audio_devices(refresh_host=True)
        )
        self.device_combo.grid(row=0, column=1, sticky="w")
        self._add_help_marker(
            settings,
            "音量しきい値",
            _HELP_THRESHOLD,
            row=0,
            column=2,
            padx=(18, 6),
            sticky="w",
        )
        self.threshold_var = tk.StringVar(value=f"{self.settings.threshold:g}")
        self.threshold_spin = ttk.Spinbox(
            settings,
            from_=0.001,
            to=0.5,
            increment=0.005,
            textvariable=self.threshold_var,
            width=7,
        )
        self.threshold_spin.grid(row=0, column=3, sticky="w")
        self.threshold_var.trace_add("write", self._schedule_persist_settings)
        self._add_help_marker(
            settings,
            "無音終了 (秒)",
            _HELP_SILENCE,
            row=0,
            column=4,
            padx=(18, 6),
            sticky="w",
        )
        self.silence_var = tk.StringVar(value=f"{self.settings.silence_duration:g}")
        self.silence_spin = ttk.Spinbox(
            settings,
            from_=0.2,
            to=5.0,
            increment=0.1,
            textvariable=self.silence_var,
            width=7,
        )
        self.silence_spin.grid(row=0, column=5, sticky="w")
        self.silence_var.trace_add("write", self._schedule_persist_settings)

        self._add_help_marker(
            settings,
            "認識モデル",
            _HELP_MODEL,
            row=1,
            column=0,
            padx=(0, 8),
            pady=(10, 0),
            sticky="w",
        )
        self.model_var = tk.StringVar()
        self.model_combo = ttk.Combobox(
            settings,
            textvariable=self.model_var,
            state="readonly",
            width=28,
        )
        self.model_combo.grid(row=1, column=1, sticky="w", pady=(10, 0))
        self._add_help_marker(
            settings,
            "ビーム",
            _HELP_BEAM,
            row=1,
            column=2,
            padx=(18, 6),
            pady=(10, 0),
            sticky="w",
        )
        self.beam_var = tk.StringVar(value=str(self.beam_size))
        self.beam_spin = ttk.Spinbox(
            settings,
            from_=1,
            to=10,
            increment=1,
            textvariable=self.beam_var,
            width=7,
        )
        self.beam_spin.grid(row=1, column=3, sticky="w", pady=(10, 0))
        self._refresh_model_combo(preferred=self.model_size)
        self.device_var.trace_add("write", self._schedule_persist_settings)
        self.model_var.trace_add("write", self._schedule_persist_settings)
        self.beam_var.trace_add("write", self._schedule_persist_settings)

        self._capture_setting_widgets = (
            self.device_combo,
            self.threshold_spin,
            self.silence_spin,
            self.model_combo,
            self.beam_spin,
        )

        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 18))
        self.monitor_tab = ttk.Frame(self.notebook, padding=14)
        self.history_tab = ttk.Frame(self.notebook, padding=14)
        self.candidates_tab = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(self.monitor_tab, text="認識・QSO")
        self.notebook.add(self.history_tab, text="QSO履歴")
        self.notebook.add(self.candidates_tab, text="確認済み候補")
        self._build_monitor_tab()
        self._build_history_tab()
        self._build_candidates_tab()

    def _build_monitor_tab(self) -> None:
        tab = self.monitor_tab
        tab.columnconfigure(0, weight=3)
        tab.columnconfigure(1, weight=2)
        tab.rowconfigure(0, weight=3)
        tab.rowconfigure(1, weight=2)

        transcript_panel = ttk.Frame(tab, style="Panel.TFrame", padding=14)
        transcript_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        transcript_panel.columnconfigure(0, weight=1)
        transcript_panel.rowconfigure(1, weight=1)
        ttk.Label(transcript_panel, text="文字起こし", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        ttk.Button(
            transcript_panel,
            text="表示クリア",
            command=self._clear_live_results,
        ).grid(row=0, column=1, sticky="e", pady=(0, 8))
        self.transcript_text = tk.Text(
            transcript_panel,
            height=10,
            wrap="word",
            relief="flat",
            background="#f7faf8",
            foreground="#20332d",
            insertbackground="#20332d",
            padx=10,
            pady=8,
            cursor="xterm",
        )
        self.transcript_text.grid(row=1, column=0, sticky="nsew")
        transcript_scroll = ttk.Scrollbar(
            transcript_panel, orient="vertical", command=self.transcript_text.yview
        )
        transcript_scroll.grid(row=1, column=1, sticky="ns")
        self.transcript_text.configure(yscrollcommand=transcript_scroll.set)
        self._make_transcript_selectable()

        candidate_panel = ttk.Frame(tab, style="Panel.TFrame", padding=14)
        candidate_panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=(0, 8))
        candidate_panel.columnconfigure(0, weight=1)
        ttk.Label(
            candidate_panel, text="コールサイン候補", style="Section.TLabel"
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            candidate_panel,
            text="候補をクリックするとコールサイン欄へ入れ、確認済み履歴へ保存します。",
            style="Muted.TLabel",
            wraplength=340,
        ).grid(row=1, column=0, sticky="w", pady=(3, 8))
        self.candidate_list = ttk.Frame(candidate_panel, style="Panel.TFrame")
        self.candidate_list.grid(row=2, column=0, sticky="new")
        self.no_candidates_var = tk.StringVar(value="認識結果を待っています")
        self.empty_candidate_label = ttk.Label(
            self.candidate_list,
            textvariable=self.no_candidates_var,
            style="Muted.TLabel",
        )
        self.empty_candidate_label.grid(row=0, column=0, sticky="w", pady=6)

        qso_panel = ttk.Frame(tab, style="Panel.TFrame", padding=14)
        qso_panel.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        qso_panel.columnconfigure(1, weight=1)
        qso_panel.columnconfigure(3, weight=1)
        qso_panel.rowconfigure(9, weight=1)
        ttk.Label(qso_panel, text="QSOログ", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        self.qso_status_var = tk.StringVar(value="QSO未開始")
        ttk.Label(qso_panel, textvariable=self.qso_status_var, style="Muted.TLabel").grid(
            row=0, column=1, sticky="w", padx=12, pady=(0, 8)
        )
        self.qso_button = ttk.Button(
            qso_panel,
            text="QSO開始",
            style="Accent.TButton",
            command=self._toggle_qso,
        )
        self.qso_button.grid(row=0, column=4, sticky="e", pady=(0, 8))
        self.qso_edit_cancel_button = ttk.Button(
            qso_panel,
            text="編集取消",
            command=self._cancel_history_edit,
            state="disabled",
        )
        self.qso_edit_cancel_button.grid(
            row=0, column=5, sticky="e", padx=(8, 0), pady=(0, 8)
        )

        ttk.Label(qso_panel, text="コールサイン").grid(row=1, column=0, sticky="w", pady=4)
        self.qso_callsign_var = tk.StringVar()
        callsign_row = ttk.Frame(qso_panel)
        callsign_row.grid(row=1, column=1, sticky="ew", padx=(8, 18), pady=4)
        callsign_row.columnconfigure(0, weight=1)
        ttk.Entry(callsign_row, textvariable=self.qso_callsign_var, width=14).grid(
            row=0, column=0, sticky="ew"
        )
        self.dupe_apply_button = ttk.Button(
            callsign_row,
            text="前回値",
            width=6,
            command=self._choose_dupe_fields,
            state="disabled",
        )
        self.dupe_apply_button.grid(row=0, column=1, padx=(6, 0))
        self.qso_callsign_var.trace_add("write", self._on_qso_callsign_changed)
        self._dupe_after_id: str | None = None
        self._dupe_latest: dict[str, Any] | None = None
        self._dupe_recent: list[dict[str, Any]] = []
        self._dupe_total: int = 0
        self._dupe_picker: tk.Toplevel | None = None
        ttk.Label(qso_panel, text="周波数").grid(row=1, column=2, sticky="w", pady=4)
        self.frequency_var = tk.StringVar(value=self.settings.last_frequency)
        freq_row = ttk.Frame(qso_panel)
        freq_row.grid(row=1, column=3, sticky="ew", padx=(8, 18), pady=4)
        freq_row.columnconfigure(0, weight=1)
        ttk.Entry(freq_row, textvariable=self.frequency_var, width=12).grid(
            row=0, column=0, sticky="ew"
        )
        self.freq_mode_preset_var = tk.StringVar()
        self.freq_mode_preset_combo = ttk.Combobox(
            freq_row,
            textvariable=self.freq_mode_preset_var,
            state="readonly",
            width=14,
        )
        self.freq_mode_preset_combo.grid(row=0, column=1, padx=(6, 0))
        self.freq_mode_preset_combo.bind(
            "<<ComboboxSelected>>", self._on_freq_mode_preset_selected
        )
        ttk.Button(
            freq_row,
            text="＋",
            width=3,
            command=self._add_current_freq_mode_preset,
        ).grid(row=0, column=2, padx=(4, 0))
        ttk.Label(qso_panel, text="モード").grid(row=1, column=4, sticky="w", pady=4)
        self.mode_var = tk.StringVar(value=self.settings.last_mode or "FM")
        ttk.Combobox(
            qso_panel,
            textvariable=self.mode_var,
            values=("FM", "SSB", "CW", "AM", "FT8", "Other"),
            width=9,
        ).grid(row=1, column=5, sticky="ew", padx=(8, 0), pady=4)
        self._refresh_freq_mode_preset_combo()
        self.dupe_status_var = tk.StringVar(value="コールサインを入れると過去交信を表示します")
        ttk.Label(
            qso_panel,
            textvariable=self.dupe_status_var,
            style="Muted.TLabel",
        ).grid(row=2, column=0, columnspan=6, sticky="w", pady=(0, 2))
        ttk.Label(qso_panel, text="相手レポート").grid(row=3, column=0, sticky="w", pady=4)
        self.received_report_var = tk.StringVar()
        self.received_report_spin = self._make_report_spin(
            qso_panel, self.received_report_var
        )
        self.received_report_spin.grid(
            row=3, column=1, sticky="w", padx=(8, 18), pady=4
        )
        ttk.Label(qso_panel, text="自分レポート").grid(row=3, column=2, sticky="w", pady=4)
        self.sent_report_var = tk.StringVar()
        self.sent_report_spin = self._make_report_spin(
            qso_panel, self.sent_report_var
        )
        self.sent_report_spin.grid(
            row=3, column=3, sticky="w", padx=(8, 18), pady=4
        )
        ttk.Label(qso_panel, text="定型", style="Muted.TLabel").grid(
            row=4, column=0, sticky="w", pady=(0, 4)
        )
        self.report_preset_bar = ttk.Frame(qso_panel, style="Panel.TFrame")
        self.report_preset_bar.grid(
            row=4, column=1, columnspan=5, sticky="w", padx=(8, 0), pady=(0, 4)
        )
        self._refresh_report_preset_buttons()
        ttk.Label(qso_panel, text="QSL送信").grid(row=5, column=0, sticky="w", pady=4)
        self.qsl_sent_var = tk.StringVar(value="未設定")
        ttk.Combobox(
            qso_panel,
            textvariable=self.qsl_sent_var,
            values=tuple(_QSL_SENT_CODES),
            state="readonly",
            width=10,
        ).grid(row=5, column=1, sticky="w", padx=(8, 18), pady=4)
        ttk.Label(qso_panel, text="QSL受信").grid(row=5, column=2, sticky="w", pady=4)
        self.qsl_received_var = tk.StringVar(value="未設定")
        ttk.Combobox(
            qso_panel,
            textvariable=self.qsl_received_var,
            values=tuple(_QSL_RECEIVED_CODES),
            state="readonly",
            width=10,
        ).grid(row=5, column=3, sticky="w", padx=(8, 18), pady=4)
        ttk.Label(qso_panel, text="QSL経路").grid(row=5, column=4, sticky="w", pady=4)
        self.qsl_via_var = tk.StringVar()
        ttk.Entry(qso_panel, textvariable=self.qsl_via_var, width=14).grid(
            row=5, column=5, sticky="ew", padx=(8, 0), pady=4
        )

        ttk.Label(qso_panel, text="開始日時").grid(row=6, column=0, sticky="w", pady=4)
        self.datetime_controls = ttk.Frame(qso_panel, style="Panel.TFrame")
        self.datetime_controls.grid(
            row=6, column=1, columnspan=5, sticky="ew", padx=(8, 0), pady=4
        )
        self.qso_start_date = datetime.now().date()
        self.date_picker_button = ttk.Button(
            self.datetime_controls,
            text=self.qso_start_date.isoformat(),
            command=self._open_date_picker,
            state="disabled",
        )
        self.date_picker_button.pack(side="left")
        ttk.Label(self.datetime_controls, text="時刻").pack(
            side="left", padx=(12, 5)
        )
        current_time = datetime.now().astimezone()
        self.start_time_vars = [
            tk.StringVar(value=f"{part:02d}")
            for part in (current_time.hour, current_time.minute, current_time.second)
        ]
        self.time_display_var = tk.StringVar(value="")
        self.time_picker_button = ttk.Button(
            self.datetime_controls,
            textvariable=self.time_display_var,
            command=self._open_time_picker,
            state="disabled",
            width=9,
        )
        self.time_picker_button.pack(side="left")
        ttk.Label(
            self.datetime_controls, text="開始後に変更可", style="Muted.TLabel"
        ).pack(side="left", padx=(12, 0))
        self.dx_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self.datetime_controls,
            text="DX",
            variable=self.dx_var,
        ).pack(side="left", padx=(16, 0))
        self.time_zone_hint_var = tk.StringVar(value="")
        ttk.Label(
            qso_panel,
            textvariable=self.time_zone_hint_var,
            style="Muted.TLabel",
            wraplength=720,
        ).grid(row=7, column=1, columnspan=6, sticky="w", pady=(0, 2))
        self._update_time_display()

        self.callbook_panel = ttk.LabelFrame(
            qso_panel, text="相手局情報", padding=(8, 6)
        )
        self.callbook_panel.grid(
            row=8, column=0, columnspan=7, sticky="ew", pady=(4, 6)
        )
        for column in (1, 3, 5, 7):
            self.callbook_panel.columnconfigure(column, weight=1)
        self.callbook_lookup_button = ttk.Button(
            self.callbook_panel,
            text="局情報照会",
            command=self._lookup_callbook,
            state="disabled",
        )
        self.callbook_lookup_button.grid(row=0, column=0, sticky="w", padx=(0, 10), pady=2)
        self.callbook_status_var = tk.StringVar(
            value="QSO開始後に局情報照会が使えます（HamQTH未設定なら公開DXCC）"
        )
        ttk.Label(
            self.callbook_panel,
            textvariable=self.callbook_status_var,
            style="Muted.TLabel",
        ).grid(row=0, column=1, columnspan=5, sticky="w", pady=2)

        self.station_entry_vars = {
            "operator_name": tk.StringVar(),
            "qth": tk.StringVar(),
            "gridsquare": tk.StringVar(),
            "station_code": tk.StringVar(),
        }
        self.callbook_display_vars = {
            "country": tk.StringVar(value="—"),
            "dxcc": tk.StringVar(value="—"),
            "zones": tk.StringVar(value="—"),
            "dxcc_details": tk.StringVar(value="—"),
            "continent": tk.StringVar(value="—"),
        }
        station_fields = (
            ("His Name", "operator_name", 1, 0, 1, 2),
            ("QTH", "qth", 1, 3, 4, 2),
            ("国", "country", 1, 6, 7, 1),
            ("DXCC", "dxcc", 2, 0, 1, 1),
            ("GL", "gridsquare", 2, 2, 3, 1),
            ("CQ / ITU", "zones", 2, 4, 5, 1),
            ("Code (JCC/JCG)", "station_code", 2, 6, 7, 1),
            ("詳細", "dxcc_details", 3, 0, 1, 5),
            ("大陸", "continent", 3, 6, 7, 1),
        )
        self._jcc_after_id: str | None = None
        self._jcc_match = None
        for label, key, row, label_column, value_column, span in station_fields:
            ttk.Label(self.callbook_panel, text=label, style="Muted.TLabel").grid(
                row=row + 1,
                column=label_column,
                sticky="w",
                padx=(0, 5),
                pady=2,
            )
            if key == "station_code":
                code_row = ttk.Frame(self.callbook_panel)
                code_row.grid(
                    row=row + 1,
                    column=value_column,
                    columnspan=span,
                    sticky="ew",
                    padx=(0, 12),
                    pady=2,
                )
                code_row.columnconfigure(0, weight=1)
                ttk.Entry(
                    code_row,
                    textvariable=self.station_entry_vars["station_code"],
                    width=12,
                ).grid(row=0, column=0, sticky="ew")
                self.jcc_apply_button = ttk.Button(
                    code_row,
                    text="QTHへ",
                    width=6,
                    command=self._apply_jcc_qth,
                    state="disabled",
                )
                self.jcc_apply_button.grid(row=0, column=1, padx=(4, 0))
                continue
            if key == "qth":
                qth_row = ttk.Frame(self.callbook_panel)
                qth_row.grid(
                    row=row + 1,
                    column=value_column,
                    columnspan=span,
                    sticky="ew",
                    padx=(0, 12),
                    pady=2,
                )
                qth_row.columnconfigure(0, weight=1)
                ttk.Entry(
                    qth_row,
                    textvariable=self.station_entry_vars["qth"],
                    width=18,
                ).grid(row=0, column=0, sticky="ew")
                ttk.Button(
                    qth_row,
                    text="地名→Code",
                    width=10,
                    command=self._open_qth_code_search,
                ).grid(row=0, column=1, padx=(4, 0))
                continue
            if key in self.station_entry_vars:
                widget = ttk.Entry(
                    self.callbook_panel,
                    textvariable=self.station_entry_vars[key],
                    width=18,
                )
            else:
                widget = ttk.Label(
                    self.callbook_panel,
                    textvariable=self.callbook_display_vars[key],
                    wraplength=220,
                )
            widget.grid(
                row=row + 1,
                column=value_column,
                columnspan=span,
                sticky="ew" if key in self.station_entry_vars else "w",
                padx=(0, 12),
                pady=2,
            )

        self.jcc_status_var = tk.StringVar(
            value="Code に JCC/JCG/区番号を入れると地名を表示します"
        )
        ttk.Label(
            self.callbook_panel,
            textvariable=self.jcc_status_var,
            style="Muted.TLabel",
            wraplength=720,
        ).grid(row=5, column=0, columnspan=8, sticky="w", pady=(2, 0))
        self.station_entry_vars["station_code"].trace_add(
            "write", self._on_station_code_changed
        )

        ttk.Label(qso_panel, text="Remarks 1").grid(row=9, column=0, sticky="nw", pady=4)
        self.notes_text = tk.Text(
            qso_panel,
            height=3,
            wrap="word",
            relief="solid",
            borderwidth=1,
            background="#ffffff",
            foreground="#20332d",
            insertbackground="#20332d",
        )
        self.notes_text.grid(
            row=9, column=1, columnspan=5, sticky="nsew", padx=(8, 0), pady=4
        )
        self.notes_scrollbar = ttk.Scrollbar(
            qso_panel, orient="vertical", command=self.notes_text.yview
        )
        self.notes_scrollbar.grid(row=9, column=6, sticky="ns", padx=(4, 0), pady=4)
        self.notes_text.configure(yscrollcommand=self.notes_scrollbar.set)
        ttk.Label(qso_panel, text="Remarks 2").grid(row=10, column=0, sticky="nw", pady=4)
        self.remarks2_text = tk.Text(
            qso_panel,
            height=2,
            wrap="word",
            relief="solid",
            borderwidth=1,
            background="#ffffff",
            foreground="#20332d",
            insertbackground="#20332d",
        )
        self.remarks2_text.grid(
            row=10, column=1, columnspan=6, sticky="ew", padx=(8, 0), pady=4
        )

    def _build_history_tab(self) -> None:
        tab = self.history_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(3, weight=1)
        search_row = ttk.Frame(tab)
        search_row.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(search_row, text="履歴検索").pack(side="left", padx=(0, 8))
        self.history_search_var = tk.StringVar()
        search_entry = ttk.Entry(search_row, textvariable=self.history_search_var, width=36)
        search_entry.pack(side="left", fill="x", expand=True)
        search_entry.bind("<KeyRelease>", self._schedule_history_refresh)
        ttk.Button(search_row, text="更新", command=self._refresh_history).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(
            search_row,
            text="条件クリア",
            command=self._clear_history_filters,
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            search_row,
            text="選択を編集",
            command=self._edit_selected_history_row,
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            search_row,
            text="HAMLOG取込",
            command=self._import_history_hamlog_csv,
        ).pack(side="right", padx=(8, 0))
        ttk.Button(
            search_row,
            text="ADIF取込",
            command=self._import_history_adif,
        ).pack(side="right", padx=(8, 0))
        ttk.Button(
            search_row,
            text="HAMLOG CSV",
            command=self._export_history_hamlog_csv,
        ).pack(side="right", padx=(8, 0))
        ttk.Button(
            search_row,
            text="ADIF出力",
            command=self._export_history_adif,
        ).pack(side="right", padx=(8, 0))

        filter_row = ttk.Frame(tab)
        filter_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(filter_row, text="モード").pack(side="left")
        self.history_mode_var = tk.StringVar(value="すべて")
        mode_combo = ttk.Combobox(
            filter_row,
            textvariable=self.history_mode_var,
            values=("すべて", "FM", "SSB", "CW", "AM", "FT8", "Other"),
            state="readonly",
            width=8,
        )
        mode_combo.pack(side="left", padx=(4, 12))
        mode_combo.bind("<<ComboboxSelected>>", self._schedule_history_refresh)
        self.history_dx_only_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            filter_row,
            text="DXのみ",
            variable=self.history_dx_only_var,
            command=self._refresh_history,
        ).pack(side="left", padx=(0, 12))
        ttk.Label(filter_row, text="QSL").pack(side="left")
        self.history_qsl_var = tk.StringVar(value="すべて")
        qsl_combo = ttk.Combobox(
            filter_row,
            textvariable=self.history_qsl_var,
            values=(
                "すべて",
                "未送信",
                "未受領",
                "送信済",
                "受領済",
                "送信待ち",
                "請求中",
            ),
            state="readonly",
            width=8,
        )
        qsl_combo.pack(side="left", padx=(4, 12))
        qsl_combo.bind("<<ComboboxSelected>>", self._schedule_history_refresh)
        ttk.Label(filter_row, text="開始日").pack(side="left")
        self.history_date_from_var = tk.StringVar()
        date_from = ttk.Entry(filter_row, textvariable=self.history_date_from_var, width=11)
        date_from.pack(side="left", padx=(4, 4))
        date_from.bind("<KeyRelease>", self._schedule_history_refresh)
        ttk.Label(filter_row, text="〜").pack(side="left")
        self.history_date_to_var = tk.StringVar()
        date_to = ttk.Entry(filter_row, textvariable=self.history_date_to_var, width=11)
        date_to.pack(side="left", padx=(4, 12))
        date_to.bind("<KeyRelease>", self._schedule_history_refresh)
        ttk.Label(
            filter_row,
            text="YYYY-MM-DD",
            style="Muted.TLabel",
        ).pack(side="left")
        self.history_count_var = tk.StringVar(value="0件")
        ttk.Label(filter_row, textvariable=self.history_count_var, style="Muted.TLabel").pack(
            side="right"
        )

        delete_row = ttk.Frame(tab)
        delete_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self._history_delete_mode = False
        self._history_checked: set[str] = set()
        self._history_locked: set[str] = set()
        self.history_delete_mode_button = ttk.Button(
            delete_row,
            text="削除モード",
            command=self._toggle_history_delete_mode,
        )
        self.history_delete_mode_button.pack(side="left")
        self.history_select_all_button = ttk.Button(
            delete_row,
            text="すべて選択",
            command=self._select_all_history_rows,
            state="disabled",
        )
        self.history_select_all_button.pack(side="left", padx=(8, 0))
        self.history_clear_selection_button = ttk.Button(
            delete_row,
            text="選択解除",
            command=self._clear_history_selection,
            state="disabled",
        )
        self.history_clear_selection_button.pack(side="left", padx=(8, 0))
        self.history_delete_button = ttk.Button(
            delete_row,
            text="選択を削除",
            command=self._delete_selected_history_rows,
            state="disabled",
        )
        self.history_delete_button.pack(side="left", padx=(8, 0))
        ttk.Separator(delete_row, orient="vertical").pack(
            side="left", fill="y", padx=10, pady=2
        )
        self.history_lock_button = ttk.Button(
            delete_row,
            text="ロック",
            command=lambda: self._set_history_lock_for_selection(True),
        )
        self.history_lock_button.pack(side="left")
        self.history_unlock_button = ttk.Button(
            delete_row,
            text="ロック解除",
            command=lambda: self._set_history_lock_for_selection(False),
        )
        self.history_unlock_button.pack(side="left", padx=(8, 0))
        self.history_delete_status_var = tk.StringVar(
            value="ロック列クリックで保護。削除モードでは□で選択"
        )
        ttk.Label(
            delete_row,
            textvariable=self.history_delete_status_var,
            style="Muted.TLabel",
        ).pack(side="left", padx=(12, 0))

        columns = (
            "select",
            "lock",
            "started",
            "ended",
            "callsign",
            "dx",
            "operator_name",
            "qth",
            "country",
            "dxcc_details",
            "continent",
            "gridsquare",
            "station_code",
            "frequency",
            "mode",
            "received_report",
            "sent_report",
            "qsl_sent",
            "qsl_received",
            "qsl_via",
            "notes",
            "remarks2",
        )
        self.history_tree = ttk.Treeview(
            tab,
            columns=columns,
            show="headings",
            height=16,
            selectmode="extended",
        )
        headings = {
            "select": "選択",
            "lock": "保護",
            "started": "開始",
            "ended": "終了",
            "callsign": "コールサイン",
            "dx": "DX",
            "operator_name": "名前",
            "qth": "QTH",
            "country": "国",
            "dxcc_details": "DXCC詳細",
            "continent": "大陸",
            "gridsquare": "Grid",
            "station_code": "Code",
            "frequency": "周波数",
            "mode": "モード",
            "received_report": "相手レポート",
            "sent_report": "自分レポート",
            "qsl_sent": "QSL送信",
            "qsl_received": "QSL受信",
            "qsl_via": "QSL経路",
            "notes": "Remarks 1",
            "remarks2": "Remarks 2",
        }
        widths = {
            "select": 44,
            "lock": 56,
            "started": 170,
            "ended": 170,
            "callsign": 100,
            "dx": 40,
            "operator_name": 125,
            "qth": 120,
            "country": 100,
            "dxcc_details": 190,
            "continent": 70,
            "gridsquare": 90,
            "station_code": 95,
            "frequency": 90,
            "mode": 60,
            "received_report": 105,
            "sent_report": 105,
            "qsl_sent": 85,
            "qsl_received": 85,
            "qsl_via": 100,
            "notes": 200,
            "remarks2": 200,
        }
        for column in columns:
            self.history_tree.heading(column, text=headings[column])
            anchor = "center" if column in {"select", "lock"} else "w"
            self.history_tree.column(
                column, width=widths[column], minwidth=24, anchor=anchor
            )
        # Keep the checkbox column collapsed until delete mode is enabled.
        self.history_tree.column("select", width=0, minwidth=0, stretch=False)
        self.history_tree.grid(row=3, column=0, sticky="nsew")
        vertical_scrollbar = ttk.Scrollbar(
            tab, orient="vertical", command=self.history_tree.yview
        )
        vertical_scrollbar.grid(row=3, column=1, sticky="ns")
        horizontal_scrollbar = ttk.Scrollbar(
            tab, orient="horizontal", command=self.history_tree.xview
        )
        horizontal_scrollbar.grid(row=4, column=0, sticky="ew")
        self.history_tree.configure(
            yscrollcommand=vertical_scrollbar.set,
            xscrollcommand=horizontal_scrollbar.set,
        )
        self.history_tree.bind("<Button-1>", self._on_history_tree_click)
        self.history_tree.bind("<Double-1>", self._on_history_tree_double_click)

    def _build_candidates_tab(self) -> None:
        tab = self.candidates_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        columns = ("confirmed", "callsign", "transcript", "qso")
        self.candidate_tree = ttk.Treeview(tab, columns=columns, show="headings", height=18)
        headings = {
            "confirmed": "確認日時",
            "callsign": "コールサイン",
            "transcript": "文字起こし",
            "qso": "QSO ID",
        }
        widths = {"confirmed": 170, "callsign": 120, "transcript": 560, "qso": 80}
        for column in columns:
            self.candidate_tree.heading(column, text=headings[column])
            self.candidate_tree.column(column, width=widths[column], anchor="w")
        self.candidate_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(tab, orient="vertical", command=self.candidate_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.candidate_tree.configure(yscrollcommand=scrollbar.set)

    def _load_audio_devices(self, refresh_host: bool = False) -> None:
        previous = self.device_var.get() if hasattr(self, "device_var") else "システム既定"
        self._device_map = {"システム既定": None}
        try:
            if refresh_host:
                refresh_audio_host()
            for index, name in list_input_devices():
                label = f"{index}: {name}"
                self._device_map[label] = index
        except Exception:
            if hasattr(self, "status_var"):
                self.status_var.set(
                    "入力デバイス一覧を取得できません。認識開始時に確認してください"
                )
        if hasattr(self, "device_combo"):
            self.device_combo["values"] = tuple(self._device_map)
            if previous in self._device_map:
                self.device_combo.set(previous)
            else:
                preferred = match_device_label(
                    self._device_map,
                    self.settings.device_index,
                    self.settings.device_name,
                )
                self.device_combo.set(
                    preferred if preferred in self._device_map else "システム既定"
                )

    def _refresh_model_combo(self, preferred: str | None = None) -> None:
        downloaded = list_downloaded_models()
        preferred_key = parse_model_choice(
            preferred or self.model_var.get() or self.model_size
        )
        if (
            preferred_key
            and preferred_key not in downloaded
            and is_model_downloaded(preferred_key)
        ):
            downloaded = [
                preferred_key,
                *[key for key in downloaded if key != preferred_key],
            ]
        labels = [format_model_choice(key) for key in downloaded]
        self.model_combo["values"] = tuple(labels)
        if preferred_key in downloaded:
            self.model_var.set(format_model_choice(preferred_key))
            self.model_size = preferred_key
        elif downloaded:
            self.model_var.set(format_model_choice(downloaded[0]))
            self.model_size = downloaded[0]
        else:
            self.model_var.set("")
            self.model_size = preferred_key or "faster-whisper:small"

    def _is_model_downloading(self) -> bool:
        return (
            self._model_download_thread is not None
            and self._model_download_thread.is_alive()
        )

    def _open_settings(self) -> None:
        if self._settings_window is not None and self._settings_window.winfo_exists():
            self._settings_window.lift()
            self._settings_window.focus_force()
            return

        window = tk.Toplevel(self.root)
        self._settings_window = window
        window.title("設定")
        window.transient(self.root)
        window.minsize(520, 420)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)
        window.protocol("WM_DELETE_WINDOW", self._close_settings)

        notebook = ttk.Notebook(window)
        notebook.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        model_tab = ttk.Frame(notebook, padding=12)
        callbook_tab = ttk.Frame(notebook, padding=12)
        preset_tab = ttk.Frame(notebook, padding=12)
        notebook.add(model_tab, text="認識モデル")
        notebook.add(callbook_tab, text="HamQTH")
        notebook.add(preset_tab, text="QSOプリセット")
        self._build_model_settings_tab(model_tab, window)
        self._build_callbook_settings_tab(callbook_tab, window)
        self._build_preset_settings_tab(preset_tab, window)
        window.grab_set()

    def _close_settings(self) -> None:
        if self._settings_window is not None and self._settings_window.winfo_exists():
            self._settings_window.grab_release()
            self._settings_window.destroy()
        self._settings_window = None
        self._model_settings_ui = None

    def _reset_model_download_progress_ui(self) -> None:
        ui = self._model_settings_ui
        if ui is None:
            return
        progress_bar: ttk.Progressbar = ui["progress_bar"]
        progress_bar.stop()
        progress_bar.configure(mode="determinate", maximum=100, value=0)
        ui["progress_percent_var"].set("")

    def _apply_model_download_progress(
        self, current: int, total: int, description: str
    ) -> None:
        self._model_download_last = (current, total, description)
        ui = self._model_settings_ui
        if ui is None:
            if self._is_model_downloading() and self._model_download_name:
                label = format_model_choice(self._model_download_name)
                self.status_var.set(
                    f"モデル取得中: {label}"
                    + (f" {min(100.0, current / total * 100):.0f}%" if total > 0 else "")
                )
            return

        progress_bar: ttk.Progressbar = ui["progress_bar"]
        status_var: tk.StringVar = ui["status_var"]
        progress_percent_var: tk.StringVar = ui["progress_percent_var"]
        label = description or "ダウンロード中"
        if total > 0:
            percent = min(100.0, (current / total) * 100)
            if str(progress_bar["mode"]) != "determinate":
                progress_bar.stop()
                progress_bar.configure(mode="determinate", maximum=100)
            progress_bar["value"] = percent
            progress_percent_var.set(f"{percent:.0f}%")
            status_var.set(
                f"{label}: {format_byte_size(current)} / {format_byte_size(total)}"
            )
        else:
            if str(progress_bar["mode"]) != "indeterminate":
                progress_bar.configure(mode="indeterminate")
                progress_bar.start(12)
            progress_percent_var.set("")
            if current > 0:
                status_var.set(f"{label}: {format_byte_size(current)}")
            else:
                status_var.set(f"{label}…")

    def _set_model_download_controls(self, enabled: bool) -> None:
        ui = self._model_settings_ui
        if ui is None:
            return
        state = "normal" if enabled else "disabled"
        ui["download_button"].configure(state=state)
        ui["select_button"].configure(state=state)

    def _refresh_model_settings_tree(self, select_name: str | None = None) -> None:
        ui = self._model_settings_ui
        if ui is None:
            return
        tree: ttk.Treeview = ui["tree"]
        for item in tree.get_children():
            tree.delete(item)
        for row in list_model_statuses():
            status = row["status"]
            if (
                self._is_model_downloading()
                and row["key"] == self._model_download_name
            ):
                status = "取得中"
            tree.insert(
                "",
                "end",
                iid=row["key"],
                values=(row["engine"], row["label"], status),
            )
        target = parse_model_choice(
            select_name or self._model_download_name or self.model_size
        )
        if target in tree.get_children():
            tree.selection_set(target)
            tree.see(target)
        self._refresh_model_combo(preferred=self.model_size)

    def _start_model_download(self, model_name: str) -> None:
        label = format_model_choice(model_name)
        self._model_download_name = model_name
        self._model_download_last = (0, 0, f"{label} を準備中")
        self._set_model_download_controls(False)
        self._reset_model_download_progress_ui()
        self._apply_model_download_progress(0, 0, f"{label} を準備中")
        self._refresh_model_settings_tree(select_name=model_name)
        self.status_var.set(f"モデル取得中: {label}")

        def on_progress(current: int, total: int, description: str) -> None:
            self._model_download_progress.put((current, total, description))

        def worker() -> None:
            try:
                download_model(model_name, progress_callback=on_progress)
                self._model_download_results.put((model_name, None))
            except ModelManagerError as exc:
                self._model_download_results.put((model_name, str(exc)))
            except Exception as exc:
                self._model_download_results.put(
                    (model_name, f"ダウンロードに失敗しました: {exc}")
                )

        self._model_download_thread = threading.Thread(target=worker, daemon=True)
        self._model_download_thread.start()

    def _finish_model_download(self, model_name: str, error: str | None) -> None:
        parent = (
            self._settings_window
            if self._settings_window is not None and self._settings_window.winfo_exists()
            else self.root
        )
        self._model_download_thread = None
        self._model_download_name = None
        self._set_model_download_controls(True)

        if error:
            self._model_download_last = None
            self._reset_model_download_progress_ui()
            if self._model_settings_ui is not None:
                self._model_settings_ui["status_var"].set("ダウンロードに失敗しました")
            self.status_var.set("モデルのダウンロードに失敗しました")
            self._refresh_model_settings_tree()
            messagebox.showerror("認識モデル", error, parent=parent)
            return

        label = format_model_choice(model_name)
        self._model_download_last = (1, 1, f"{label} を取得しました")
        if self._model_settings_ui is not None:
            progress_bar: ttk.Progressbar = self._model_settings_ui["progress_bar"]
            progress_bar.stop()
            progress_bar.configure(mode="determinate", maximum=100, value=100)
            self._model_settings_ui["progress_percent_var"].set("100%")
            self._model_settings_ui["status_var"].set(f"{label} を取得しました")
        self._refresh_model_combo(preferred=model_name)
        self._refresh_model_settings_tree(select_name=model_name)
        self.status_var.set(f"モデル「{label}」を取得しました")
        messagebox.showinfo(
            "認識モデル",
            f"「{label}」をダウンロードしました。",
            parent=parent,
        )

    def _process_model_download_events(self) -> None:
        try:
            while True:
                self._apply_model_download_progress(
                    *self._model_download_progress.get_nowait()
                )
        except queue.Empty:
            pass

        try:
            model_name, error = self._model_download_results.get_nowait()
        except queue.Empty:
            return
        self._finish_model_download(model_name, error)

    def _build_model_settings_tab(self, tab: ttk.Frame, window: tk.Toplevel) -> None:
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        ttk.Label(
            tab,
            text=(
                "faster-whisper / whisper.cpp / Moonshine のモデルをここで取得します。"
                "メイン画面では取得済みのみ選べます。"
                "whisper.cpp / Moonshine は初回ダウンロード時に必要なパッケージも自動インストールします。"
            ),
            style="Muted.TLabel",
            wraplength=480,
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        columns = ("engine", "label", "status")
        tree = ttk.Treeview(tab, columns=columns, show="headings", height=12)
        tree.heading("engine", text="エンジン")
        tree.heading("label", text="モデル")
        tree.heading("status", text="状態")
        tree.column("engine", width=120, anchor="w")
        tree.column("label", width=120, anchor="w")
        tree.column("status", width=90, anchor="w")
        tree.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(tab, orient="vertical", command=tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        tree.configure(yscrollcommand=scrollbar.set)

        progress_frame = ttk.Frame(tab)
        progress_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        progress_frame.columnconfigure(0, weight=1)
        progress_bar = ttk.Progressbar(
            progress_frame,
            mode="determinate",
            maximum=100,
            value=0,
        )
        progress_bar.grid(row=0, column=0, sticky="ew")
        progress_percent_var = tk.StringVar(value="")
        ttk.Label(progress_frame, textvariable=progress_percent_var, width=5).grid(
            row=0, column=1, padx=(8, 0)
        )

        status_var = tk.StringVar(value="未取得のモデルを選んでダウンロードできます。")
        ttk.Label(tab, textvariable=status_var, style="Muted.TLabel").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(4, 8)
        )

        actions = ttk.Frame(tab)
        actions.grid(row=4, column=0, columnspan=2, sticky="ew")

        def selected_model() -> str | None:
            selection = tree.selection()
            return selection[0] if selection else None

        def download_selected() -> None:
            if self._is_model_downloading():
                messagebox.showinfo(
                    "認識モデル",
                    "別のダウンロードが進行中です。",
                    parent=window,
                )
                return
            model_name = selected_model()
            if not model_name:
                messagebox.showinfo("認識モデル", "モデルを選択してください。", parent=window)
                return
            if is_model_downloaded(model_name):
                messagebox.showinfo(
                    "認識モデル",
                    f"「{format_model_choice(model_name)}」はすでに取得済みです。",
                    parent=window,
                )
                return
            self._start_model_download(model_name)

        def select_selected() -> None:
            model_name = selected_model()
            if not model_name:
                messagebox.showinfo("認識モデル", "モデルを選択してください。", parent=window)
                return
            if self._is_model_downloading():
                messagebox.showinfo(
                    "認識モデル",
                    "ダウンロード中はモデルを変更できません。",
                    parent=window,
                )
                return
            if not is_model_downloaded(model_name):
                messagebox.showinfo(
                    "認識モデル",
                    f"「{format_model_choice(model_name)}」は未取得です。"
                    "先にダウンロードしてください。",
                    parent=window,
                )
                return
            if self.worker is not None and self.worker.is_alive():
                messagebox.showinfo(
                    "認識モデル",
                    "認識中はモデルを変更できません。停止してから選んでください。",
                    parent=window,
                )
                return
            label = format_model_choice(model_name)
            self._refresh_model_combo(preferred=model_name)
            status_var.set(f"利用モデルを {label} に設定しました")
            messagebox.showinfo(
                "認識モデル",
                f"利用モデルを「{label}」に設定しました。",
                parent=window,
            )

        download_button = ttk.Button(
            actions,
            text="ダウンロード",
            style="Accent.TButton",
            command=download_selected,
        )
        download_button.pack(side="left")
        select_button = ttk.Button(
            actions,
            text="このモデルを使う",
            command=select_selected,
        )
        select_button.pack(side="left", padx=(8, 0))
        ttk.Button(
            actions,
            text="一覧を更新",
            command=lambda: self._refresh_model_settings_tree(),
        ).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="閉じる", command=self._close_settings).pack(side="right")

        self._model_settings_ui = {
            "tree": tree,
            "progress_bar": progress_bar,
            "progress_percent_var": progress_percent_var,
            "status_var": status_var,
            "download_button": download_button,
            "select_button": select_button,
        }
        self._refresh_model_settings_tree(
            select_name=self._model_download_name or self.model_size
        )
        if self._is_model_downloading():
            self._set_model_download_controls(False)
            if self._model_download_last is not None:
                self._apply_model_download_progress(*self._model_download_last)
            else:
                self._apply_model_download_progress(
                    0,
                    0,
                    f"{format_model_choice(self._model_download_name or '')} をダウンロード中",
                )
        else:
            self._set_model_download_controls(True)

    def _build_callbook_settings_tab(self, tab: ttk.Frame, window: tk.Toplevel) -> None:
        tab.columnconfigure(1, weight=1)

        try:
            saved_username, _ = load_hamqth_credentials()
        except CallbookError:
            saved_username = ""

        username_var = tk.StringVar(value=saved_username)
        password_var = tk.StringVar()
        ttk.Label(tab, text="HamQTHユーザー名").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 6)
        )
        ttk.Entry(tab, textvariable=username_var, width=32).grid(
            row=0, column=1, sticky="ew", pady=(0, 6)
        )
        ttk.Label(tab, text="パスワード").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=6
        )
        ttk.Entry(tab, textvariable=password_var, show="•", width=32).grid(
            row=1, column=1, sticky="ew", pady=6
        )
        ttk.Label(
            tab,
            text="認証情報はOSの資格情報ストアに保存します。",
            style="Muted.TLabel",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 10))

        actions = ttk.Frame(tab)
        actions.grid(row=3, column=0, columnspan=2, sticky="ew")
        ttk.Button(actions, text="閉じる", command=self._close_settings).pack(side="right")

        def save() -> None:
            try:
                save_hamqth_credentials(username_var.get(), password_var.get())
            except CallbookError as exc:
                messagebox.showerror("照会設定エラー", str(exc), parent=window)
                return
            self.hamqth_client = None
            self._refresh_callbook_button()
            messagebox.showinfo("照会設定", "HamQTHの認証情報を保存しました。", parent=window)

        ttk.Button(
            actions,
            text="保存",
            style="Accent.TButton",
            command=save,
        ).pack(side="right", padx=(0, 8))

    def _build_preset_settings_tab(self, tab: ttk.Frame, window: tk.Toplevel) -> None:
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        tab.rowconfigure(3, weight=1)

        ttk.Label(
            tab,
            text="周波数 / モード（1行に「145.000 FM」または「145.000/FM」）",
            style="Muted.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        freq_text = tk.Text(tab, height=8, wrap="none")
        freq_text.grid(row=1, column=0, sticky="nsew", pady=(0, 10))
        freq_text.insert(
            "1.0",
            "\n".join(
                f"{item['frequency']} {item['mode']}"
                for item in self.settings.freq_mode_presets
            ),
        )

        ttk.Label(
            tab,
            text="レポート（1行に「59/59」。片方だけなら両方に同じ値）",
            style="Muted.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(0, 4))
        report_text = tk.Text(tab, height=5, wrap="none")
        report_text.grid(row=3, column=0, sticky="nsew", pady=(0, 10))
        report_text.insert("1.0", "\n".join(self.settings.report_presets))

        time_row = ttk.Frame(tab)
        time_row.grid(row=4, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(time_row, text="履歴の時刻表示").pack(side="left")
        time_display_var = tk.StringVar(
            value=normalize_time_display(self.settings.time_display)
        )
        ttk.Combobox(
            time_row,
            textvariable=time_display_var,
            values=TIME_DISPLAY_MODES,
            state="readonly",
            width=10,
        ).pack(side="left", padx=(8, 0))
        ttk.Label(
            time_row,
            text="jst / utc / both（ADIFは常にUTC、HAMLOG CSVは常にJST）",
            style="Muted.TLabel",
        ).pack(side="left", padx=(10, 0))

        qsl_defaults = ttk.LabelFrame(tab, text="QSO開始時のQSL既定値", padding=(8, 6))
        qsl_defaults.grid(row=5, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(qsl_defaults, text="送信").grid(row=0, column=0, sticky="w")
        default_qsl_sent_var = tk.StringVar(
            value=normalize_qsl_sent_label(self.settings.default_qsl_sent)
        )
        ttk.Combobox(
            qsl_defaults,
            textvariable=default_qsl_sent_var,
            values=tuple(_QSL_SENT_CODES),
            state="readonly",
            width=10,
        ).grid(row=0, column=1, sticky="w", padx=(6, 16))
        ttk.Label(qsl_defaults, text="受信").grid(row=0, column=2, sticky="w")
        default_qsl_received_var = tk.StringVar(
            value=normalize_qsl_received_label(self.settings.default_qsl_received)
        )
        ttk.Combobox(
            qsl_defaults,
            textvariable=default_qsl_received_var,
            values=tuple(_QSL_RECEIVED_CODES),
            state="readonly",
            width=10,
        ).grid(row=0, column=3, sticky="w", padx=(6, 16))
        ttk.Label(qsl_defaults, text="経路").grid(row=0, column=4, sticky="w")
        default_qsl_via_var = tk.StringVar(value=self.settings.default_qsl_via)
        ttk.Entry(qsl_defaults, textvariable=default_qsl_via_var, width=14).grid(
            row=0, column=5, sticky="w", padx=(6, 0)
        )

        actions = ttk.Frame(tab)
        actions.grid(row=6, column=0, sticky="ew")
        ttk.Button(actions, text="閉じる", command=self._close_settings).pack(side="right")

        def save() -> None:
            freq_lines = [
                line.strip()
                for line in freq_text.get("1.0", "end").splitlines()
                if line.strip()
            ]
            report_lines = [
                line.strip()
                for line in report_text.get("1.0", "end").splitlines()
                if line.strip()
            ]
            self.settings.freq_mode_presets = normalize_freq_mode_presets(freq_lines)
            self.settings.report_presets = normalize_report_presets(report_lines)
            self.settings.time_display = normalize_time_display(time_display_var.get())
            self.settings.default_qsl_sent = normalize_qsl_sent_label(
                default_qsl_sent_var.get()
            )
            self.settings.default_qsl_received = normalize_qsl_received_label(
                default_qsl_received_var.get()
            )
            self.settings.default_qsl_via = default_qsl_via_var.get().strip()
            self._refresh_freq_mode_preset_combo()
            self._refresh_report_preset_buttons()
            self._refresh_history()
            self._persist_settings()
            messagebox.showinfo(
                "QSOプリセット",
                "プリセットを保存しました。",
                parent=window,
            )

        ttk.Button(
            actions,
            text="保存",
            style="Accent.TButton",
            command=save,
        ).pack(side="right", padx=(0, 8))

    def _refresh_freq_mode_preset_combo(self) -> None:
        if not hasattr(self, "freq_mode_preset_combo"):
            return
        labels = [
            format_freq_mode_preset(item) for item in self.settings.freq_mode_presets
        ]
        self.freq_mode_preset_combo.configure(values=labels)
        if self.freq_mode_preset_var.get() not in labels:
            self.freq_mode_preset_var.set("")

    def _on_freq_mode_preset_selected(self, _event: tk.Event | None = None) -> None:
        label = self.freq_mode_preset_var.get().strip()
        for item in self.settings.freq_mode_presets:
            if format_freq_mode_preset(item) == label:
                self.frequency_var.set(item["frequency"])
                self.mode_var.set(item["mode"])
                self.qso_status_var.set(
                    f"プリセット {label} を周波数 / モードへ入れました"
                )
                return

    def _add_current_freq_mode_preset(self) -> None:
        frequency = unicodedata.normalize("NFKC", self.frequency_var.get()).strip()
        mode = (self.mode_var.get() or "FM").strip() or "FM"
        if not frequency:
            messagebox.showinfo("プリセット", "先に周波数を入力してください。")
            return
        presets = list(self.settings.freq_mode_presets)
        if any(
            item.get("frequency") == frequency and item.get("mode") == mode
            for item in presets
        ):
            self.qso_status_var.set(f"{frequency} · {mode} は既にプリセットにあります")
            return
        presets.insert(0, {"frequency": frequency, "mode": mode})
        self.settings.freq_mode_presets = normalize_freq_mode_presets(presets)
        self._refresh_freq_mode_preset_combo()
        self.freq_mode_preset_var.set(format_freq_mode_preset(presets[0]))
        self._persist_settings()
        self.qso_status_var.set(f"{frequency} · {mode} をプリセットに追加しました")

    def _refresh_report_preset_buttons(self) -> None:
        if not hasattr(self, "report_preset_bar"):
            return
        for child in self.report_preset_bar.winfo_children():
            child.destroy()
        for preset in self.settings.report_presets:
            ttk.Button(
                self.report_preset_bar,
                text=preset,
                width=max(6, len(preset) + 1),
                command=lambda value=preset: self._apply_report_preset(value),
            ).pack(side="left", padx=(0, 4))

    def _apply_report_preset(self, value: str) -> None:
        parsed = parse_report_preset(value)
        if parsed is None:
            return
        received, sent = parsed
        self.received_report_var.set(received)
        self.sent_report_var.set(sent)
        self.qso_status_var.set(f"レポート {received}/{sent} を入力しました")

    def _make_report_spin(
        self, parent: ttk.Frame | tk.Misc, variable: tk.StringVar
    ) -> ttk.Frame:
        """Report field with ▲▼ / Up-Down / wheel nudging by 1."""
        frame = ttk.Frame(parent, style="Panel.TFrame")
        entry = ttk.Entry(frame, textvariable=variable, width=8)
        entry.pack(side="left")
        arrows = ttk.Frame(frame, style="Panel.TFrame")
        arrows.pack(side="left", padx=(4, 0))
        ttk.Button(
            arrows,
            text="▲",
            width=2,
            command=lambda: self._nudge_report(variable, 1),
        ).pack(side="left")
        ttk.Button(
            arrows,
            text="▼",
            width=2,
            command=lambda: self._nudge_report(variable, -1),
        ).pack(side="left", padx=(2, 0))

        def bump(delta: int, _event: tk.Event | None = None) -> str:
            self._nudge_report(variable, delta)
            return "break"

        entry.bind("<Up>", lambda event: bump(1, event))
        entry.bind("<Down>", lambda event: bump(-1, event))
        entry.bind(
            "<MouseWheel>",
            lambda event: bump(1 if event.delta > 0 else -1, event),
        )
        entry.bind("<Button-4>", lambda event: bump(1, event))
        entry.bind("<Button-5>", lambda event: bump(-1, event))
        return frame

    def _nudge_report(self, variable: tk.StringVar, delta: int) -> None:
        variable.set(nudge_report_value(variable.get(), delta))

    def _session_qso_id(self) -> int | None:
        """Active recording QSO, or history-edit target."""
        if self.active_qso_id is not None:
            return self.active_qso_id
        return self.editing_qso_id

    def _lookup_callbook(self) -> None:
        qso_id = self._session_qso_id()
        if qso_id is None:
            messagebox.showinfo(
                "局情報照会",
                "QSOを開始するか、履歴から編集を開始してください。",
            )
            return
        callsign = unicodedata.normalize("NFKC", self.qso_callsign_var.get()).strip().upper()
        if not callsign:
            messagebox.showinfo("局情報照会", "コールサインを入力してください。")
            return

        self.qso_callsign_var.set(callsign)
        self.callbook_lookup_button.configure(text="照会中…", state="disabled")
        self.settings_button.configure(state="disabled")
        self.callbook_status_var.set(f"{callsign} を公開情報から照会しています")
        self.callbook_lookup_thread = threading.Thread(
            target=self._perform_callbook_lookup,
            args=(qso_id, callsign),
            daemon=True,
        )
        self.callbook_lookup_thread.start()

    def _perform_callbook_lookup(self, qso_id: int, callsign: str) -> None:
        try:
            client = self.hamqth_client
            if client is None:
                try:
                    username, password = load_hamqth_credentials()
                except CallbookError:
                    result = lookup_public_dxcc(callsign)
                else:
                    client = HamQTHClient(username, password)
                    self.hamqth_client = client
                    result = client.lookup(callsign)
            else:
                result = client.lookup(callsign)
            self.events.put(("callbook_result", (qso_id, callsign, result)))
        except CallbookError as exc:
            self.events.put(("callbook_error", (qso_id, str(exc))))
        except Exception:
            self.events.put(("callbook_error", (qso_id, "局情報を取得できませんでした。")))
        finally:
            self.events.put(("callbook_done", qso_id))

    def _review_callbook_result(
        self, qso_id: int, callsign: str, result: dict[str, str]
    ) -> None:
        current_callsign = unicodedata.normalize(
            "NFKC", self.qso_callsign_var.get()
        ).strip().upper()
        if qso_id != self._session_qso_id() or current_callsign != callsign:
            self.callbook_status_var.set("QSOまたはコールサインが変更されたため、取得結果を破棄しました")
            return

        labels = (
            ("名前", "operator_name"),
            ("QTH", "qth"),
            ("国", "country"),
            ("DXCC", "dxcc"),
            ("グリッド", "gridsquare"),
            ("CQゾーン", "cq_zone"),
            ("ITUゾーン", "itu_zone"),
            ("詳細", "dxcc_details"),
            ("大陸", "continent"),
        )
        details = "\n".join(
            f"{label}: {result.get(key) or '—'}" for label, key in labels
        )
        if not any(result.get(key) for _, key in labels):
            messagebox.showinfo("局情報照会", f"{callsign} の基本局情報は見つかりませんでした。")
            return

        if messagebox.askyesno(
            "局情報を確認",
            f"{callsign} の局情報です。\n\n{details}\n\nこのQSOログに付加しますか？",
        ):
            self.callbook_data = result
            self._render_callbook_info(result)
            if not is_japan_entity(
                result.get("dxcc", ""), result.get("country", "")
            ):
                self.dx_var.set(True)
            self.callbook_status_var.set(
                f"{result['lookup_provider']}情報をQSO終了時に保存します · {result['lookup_at']}"
            )

    def _render_callbook_info(self, result: dict[str, str]) -> None:
        for key in ("operator_name", "qth", "gridsquare"):
            if result.get(key):
                self.station_entry_vars[key].set(result[key])
        for key in ("country", "dxcc"):
            self.callbook_display_vars[key].set(result.get(key) or "—")
        cq_zone = result.get("cq_zone", "")
        itu_zone = result.get("itu_zone", "")
        zones = f"{cq_zone or '—'} / {itu_zone or '—'}"
        self.callbook_display_vars["zones"].set(zones)
        for key in ("dxcc_details", "continent"):
            self.callbook_display_vars[key].set(result.get(key) or "—")

    def _clear_callbook_info(self, status: str = "") -> None:
        self.callbook_data = {}
        self._render_callbook_info({})
        for variable in self.station_entry_vars.values():
            variable.set("")
        self.callbook_status_var.set(status or "局情報未照会")

    def _on_station_code_changed(self, *_args: Any) -> None:
        if self._jcc_after_id is not None:
            try:
                self.root.after_cancel(self._jcc_after_id)
            except tk.TclError:
                pass
        self._jcc_after_id = self.root.after(200, self._refresh_jcc_helper)

    def _refresh_jcc_helper(self) -> None:
        self._jcc_after_id = None
        if not hasattr(self, "jcc_status_var"):
            return
        code = normalize_station_code(self.station_entry_vars["station_code"].get())
        if len(code) < 3:
            self._jcc_match = None
            self.jcc_status_var.set("Code に JCC/JCG/区番号を入れると地名を表示します")
            self.jcc_apply_button.configure(state="disabled")
            return
        entry = lookup_station_code(code)
        self._jcc_match = entry
        if entry is None:
            self.jcc_status_var.set(f"{code}: 辞書に未登録（手入力のまま保存できます）")
            self.jcc_apply_button.configure(state="disabled")
            return
        self.jcc_status_var.set(entry.label)
        self.jcc_apply_button.configure(state="normal")
        # Fill empty QTH automatically; never overwrite an existing value.
        if entry.qth and not self.station_entry_vars["qth"].get().strip():
            self.station_entry_vars["qth"].set(entry.qth)

    def _apply_jcc_qth(self) -> None:
        entry = self._jcc_match
        if entry is None or not entry.qth:
            return
        self.station_entry_vars["qth"].set(entry.qth)
        self.qso_status_var.set(f"Code {entry.code} → QTH「{entry.qth}」")

    def _open_qth_code_search(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("地名から Code を検索")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(True, True)
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        ttk.Label(
            frame,
            text="地名・市区町村・コードの一部（2文字以上）",
            style="Muted.TLabel",
        ).grid(row=0, column=0, sticky="w")
        query_var = tk.StringVar(value=self.station_entry_vars["qth"].get().strip())
        query_entry = ttk.Entry(frame, textvariable=query_var, width=40)
        query_entry.grid(row=1, column=0, sticky="ew", pady=(4, 8))
        listbox = tk.Listbox(frame, height=12, exportselection=False)
        listbox.grid(row=2, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=listbox.yview)
        scrollbar.grid(row=2, column=1, sticky="ns")
        listbox.configure(yscrollcommand=scrollbar.set)
        hits: list[Any] = []

        def refresh_hits(_event: tk.Event | None = None) -> None:
            nonlocal hits
            hits = search_station_codes(query_var.get(), limit=40)
            listbox.delete(0, "end")
            for entry in hits:
                mark = "（廃止）" if entry.deleted else ""
                listbox.insert("end", f"{entry.code}  {entry.qth}{mark}")

        def apply_selected(_event: tk.Event | None = None) -> None:
            selection = listbox.curselection()
            if not selection:
                return
            entry = hits[int(selection[0])]
            self.station_entry_vars["station_code"].set(entry.code)
            if entry.qth and (
                not self.station_entry_vars["qth"].get().strip()
                or messagebox.askyesno(
                    "QTHの反映",
                    f"QTHを「{entry.qth}」にしますか？",
                    parent=dialog,
                )
            ):
                self.station_entry_vars["qth"].set(entry.qth)
            self._refresh_jcc_helper()
            self.qso_status_var.set(f"Code {entry.code} を入れました")
            dialog.destroy()

        actions = ttk.Frame(frame)
        actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(actions, text="検索", command=refresh_hits).pack(side="left")
        ttk.Button(
            actions, text="選択して反映", style="Accent.TButton", command=apply_selected
        ).pack(side="right")
        ttk.Button(actions, text="閉じる", command=dialog.destroy).pack(
            side="right", padx=(0, 8)
        )
        query_entry.bind("<KeyRelease>", refresh_hits)
        listbox.bind("<Double-Button-1>", apply_selected)
        refresh_hits()
        query_entry.focus_set()

    def _on_qso_callsign_changed(self, *_args: Any) -> None:
        current_callsign = unicodedata.normalize(
            "NFKC", self.qso_callsign_var.get()
        ).strip().upper()
        if self.callbook_data and hasattr(self, "callbook_status_var"):
            if current_callsign != self.callbook_data.get("callsign"):
                self._clear_callbook_info("コールサイン変更により局情報を解除しました")
        self._schedule_dupe_check()

    def _schedule_dupe_check(self) -> None:
        if self._dupe_after_id is not None:
            try:
                self.root.after_cancel(self._dupe_after_id)
            except tk.TclError:
                pass
        self._dupe_after_id = self.root.after(250, self._refresh_dupe_status)

    def _refresh_dupe_status(self) -> None:
        self._dupe_after_id = None
        if not hasattr(self, "dupe_status_var"):
            return
        callsign = unicodedata.normalize(
            "NFKC", self.qso_callsign_var.get()
        ).strip().upper()
        if len(callsign) < 4:
            self._dupe_latest = None
            self._dupe_recent = []
            self._dupe_total = 0
            self.dupe_status_var.set("コールサインを入れると過去交信を表示します")
            self.dupe_apply_button.configure(state="disabled")
            return

        history = self.store.lookup_callsign_history(callsign, limit=30)
        recent = history.get("recent") or []
        self._dupe_recent = [row for row in recent if isinstance(row, dict)]
        self._dupe_latest = self._dupe_recent[0] if self._dupe_recent else None
        total = int(history.get("total") or 0)
        self._dupe_total = total
        if total <= 0 or self._dupe_latest is None:
            self.dupe_status_var.set(f"{callsign}: このアプリ内に過去交信なし（ニュー）")
            self.dupe_apply_button.configure(state="disabled")
            return

        started = self._format_time(self._dupe_latest.get("started_at"))
        mode = self._dupe_latest.get("mode") or "?"
        freq = self._dupe_latest.get("frequency") or "?"
        name = self._dupe_latest.get("operator_name") or ""
        qth = self._dupe_latest.get("qth") or ""
        detail = " / ".join(part for part in (name, qth) if part)
        suffix = f" · {detail}" if detail else ""
        if total == 1:
            self.dupe_status_var.set(
                f"{callsign}: 過去 1件 · {started} {mode} {freq}{suffix}"
            )
        else:
            self.dupe_status_var.set(
                f"{callsign}: 過去 {total}件 · 直近 {started} {mode} {freq}{suffix}"
                "（前回値で選択）"
            )
        self.dupe_apply_button.configure(state="normal")

    def _choose_dupe_fields(self) -> None:
        """Apply the only prior QSO, or open a picker when several exist."""
        if not self._dupe_recent:
            return
        if len(self._dupe_recent) == 1:
            self._apply_dupe_record(self._dupe_recent[0])
            return
        self._open_dupe_picker()

    def _open_dupe_picker(self) -> None:
        if self._dupe_picker is not None and self._dupe_picker.winfo_exists():
            self._dupe_picker.lift()
            self._dupe_picker.focus_force()
            return

        callsign = (
            self._dupe_latest.get("callsign")
            if self._dupe_latest
            else self.qso_callsign_var.get().strip().upper()
        )
        window = tk.Toplevel(self.root)
        self._dupe_picker = window
        window.title(f"前回値を選択 — {callsign}")
        window.transient(self.root)
        window.resizable(True, True)
        window.minsize(640, 280)

        shown = len(self._dupe_recent)
        total = self._dupe_total
        summary = (
            f"{callsign}: 直近 {shown}件を表示（全 {total}件）"
            if total > shown
            else f"{callsign}: 過去交信 {shown}件から取り込む行を選んでください"
        )
        ttk.Label(window, text=summary, style="Muted.TLabel").pack(
            anchor="w", padx=12, pady=(12, 6)
        )

        columns = (
            "started_at",
            "mode",
            "frequency",
            "operator_name",
            "qth",
            "station_code",
            "gridsquare",
        )
        headings = {
            "started_at": "日時",
            "mode": "モード",
            "frequency": "周波数",
            "operator_name": "Name",
            "qth": "QTH",
            "station_code": "Code",
            "gridsquare": "GL",
        }
        widths = {
            "started_at": 150,
            "mode": 70,
            "frequency": 90,
            "operator_name": 100,
            "qth": 120,
            "station_code": 90,
            "gridsquare": 80,
        }

        tree_frame = ttk.Frame(window, padding=(12, 0))
        tree_frame.pack(fill="both", expand=True)
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=10)
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], minwidth=50, stretch=True)
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        row_by_iid: dict[str, dict[str, Any]] = {}
        for row in self._dupe_recent:
            iid = str(row.get("id") or len(row_by_iid))
            row_by_iid[iid] = row
            tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    self._format_time(row.get("started_at")),
                    row.get("mode") or "",
                    row.get("frequency") or "",
                    row.get("operator_name") or "",
                    row.get("qth") or "",
                    row.get("station_code") or "",
                    row.get("gridsquare") or "",
                ),
            )
        first = tree.get_children()
        if first:
            tree.selection_set(first[0])
            tree.focus(first[0])

        def apply_selection() -> None:
            selected = tree.selection()
            if not selected:
                messagebox.showinfo(
                    "前回値", "取り込む行を選択してください。", parent=window
                )
                return
            record = row_by_iid.get(selected[0])
            if record is None:
                return
            self._apply_dupe_record(record)
            window.destroy()

        actions = ttk.Frame(window, padding=(12, 10, 12, 12))
        actions.pack(fill="x")
        ttk.Button(actions, text="キャンセル", command=window.destroy).pack(side="right")
        ttk.Button(
            actions,
            text="この行を適用",
            style="Accent.TButton",
            command=apply_selection,
        ).pack(side="right", padx=(0, 8))
        tree.bind("<Double-1>", lambda _event: apply_selection())
        tree.bind("<Return>", lambda _event: apply_selection())
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        window.bind("<Escape>", lambda _event: window.destroy())

        def _clear_picker(event: tk.Event) -> None:
            if event.widget is window and self._dupe_picker is window:
                self._dupe_picker = None

        window.bind("<Destroy>", _clear_picker)
        window.update_idletasks()
        parent_x = self.root.winfo_rootx()
        parent_y = self.root.winfo_rooty()
        width = max(680, window.winfo_reqwidth())
        height = max(320, window.winfo_reqheight())
        window.geometry(f"{width}x{height}+{parent_x + 40}+{parent_y + 60}")
        window.grab_set()
        tree.focus_set()

    def _apply_dupe_record(self, record: dict[str, Any]) -> None:
        """Fill Name/QTH/Code/GL (and empty freq / mode) from a prior QSO."""
        mapping = {
            "operator_name": record.get("operator_name", ""),
            "qth": record.get("qth", ""),
            "gridsquare": record.get("gridsquare", ""),
            "station_code": record.get("station_code", ""),
        }
        for key, value in mapping.items():
            if value:
                self.station_entry_vars[key].set(str(value))
        if not self.frequency_var.get().strip() and record.get("frequency"):
            self.frequency_var.set(str(record["frequency"]))
        if record.get("mode"):
            self.mode_var.set(str(record["mode"]))
        when = self._format_time(record.get("started_at"))
        self.qso_status_var.set(
            f"{record.get('callsign', '')} の前回値（{when}）を入力欄へ入れました"
        )

    def _set_capture_settings_state(self, state: str) -> None:
        """Enable or disable capture settings while recognition is running."""
        for widget in self._capture_setting_widgets:
            if state == "normal" and widget in (self.device_combo, self.model_combo):
                widget.configure(state="readonly")
            else:
                widget.configure(state=state)

    def _toggle_recognition(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            if self.stop_event is not None:
                self.stop_event.set()
            self.recognition_button.configure(text="停止中...", state="disabled")
            self.status_var.set("認識を停止しています")
            return

        try:
            threshold = float(self.threshold_var.get())
            silence_duration = float(self.silence_var.get())
            beam_size = int(self.beam_var.get())
            if threshold < 0 or silence_duration <= 0 or beam_size < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "設定エラー",
                "音量しきい値、無音終了時間、ビームサイズを確認してください",
            )
            return

        model_size = parse_model_choice(self.model_var.get())
        if not model_size:
            messagebox.showerror(
                "設定エラー",
                "取得済みの認識モデルがありません。設定画面からダウンロードしてください。",
            )
            return
        if not is_model_downloaded(model_size):
            messagebox.showerror(
                "設定エラー",
                f"モデル「{format_model_choice(model_size)}」が未取得です。"
                "設定画面からダウンロードしてください。",
            )
            return

        self.model_size = model_size
        self.beam_size = beam_size
        self._load_audio_devices(refresh_host=True)
        device = self._device_map.get(self.device_var.get())
        try:
            device = resolve_input_device(device)
        except Exception as exc:
            messagebox.showerror("入力デバイス", format_audio_error(exc))
            return

        self.stop_event = threading.Event()
        self._pending_error_dialog = None
        self.worker = RecognitionWorker(
            self.events,
            self.stop_event,
            self.model_size,
            self.language,
            self.beam_size,
            threshold,
            silence_duration,
            device,
        )
        self._set_capture_settings_state("disabled")
        self.recognition_button.configure(text="認識停止", style="Stop.TButton", state="normal")
        self.worker.start()
        if self.settings.popup_enabled:
            self.candidate_popup.show()
            self.candidate_popup.clear()
            self.status_var.set("認識中 · 候補ポップアップを表示しています")

    def _on_popup_toggle(self) -> None:
        self._set_popup_enabled(bool(self.popup_enabled_var.get()))

    def _set_popup_enabled(self, enabled: bool) -> None:
        self.settings.popup_enabled = enabled
        if hasattr(self, "popup_enabled_var"):
            self.popup_enabled_var.set(enabled)
        if enabled:
            self.candidate_popup.show()
        else:
            self.candidate_popup.hide(destroy=True)
        self._schedule_persist_settings()

    def _show_recognition_error_dialog(self, message: str) -> None:
        if self._pending_error_dialog != message:
            return
        self._pending_error_dialog = None
        messagebox.showerror("音声認識エラー", message, parent=self.root)

    def _poll_events(self) -> None:
        if self._poll_busy:
            self.root.after(100, self._poll_events)
            return
        self._poll_busy = True
        recognition_error: str | None = None
        saw_stopped = False
        try:
            try:
                while True:
                    event, payload = self.events.get_nowait()
                    if event == "status":
                        self.status_var.set(payload)
                    elif event == "transcript":
                        self._show_transcript(payload)
                    elif event == "callbook_result":
                        self._review_callbook_result(*payload)
                    elif event == "callbook_error":
                        qso_id, error = payload
                        if qso_id == self._session_qso_id():
                            self.callbook_status_var.set("局情報を取得できませんでした")
                            messagebox.showerror(
                                "局情報照会エラー", error, parent=self.root
                            )
                    elif event == "callbook_done":
                        self.settings_button.configure(state="normal")
                        if payload == self._session_qso_id():
                            self._refresh_callbook_button()
                    elif event == "error":
                        # Keep the first failure only; modal dialogs must not
                        # re-enter this drain loop and spam the operator.
                        if recognition_error is None:
                            recognition_error = str(payload)
                    elif event == "stopped":
                        saw_stopped = True
            except queue.Empty:
                pass

            if recognition_error is not None:
                if self.stop_event is not None:
                    self.stop_event.set()
                self.status_var.set("エラーが発生しました")
                self._pending_error_dialog = recognition_error
                self.root.after_idle(
                    lambda msg=recognition_error: self._show_recognition_error_dialog(msg)
                )

            if saw_stopped:
                self.status_var.set(
                    "エラーが発生しました" if recognition_error else "停止中"
                )
                self._set_capture_settings_state("normal")
                self.recognition_button.configure(
                    text="認識開始", style="Accent.TButton", state="normal"
                )

            self._process_model_download_events()

            if self.close_requested:
                if self.worker is None or not self.worker.is_alive():
                    self._finish_close()
                    return
        finally:
            self._poll_busy = False
            if not self.close_requested or (
                self.worker is not None and self.worker.is_alive()
            ):
                self.root.after(100, self._poll_events)

    def _make_transcript_selectable(self) -> None:
        """Allow selecting/copying transcript text while blocking edits."""
        self.transcript_text.bind("<Key>", self._on_transcript_key)
        self.transcript_text.bind("<<Paste>>", lambda _event: "break")
        self.transcript_text.bind("<<Cut>>", lambda _event: "break")
        self.transcript_text.bind("<<Clear>>", lambda _event: "break")

    def _on_transcript_key(self, event: tk.Event[Any]) -> str | None:
        key = event.keysym
        if key in {
            "Left",
            "Right",
            "Up",
            "Down",
            "Home",
            "End",
            "Prior",
            "Next",
            "Shift_L",
            "Shift_R",
            "Control_L",
            "Control_R",
            "Meta_L",
            "Meta_R",
            "Alt_L",
            "Alt_R",
            "Caps_Lock",
            "Escape",
            "Tab",
        }:
            return None
        # Allow copy / select-all with Ctrl or Command.
        if key.lower() in {"c", "a"} and event.state & (0x4 | 0x8 | 0x10 | 0x100000):
            return None
        return "break"

    def _show_transcript(self, result: dict[str, Any]) -> None:
        text = result["text"]
        callsigns = result["callsigns"]
        partial = result["partial"]
        self.transcript_text.insert("end", f"{result['time']}  {text}\n")
        if callsigns:
            self.transcript_text.insert("end", f"          候補: {', '.join(callsigns)}\n")
        elif partial:
            self.transcript_text.insert("end", f"          一部解読: {partial}\n")
        self.transcript_text.see("end")

        if callsigns:
            self.no_candidates_var.set("")
            self.empty_candidate_label.grid_remove()
            for callsign in callsigns:
                button = ttk.Button(
                    self.candidate_list,
                    text=f"{callsign}  ·  クリックして保存",
                    style="Accent.TButton",
                )
                button.configure(
                    command=lambda call=callsign, transcript=text, item=button: self._confirm_candidate(
                        call, transcript, item
                    )
                )
                button.grid(
                    row=len(self._candidate_buttons),
                    column=0,
                    sticky="ew",
                    pady=3,
                )
                self._candidate_buttons.append(button)
            while len(self._candidate_buttons) > 12:
                self._candidate_buttons.pop(0).destroy()
        else:
            self.no_candidates_var.set(
                f"一部解読: {partial}" if partial else "コールサイン候補なし"
            )
        self.candidate_popup.present_result(text, callsigns, partial)

    def _clear_live_results(self) -> None:
        self.transcript_text.delete("1.0", "end")
        for button in self._candidate_buttons:
            button.destroy()
        self._candidate_buttons.clear()
        self.no_candidates_var.set("認識結果を待っています")
        self.empty_candidate_label.grid()
        self.candidate_popup.clear()
        # Clear is display-only; recognition keeps running if the worker is alive.
        if self.worker is not None and self.worker.is_alive():
            self.status_var.set("認識中 · マイクを監視中（表示をクリアしました）")
        else:
            self.status_var.set("画面表示をクリアしました。保存済み履歴は保持されています")

    def _confirm_candidate(
        self,
        callsign: str,
        transcript: str,
        button: ttk.Button | None = None,
    ) -> None:
        try:
            candidate_id = self.store.save_candidate(
                callsign,
                transcript,
                qso_id=self.active_qso_id,
            )
        except Exception as exc:
            messagebox.showerror("保存エラー", str(exc))
            return

        if button is not None:
            button.configure(text=f"{callsign}  ·  保存済み", state="disabled")
        self.candidate_popup.mark_saved(callsign)
        # Mirror disabled state on main candidate buttons with the same callsign.
        for item in self._candidate_buttons:
            label = str(item.cget("text"))
            if label == callsign or label.startswith(f"{callsign} "):
                item.configure(text=f"{callsign}  ·  保存済み", state="disabled")
        self.qso_callsign_var.set(callsign)
        if self.active_qso_id is not None:
            self.qso_status_var.set(f"QSO記録中 · {callsign} を関連付けました")
        else:
            self.qso_status_var.set(f"コールサイン欄に {callsign} を入力しました（QSO未開始）")
        self.status_var.set(f"{callsign} を確認済み候補 #{candidate_id} として保存しました")
        self._refresh_candidate_history()
        self._refresh_dupe_status()

    def _apply_qsl_defaults(self) -> None:
        self.qsl_sent_var.set(
            normalize_qsl_sent_label(self.settings.default_qsl_sent)
        )
        self.qsl_received_var.set(
            normalize_qsl_received_label(self.settings.default_qsl_received)
        )
        self.qsl_via_var.set(self.settings.default_qsl_via)

    def _toggle_qso(self) -> None:
        if self.editing_qso_id is not None:
            self._save_history_edit()
            return
        if self.active_qso_id is None:
            started_at = datetime.now().astimezone()
            self._set_datetime_picker_state("normal")
            self._set_qso_datetime(started_at)
            self._set_datetime_picker_state("readonly")
            try:
                self.active_qso_id = self.store.start_qso(
                    frequency=self.frequency_var.get(),
                    mode=self.mode_var.get(),
                    started_at=started_at.isoformat(timespec="seconds"),
                )
            except Exception as exc:
                self._set_datetime_picker_state("disabled")
                messagebox.showerror("QSO開始エラー", str(exc))
                return
            self.qso_status_var.set(
                f"QSO記録中 · 開始 {datetime.now().astimezone().strftime('%H:%M:%S')}"
            )
            self.qso_button.configure(text="QSO終了", style="Stop.TButton")
            self._apply_qsl_defaults()
            self.dx_var.set(False)
            self.remarks2_text.delete("1.0", "end")
            self._clear_callbook_info("局情報未照会")
            self._refresh_callbook_button()
            self._refresh_time_zone_hint()
            self._refresh_history()
            return

        self._end_active_qso()

    def _has_hamqth_credentials(self) -> bool:
        try:
            load_hamqth_credentials()
            return True
        except CallbookError:
            return False

    def _callbook_button_label(self) -> str:
        if self._session_qso_id() is None:
            return "局情報照会"
        if self._has_hamqth_credentials():
            return "HamQTH照会"
        return "公開DXCC照会"

    def _refresh_callbook_button(self) -> None:
        if (
            self.callbook_lookup_thread is not None
            and self.callbook_lookup_thread.is_alive()
        ):
            return
        if self._session_qso_id() is None:
            self.callbook_lookup_button.configure(
                text="局情報照会", state="disabled"
            )
            return
        self.callbook_lookup_button.configure(
            text=self._callbook_button_label(),
            state="normal",
        )

    def _end_active_qso(self, *, closing: bool = False) -> bool:
        """End the open QSO. Return True when no open QSO remains."""
        if self.active_qso_id is None:
            return True
        try:
            hour, minute, second = (int(value.get()) for value in self.start_time_vars)
            started_at = datetime.combine(
                self.qso_start_date,
                datetime_time(hour=hour, minute=minute, second=second),
            ).astimezone()
        except ValueError:
            if closing:
                return self._resolve_failed_qso_close(
                    "開始日と時刻を確認できませんでした。"
                )
            messagebox.showerror(
                "日時の設定エラー",
                "開始日と時刻を確認してください。",
            )
            return False

        callbook_info = dict(self.callbook_data)
        callbook_info.update(
            {
                "operator_name": self.station_entry_vars["operator_name"].get(),
                "qth": self.station_entry_vars["qth"].get(),
                "gridsquare": self.station_entry_vars["gridsquare"].get(),
                "station_code": self.station_entry_vars["station_code"].get(),
            }
        )
        notes = self.notes_text.get("1.0", "end-1c")
        if closing and "[アプリ終了時にQSOを終了]" not in notes:
            notes = f"{notes.rstrip()}\n[アプリ終了時にQSOを終了]".strip()
            self.notes_text.delete("1.0", "end")
            self.notes_text.insert("1.0", notes)

        try:
            self.store.end_qso(
                self.active_qso_id,
                self.qso_callsign_var.get(),
                self.frequency_var.get(),
                self.mode_var.get(),
                notes,
                started_at=started_at.isoformat(timespec="seconds"),
                received_report=self.received_report_var.get(),
                sent_report=self.sent_report_var.get(),
                callbook_info=callbook_info,
                qsl_sent=_QSL_SENT_CODES.get(self.qsl_sent_var.get(), ""),
                qsl_received=_QSL_RECEIVED_CODES.get(self.qsl_received_var.get(), ""),
                qsl_via=self.qsl_via_var.get(),
                remarks2=self.remarks2_text.get("1.0", "end-1c"),
                is_dx=bool(self.dx_var.get()),
            )
        except Exception as exc:
            if closing:
                return self._resolve_failed_qso_close(str(exc))
            messagebox.showerror("QSO保存エラー", str(exc))
            return False

        self._clear_active_qso_ui()
        return True

    def _resolve_failed_qso_close(self, error: str) -> bool:
        """Ask what to do when ending a QSO fails during app quit."""
        choice = messagebox.askyesnocancel(
            "QSOが未保存です",
            "QSOの保存に失敗したため、まだ記録中のままです。\n\n"
            f"{error}\n\n"
            "はい: もう一度保存して終了\n"
            "いいえ: 強制終了してアプリを閉じる（不完全なログになることがあります）\n"
            "キャンセル: アプリを閉じない",
            parent=self.root,
        )
        if choice is None:
            return False
        if choice:
            return self._end_active_qso(closing=True)
        qso_id = self.active_qso_id
        if qso_id is None:
            return True
        try:
            self.store.force_end_qso(
                qso_id, note="[アプリ終了時に強制終了（通常保存失敗）]"
            )
        except Exception as exc:
            messagebox.showerror(
                "QSO強制終了エラー",
                "強制終了にも失敗したため、アプリを閉じません。\n" + str(exc),
                parent=self.root,
            )
            return False
        self._clear_active_qso_ui()
        return True

    def _clear_active_qso_ui(self) -> None:
        self.active_qso_id = None
        if self.editing_qso_id is None:
            self.dx_var.set(False)
            self._set_datetime_picker_state("disabled")
            self.qso_status_var.set("QSO未開始")
            self.qso_button.configure(text="QSO開始", style="Accent.TButton")
            self.qso_edit_cancel_button.configure(state="disabled")
        self._refresh_callbook_button()
        self._refresh_history()
        self._refresh_candidate_history()
        self._refresh_time_zone_hint()

    def _set_qso_datetime(self, value: datetime) -> None:
        self.qso_start_date = value.date()
        self.date_picker_button.configure(text=self.qso_start_date.isoformat())
        for variable, part in zip(
            self.start_time_vars, (value.hour, value.minute, value.second)
        ):
            variable.set(f"{part:02d}")
        self._update_time_display()

    def _set_datetime_picker_state(self, state: str) -> None:
        self.date_picker_button.configure(state=state)
        self.time_picker_button.configure(state=state)

    def _open_date_picker(self) -> None:
        if self._session_qso_id() is None:
            return
        window = tk.Toplevel(self.root)
        window.title("開始日を選択")
        window.transient(self.root)
        calendar = Calendar(
            window,
            selectmode="day",
            locale="ja_JP",
            firstweekday="sunday",
            showweeknumbers=False,
        )
        calendar.selection_set(self.qso_start_date)
        calendar.pack(padx=12, pady=12)

        actions = ttk.Frame(window, padding=(12, 0, 12, 12))
        actions.pack(fill="x")
        ttk.Button(actions, text="キャンセル", command=window.destroy).pack(side="right")
        ttk.Button(
            actions,
            text="日付を選択",
            style="Accent.TButton",
            command=lambda: self._apply_selected_date(calendar, window),
        ).pack(side="right", padx=(0, 8))
        window.grab_set()

    def _apply_selected_date(self, calendar: Calendar, window: tk.Toplevel) -> None:
        selected_date = calendar.selection_get()
        if selected_date is not None:
            self.qso_start_date = selected_date
            self.date_picker_button.configure(text=selected_date.isoformat())
            self._refresh_time_zone_hint()
        window.destroy()

    def _open_time_picker(self) -> None:
        if self._session_qso_id() is None:
            return
        initial_time = tuple(int(value.get()) for value in self.start_time_vars)
        ClockTimePicker(self.root, initial_time, self._apply_selected_time)

    def _apply_selected_time(self, hour: int, minute: int, second: int) -> None:
        for current, selected in zip(
            self.start_time_vars, (hour, minute, second)
        ):
            current.set(f"{selected:02d}")
        self._update_time_display()

    def _update_time_display(self) -> None:
        if hasattr(self, "time_display_var"):
            self.time_display_var.set(
                ":".join(value.get() for value in self.start_time_vars)
            )
        self._refresh_time_zone_hint()

    def _current_qso_start_datetime(self) -> datetime | None:
        if not hasattr(self, "start_time_vars"):
            return None
        try:
            hour, minute, second = (int(value.get()) for value in self.start_time_vars)
            return datetime.combine(
                self.qso_start_date,
                datetime_time(hour=hour, minute=minute, second=second),
            ).astimezone()
        except (ValueError, AttributeError):
            return None

    def _refresh_time_zone_hint(self) -> None:
        if not hasattr(self, "time_zone_hint_var"):
            return
        moment = self._current_qso_start_datetime()
        if moment is None:
            self.time_zone_hint_var.set(
                "ADIF出力はUTC、HAMLOG CSVはJSTで書き出します"
            )
            return
        self.time_zone_hint_var.set(format_dual_hint(moment))

    def _schedule_history_refresh(self, _event: tk.Event[Any] | None = None) -> None:
        if self._history_after_id is not None:
            self.root.after_cancel(self._history_after_id)
        self._history_after_id = self.root.after(250, self._refresh_history)

    def _history_filter_kwargs(self) -> dict[str, Any]:
        qsl_label = (
            self.history_qsl_var.get() if hasattr(self, "history_qsl_var") else "すべて"
        )
        qsl_map = {
            "未送信": "sent_pending",
            "未受領": "recv_pending",
            "送信済": "sent",
            "受領済": "recv",
            "送信待ち": "queued",
            "請求中": "requested",
        }
        return {
            "mode": (
                self.history_mode_var.get()
                if hasattr(self, "history_mode_var")
                else "すべて"
            ),
            "dx_only": bool(
                self.history_dx_only_var.get()
                if hasattr(self, "history_dx_only_var")
                else False
            ),
            "qsl": qsl_map.get(qsl_label, ""),
            "date_from": (
                self.history_date_from_var.get().strip()
                if hasattr(self, "history_date_from_var")
                else ""
            ),
            "date_to": (
                self.history_date_to_var.get().strip()
                if hasattr(self, "history_date_to_var")
                else ""
            ),
        }

    def _clear_history_filters(self) -> None:
        if hasattr(self, "history_search_var"):
            self.history_search_var.set("")
        if hasattr(self, "history_mode_var"):
            self.history_mode_var.set("すべて")
        if hasattr(self, "history_dx_only_var"):
            self.history_dx_only_var.set(False)
        if hasattr(self, "history_qsl_var"):
            self.history_qsl_var.set("すべて")
        if hasattr(self, "history_date_from_var"):
            self.history_date_from_var.set("")
        if hasattr(self, "history_date_to_var"):
            self.history_date_to_var.set("")
        self._refresh_history()

    def _history_row_is_active(self, iid: str) -> bool:
        return self.active_qso_id is not None and str(self.active_qso_id) == iid

    def _history_row_is_protected(self, iid: str) -> bool:
        return self._history_row_is_active(iid) or iid in self._history_locked

    def _history_lock_mark(self, iid: str) -> str:
        if self._history_row_is_active(iid):
            return "記録中"
        if iid in self._history_locked:
            return "🔒"
        return ""

    def _history_select_mark(self, iid: str) -> str:
        if not self._history_delete_mode:
            return ""
        if self._history_row_is_protected(iid):
            return "—"
        return "☑" if iid in self._history_checked else "☐"

    def _update_history_delete_controls(self) -> None:
        if not hasattr(self, "history_delete_button"):
            return
        enabled = "normal" if self._history_delete_mode else "disabled"
        self.history_select_all_button.configure(state=enabled)
        self.history_clear_selection_button.configure(state=enabled)
        deletable = {
            iid for iid in self._history_checked if not self._history_row_is_protected(iid)
        }
        self._history_checked = deletable
        has_selection = bool(deletable) and self._history_delete_mode
        self.history_delete_button.configure(
            state="normal" if has_selection else "disabled"
        )
        if not self._history_delete_mode:
            self.history_delete_mode_button.configure(text="削除モード")
            self.history_delete_status_var.set(
                "保護列クリック、または ⌘/Ctrl+クリックで複数行→ロック"
            )
            self.history_tree.column("select", width=0, minwidth=0, stretch=False)
            return
        self.history_delete_mode_button.configure(text="削除モード終了")
        self.history_tree.column("select", width=44, minwidth=40, stretch=False)
        count = len(deletable)
        self.history_delete_status_var.set(
            f"削除モード中 · {count}件選択（行クリックで飛び飛び選択可）"
            if count
            else "削除モード中 · 行クリックで□選択（ロック／記録中は —）"
        )

    def _toggle_history_delete_mode(self) -> None:
        self._history_delete_mode = not self._history_delete_mode
        if not self._history_delete_mode:
            self._history_checked.clear()
        self._refresh_history_row_marks()
        self._update_history_delete_controls()

    def _refresh_history_row_marks(self) -> None:
        if not hasattr(self, "history_tree"):
            return
        visible = set(self.history_tree.get_children())
        self._history_checked = {
            iid
            for iid in (self._history_checked & visible)
            if not self._history_row_is_protected(iid)
        }
        for iid in visible:
            values = list(self.history_tree.item(iid, "values"))
            if len(values) < 2:
                continue
            values[0] = self._history_select_mark(iid)
            values[1] = self._history_lock_mark(iid)
            self.history_tree.item(iid, values=values)

    def _on_history_tree_click(self, event: tk.Event) -> str | None:
        region = self.history_tree.identify_region(event.x, event.y)
        if region not in {"cell", "tree"}:
            return None
        column = self.history_tree.identify_column(event.x)
        iid = self.history_tree.identify_row(event.y)
        if not iid:
            return None
        # "#1" select, "#2" lock
        if column == "#2":
            self._toggle_history_lock(iid)
            return "break"
        if not self._history_delete_mode:
            # Allow native extended selection (⌘/Ctrl discontinuous, Shift range).
            return None
        # Delete mode: click any cell (except lock) to toggle ☐/☑ on that row.
        if self._history_row_is_protected(iid):
            reason = (
                "記録中のQSOは削除できません"
                if self._history_row_is_active(iid)
                else "ロック中のQSOは削除できません（先にロック解除）"
            )
            self.history_delete_status_var.set(reason)
            return "break"
        if iid in self._history_checked:
            self._history_checked.discard(iid)
        else:
            self._history_checked.add(iid)
        values = list(self.history_tree.item(iid, "values"))
        if values:
            values[0] = self._history_select_mark(iid)
            self.history_tree.item(iid, values=values)
        self._update_history_delete_controls()
        return "break"

    def _toggle_history_lock(self, iid: str) -> None:
        if self._history_row_is_active(iid):
            self.history_delete_status_var.set("記録中のQSOはロック操作できません")
            return
        locked = iid not in self._history_locked
        try:
            self.store.set_qso_locked([int(iid)], locked)
        except Exception as exc:
            messagebox.showerror("ロックエラー", str(exc), parent=self.root)
            return
        if locked:
            self._history_locked.add(iid)
            self._history_checked.discard(iid)
        else:
            self._history_locked.discard(iid)
        self._refresh_history_row_marks()
        self._update_history_delete_controls()
        self.history_delete_status_var.set(
            "ロックしました（削除モードで選択不可）"
            if locked
            else "ロックを解除しました"
        )

    def _set_history_lock_for_selection(self, locked: bool) -> None:
        # Prefer tree multi-selection; in delete mode also accept ☐-checked rows.
        selected = list(self.history_tree.selection())
        if not selected and self._history_delete_mode:
            selected = sorted(self._history_checked)
        if not selected:
            messagebox.showinfo(
                "ロック",
                "履歴の行を選んでからロック／解除してください。\n"
                "（⌘/Ctrl+クリックで飛び飛び複数選択できます）",
                parent=self.root,
            )
            return
        targets = [
            iid for iid in selected if not self._history_row_is_active(iid)
        ]
        skipped_active = len(selected) - len(targets)
        if not targets:
            messagebox.showinfo(
                "ロック",
                "記録中のQSOはロック操作できません。",
                parent=self.root,
            )
            return
        try:
            updated = self.store.set_qso_locked([int(iid) for iid in targets], locked)
        except Exception as exc:
            messagebox.showerror("ロックエラー", str(exc), parent=self.root)
            return
        if locked:
            self._history_locked.update(targets)
            self._history_checked -= set(targets)
        else:
            self._history_locked -= set(targets)
        self._refresh_history_row_marks()
        self._update_history_delete_controls()
        note = f"{updated}件を{'ロック' if locked else 'ロック解除'}しました"
        if skipped_active:
            note += f"（記録中 {skipped_active}件は対象外）"
        self.history_delete_status_var.set(note)

    def _select_all_history_rows(self) -> None:
        if not self._history_delete_mode:
            return
        self._history_checked = {
            iid
            for iid in self.history_tree.get_children()
            if not self._history_row_is_protected(iid)
        }
        self._refresh_history_row_marks()
        self._update_history_delete_controls()

    def _clear_history_selection(self) -> None:
        self._history_checked.clear()
        self._refresh_history_row_marks()
        self._update_history_delete_controls()

    def _delete_selected_history_rows(self) -> None:
        if not self._history_delete_mode:
            return
        ids = sorted(
            int(iid)
            for iid in self._history_checked
            if not self._history_row_is_protected(iid)
        )
        if not ids:
            return
        if not messagebox.askyesno(
            "QSO履歴の削除",
            f"選択した {len(ids)} 件のQSOを削除します。\n"
            "この操作は取り消せません。よろしいですか？",
            parent=self.root,
        ):
            return
        try:
            deleted = self.store.delete_qsos(ids)
        except Exception as exc:
            messagebox.showerror("削除エラー", str(exc), parent=self.root)
            return
        self._history_checked.clear()
        self._refresh_history()
        self._refresh_candidate_history()
        self.history_delete_status_var.set(f"{deleted}件を削除しました")
        messagebox.showinfo("QSO履歴の削除", f"{deleted}件を削除しました。", parent=self.root)

    def _on_history_tree_double_click(self, event: tk.Event) -> None:
        if self._history_delete_mode:
            return
        iid = self.history_tree.identify_row(event.y)
        if iid:
            self._start_history_edit(iid)

    def _edit_selected_history_row(self) -> None:
        if self._history_delete_mode:
            messagebox.showinfo(
                "履歴の編集",
                "削除モード中は編集できません。削除モードを終了してください。",
                parent=self.root,
            )
            return
        selected = list(self.history_tree.selection())
        if not selected:
            messagebox.showinfo(
                "履歴の編集",
                "編集する行を選んでください（ダブルクリックでも可）。",
                parent=self.root,
            )
            return
        if len(selected) > 1:
            messagebox.showinfo(
                "履歴の編集",
                "編集は1件ずつです。1行だけ選んでください。",
                parent=self.root,
            )
            return
        self._start_history_edit(selected[0])

    def _start_history_edit(self, iid: str) -> None:
        if self.active_qso_id is not None:
            messagebox.showwarning(
                "履歴の編集",
                "QSO記録中は履歴を編集できません。先にQSOを終了してください。",
                parent=self.root,
            )
            return
        if self.editing_qso_id is not None and str(self.editing_qso_id) != iid:
            if not messagebox.askyesno(
                "履歴の編集",
                "別のQSOを編集中です。破棄して切り換えますか？",
                parent=self.root,
            ):
                return
        try:
            row = self.store.get_qso(int(iid))
        except Exception as exc:
            messagebox.showerror("履歴の編集", str(exc), parent=self.root)
            return
        if row is None:
            messagebox.showerror("履歴の編集", "QSOが見つかりません。", parent=self.root)
            return
        self._load_qso_into_form(row)
        self.editing_qso_id = int(row["id"])
        self._editing_ended_at = row.get("ended_at")
        self._set_datetime_picker_state("readonly")
        self.qso_button.configure(text="編集を保存", style="Accent.TButton")
        self.qso_edit_cancel_button.configure(state="normal")
        self._refresh_callbook_button()
        self.qso_status_var.set(f"履歴編集中 · QSO #{self.editing_qso_id}")
        self.notebook.select(self.monitor_tab)
        self.status_var.set(
            f"QSO #{self.editing_qso_id} を編集中です。「編集を保存」で更新します"
        )

    def _load_qso_into_form(self, row: dict[str, Any]) -> None:
        self.qso_callsign_var.set(row.get("callsign") or "")
        self.frequency_var.set(row.get("frequency") or "")
        self.mode_var.set(row.get("mode") or "FM")
        self.received_report_var.set(row.get("received_report") or "")
        self.sent_report_var.set(row.get("sent_report") or "")
        self.qsl_sent_var.set(
            _QSL_SENT_LABELS.get(row.get("qsl_sent") or "", "未設定")
        )
        self.qsl_received_var.set(
            _QSL_RECEIVED_LABELS.get(row.get("qsl_received") or "", "未設定")
        )
        self.qsl_via_var.set(row.get("qsl_via") or "")
        self.dx_var.set(str(row.get("is_dx") or "") == "1")
        started = row.get("started_at")
        if started:
            try:
                moment = datetime.fromisoformat(str(started))
                if moment.tzinfo is None:
                    moment = moment.astimezone()
                self._set_datetime_picker_state("normal")
                self._set_qso_datetime(moment.astimezone())
            except ValueError:
                pass
        self.station_entry_vars["operator_name"].set(row.get("operator_name") or "")
        self.station_entry_vars["qth"].set(row.get("qth") or "")
        self.station_entry_vars["gridsquare"].set(row.get("gridsquare") or "")
        self.station_entry_vars["station_code"].set(row.get("station_code") or "")
        self.callbook_data = {
            "callsign": row.get("callsign") or "",
            "operator_name": row.get("operator_name") or "",
            "qth": row.get("qth") or "",
            "country": row.get("country") or "",
            "dxcc": row.get("dxcc") or "",
            "gridsquare": row.get("gridsquare") or "",
            "cq_zone": row.get("cq_zone") or "",
            "itu_zone": row.get("itu_zone") or "",
            "dxcc_details": row.get("dxcc_details") or "",
            "continent": row.get("continent") or "",
            "lookup_provider": row.get("lookup_provider") or "",
            "lookup_at": row.get("lookup_at") or "",
            "station_code": row.get("station_code") or "",
        }
        self.callbook_display_vars["country"].set(row.get("country") or "—")
        self.callbook_display_vars["dxcc"].set(row.get("dxcc") or "—")
        zones = " / ".join(
            part
            for part in (row.get("cq_zone") or "", row.get("itu_zone") or "")
            if part
        )
        self.callbook_display_vars["zones"].set(zones or "—")
        self.callbook_display_vars["dxcc_details"].set(row.get("dxcc_details") or "—")
        self.callbook_display_vars["continent"].set(row.get("continent") or "—")
        if row.get("lookup_provider"):
            self.callbook_status_var.set(
                f"保存済み局情報（{row.get('lookup_provider')}）"
            )
        else:
            self.callbook_status_var.set("履歴から読み込みました")
        self.notes_text.delete("1.0", "end")
        self.notes_text.insert("1.0", row.get("notes") or "")
        self.remarks2_text.delete("1.0", "end")
        self.remarks2_text.insert("1.0", row.get("remarks2") or "")
        self._refresh_jcc_helper()
        self._refresh_dupe_status()
        self._refresh_time_zone_hint()

    def _save_history_edit(self, *, quiet: bool = False) -> bool:
        if self.editing_qso_id is None:
            return True
        try:
            hour, minute, second = (int(value.get()) for value in self.start_time_vars)
            started_at = datetime.combine(
                self.qso_start_date,
                datetime_time(hour=hour, minute=minute, second=second),
            ).astimezone()
        except ValueError:
            messagebox.showerror(
                "日時の設定エラー",
                "開始日と時刻を確認してください。",
                parent=self.root,
            )
            return False
        callbook_info = dict(self.callbook_data)
        callbook_info.update(
            {
                "operator_name": self.station_entry_vars["operator_name"].get(),
                "qth": self.station_entry_vars["qth"].get(),
                "gridsquare": self.station_entry_vars["gridsquare"].get(),
                "station_code": self.station_entry_vars["station_code"].get(),
            }
        )
        ended_at = self._editing_ended_at
        if not ended_at:
            ended_at = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            self.store.update_qso(
                self.editing_qso_id,
                {
                    "started_at": started_at.isoformat(timespec="seconds"),
                    "ended_at": ended_at,
                    "callsign": self.qso_callsign_var.get(),
                    "frequency": self.frequency_var.get(),
                    "mode": self.mode_var.get(),
                    "received_report": self.received_report_var.get(),
                    "sent_report": self.sent_report_var.get(),
                    "operator_name": callbook_info.get("operator_name", ""),
                    "qth": callbook_info.get("qth", ""),
                    "country": callbook_info.get("country", ""),
                    "dxcc": callbook_info.get("dxcc", ""),
                    "gridsquare": callbook_info.get("gridsquare", ""),
                    "cq_zone": callbook_info.get("cq_zone", ""),
                    "itu_zone": callbook_info.get("itu_zone", ""),
                    "dxcc_details": callbook_info.get("dxcc_details", ""),
                    "continent": callbook_info.get("continent", ""),
                    "lookup_provider": callbook_info.get("lookup_provider", ""),
                    "lookup_at": callbook_info.get("lookup_at", ""),
                    "station_code": callbook_info.get("station_code", ""),
                    "qsl_sent": _QSL_SENT_CODES.get(self.qsl_sent_var.get(), ""),
                    "qsl_received": _QSL_RECEIVED_CODES.get(
                        self.qsl_received_var.get(), ""
                    ),
                    "qsl_via": self.qsl_via_var.get(),
                    "notes": self.notes_text.get("1.0", "end-1c"),
                    "remarks2": self.remarks2_text.get("1.0", "end-1c"),
                    "is_dx": bool(self.dx_var.get()),
                },
            )
        except Exception as exc:
            messagebox.showerror("履歴の保存エラー", str(exc), parent=self.root)
            return False
        edited_id = self.editing_qso_id
        self._finish_history_edit_ui()
        self._refresh_history()
        self._refresh_candidate_history()
        self.status_var.set(f"QSO #{edited_id} を更新しました")
        if not quiet:
            messagebox.showinfo(
                "履歴の編集",
                f"QSO #{edited_id} を保存しました。",
                parent=self.root,
            )
        return True

    def _cancel_history_edit(self) -> None:
        if self.editing_qso_id is None:
            return
        if not messagebox.askyesno(
            "編集取消",
            "編集内容を破棄しますか？",
            parent=self.root,
        ):
            return
        self._finish_history_edit_ui()
        self.status_var.set("履歴編集を取り消しました")

    def _finish_history_edit_ui(self) -> None:
        self.editing_qso_id = None
        self._editing_ended_at = None
        self._set_datetime_picker_state("disabled")
        self.qso_button.configure(text="QSO開始", style="Accent.TButton")
        self.qso_edit_cancel_button.configure(state="disabled")
        self.qso_status_var.set("QSO未開始")
        self._refresh_callbook_button()
        self._refresh_time_zone_hint()

    def _refresh_history(self) -> None:
        self._history_after_id = None
        if not hasattr(self, "history_tree"):
            return
        previous_checked = set(self._history_checked)
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        query = self.history_search_var.get() if hasattr(self, "history_search_var") else ""
        rows = self.store.search_qsos(query, **self._history_filter_kwargs())
        visible_ids = {str(row["id"]) for row in rows}
        self._history_locked = {
            str(row["id"])
            for row in rows
            if str(row.get("is_locked") or "") == "1"
        }
        self._history_checked = {
            iid
            for iid in (previous_checked & visible_ids)
            if not self._history_row_is_protected(iid)
        }
        for row in rows:
            iid = str(row["id"])
            self.history_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    self._history_select_mark(iid),
                    self._history_lock_mark(iid),
                    self._format_time(row["started_at"]),
                    self._format_time(row["ended_at"]),
                    row["callsign"],
                    "DX" if str(row.get("is_dx") or "") == "1" else "",
                    row["operator_name"],
                    row["qth"],
                    row["country"],
                    row["dxcc_details"],
                    row["continent"],
                    row["gridsquare"],
                    row["station_code"],
                    row["frequency"],
                    row["mode"],
                    row["received_report"],
                    row["sent_report"],
                    _QSL_SENT_LABELS.get(row["qsl_sent"], row["qsl_sent"] or "未設定"),
                    _QSL_RECEIVED_LABELS.get(
                        row["qsl_received"], row["qsl_received"] or "未設定"
                    ),
                    row["qsl_via"],
                    row["notes"],
                    row["remarks2"],
                ),
            )
        if hasattr(self, "history_count_var"):
            self.history_count_var.set(f"{len(rows)}件")
        self._update_history_delete_controls()

    def _export_history_adif(self) -> None:
        destination = filedialog.asksaveasfilename(
            parent=self.root,
            title="ADIFログを書き出す",
            defaultextension=".adi",
            filetypes=(
                ("ADI (ASCII)", "*.adi"),
                ("ADI (UTF-8 / 日本語保持)", "*.adi"),
                ("ADX (XML / UTF-8)", "*.adx"),
                ("すべてのファイル", "*"),
            ),
            initialfile="ham_voice_logger.adi",
        )
        if not destination:
            return

        query = self.history_search_var.get()
        records = self.store.search_qsos(
            query, limit=100_000, **self._history_filter_kwargs()
        )
        path = destination.lower()
        try:
            if path.endswith(".adx"):
                result = export_adx(records, destination)
            elif "utf" in path or path.endswith(".adi"):
                # File dialog type is not returned; detect UTF-8 intent by
                # asking when the path is .adi (default ASCII unless confirmed).
                allow_unicode = bool(
                    messagebox.askyesno(
                        "ADIF出力形式",
                        "日本語の Name / QTH などを残す UTF-8 ADI にしますか？\n\n"
                        "はい: UTF-8 ADI（日本語保持）\n"
                        "いいえ: 従来の ASCII ADI（非ASCIIは省略）\n\n"
                        "ADX (XML) にする場合は拡張子を .adx にしてください。",
                        parent=self.root,
                    )
                )
                result = export_adif(
                    records, destination, allow_unicode=allow_unicode
                )
            else:
                result = export_adif(records, destination, allow_unicode=False)
        except (OSError, ValueError) as exc:
            messagebox.showerror("ADIF出力エラー", str(exc))
            return

        message = (
            f"{result.exported}件を書き出しました（現在の検索条件）。\n"
            "日時はUTCです。DXフラグ付きは COMMENT に DX を付与します。"
        )
        if result.skipped_incomplete:
            message += f"\n未完了またはコールサイン未入力の{result.skipped_incomplete}件は除外しました。"
        if result.omitted_non_ascii_fields:
            message += (
                f"\nADIはASCII形式のため、日本語などを含む"
                f"{result.omitted_non_ascii_fields}項目を省略しました。"
            )
        messagebox.showinfo("ADIF出力", message)

    def _export_history_hamlog_csv(self) -> None:
        destination = filedialog.asksaveasfilename(
            parent=self.root,
            title="Turbo HAMLOG用CSVを書き出す",
            defaultextension=".csv",
            filetypes=(("CSVファイル", "*.csv"), ("すべてのファイル", "*")),
            initialfile="ham_voice_logger_hamlog.csv",
        )
        if not destination:
            return

        query = self.history_search_var.get()
        records = self.store.search_qsos(
            query, limit=100_000, **self._history_filter_kwargs()
        )
        try:
            result = export_hamlog_csv(records, destination)
        except (OSError, ValueError, UnicodeError) as exc:
            messagebox.showerror("HAMLOG CSV出力エラー", str(exc))
            return

        message = (
            f"{result.exported}件を書き出しました（現在の検索条件）。\n"
            "日時はJST（HH:MMJ）です。DXフラグ付きは Remarks1 先頭に [DX] を付けます。\n"
            "Turbo HAMLOG の「データのインポート」→「CSVファイルから」で取り込めます。"
            "\n※「データに上書き」は使わず、追加で取り込んでください。"
        )
        if result.skipped_incomplete:
            message += (
                f"\n未完了またはコールサイン未入力の"
                f"{result.skipped_incomplete}件は除外しました。"
            )
        messagebox.showinfo("HAMLOG CSV出力", message)

    def _import_history_adif(self) -> None:
        source = filedialog.askopenfilename(
            parent=self.root,
            title="ADIFログを取り込む",
            filetypes=(
                ("ADIFファイル", "*.adi *.ADI *.adif *.ADIF"),
                ("すべてのファイル", "*"),
            ),
        )
        if not source:
            return
        try:
            result = import_adif(source, self.store, skip_duplicates=True)
        except (OSError, ValueError, UnicodeError) as exc:
            messagebox.showerror("ADIF取込エラー", str(exc))
            return
        self._refresh_history()
        messagebox.showinfo(
            "ADIF取込",
            f"{result.imported}件を取り込みました。\n"
            f"不完全 {result.skipped_incomplete}件 / "
            f"重複スキップ {result.skipped_duplicate}件",
        )

    def _import_history_hamlog_csv(self) -> None:
        source = filedialog.askopenfilename(
            parent=self.root,
            title="HAMLOG CSVを取り込む",
            filetypes=(("CSVファイル", "*.csv *.CSV"), ("すべてのファイル", "*")),
        )
        if not source:
            return
        try:
            result = import_hamlog_csv(source, self.store, skip_duplicates=True)
        except (OSError, ValueError, UnicodeError) as exc:
            messagebox.showerror("HAMLOG取込エラー", str(exc))
            return
        self._refresh_history()
        messagebox.showinfo(
            "HAMLOG取込",
            f"{result.imported}件を取り込みました。\n"
            f"不完全 {result.skipped_incomplete}件 / "
            f"重複スキップ {result.skipped_duplicate}件\n"
            "※ Turbo HAMLOG の複合検索CSV（14列、または先頭にレコード番号）向けです。",
        )

    def _refresh_candidate_history(self) -> None:
        if not hasattr(self, "candidate_tree"):
            return
        for item in self.candidate_tree.get_children():
            self.candidate_tree.delete(item)
        for row in self.store.recent_candidates():
            self.candidate_tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(
                    self._format_time(row["confirmed_at"]),
                    row["callsign"],
                    row["transcript"],
                    row["qso_id"] or "",
                ),
            )

    def _format_time(self, value: str | None) -> str:
        mode = normalize_time_display(
            getattr(self.settings, "time_display", "both")
        )
        return format_timestamp(value, mode)

    def _on_close(self) -> None:
        self.close_requested = True
        self._persist_settings()
        if self.worker is not None and self.worker.is_alive():
            if self.stop_event is not None:
                self.stop_event.set()
            self.status_var.set("終了処理中")
            return
        self._finish_close()

    def _on_root_configure(self, event: tk.Event) -> None:
        if event.widget is not self.root:
            return
        if not self._geometry_persist_enabled:
            return
        self._schedule_persist_settings()

    def _schedule_persist_settings(self, *_args: Any) -> None:
        if self.close_requested:
            return
        if self._persist_after_id is not None:
            try:
                self.root.after_cancel(self._persist_after_id)
            except tk.TclError:
                pass
        try:
            self._persist_after_id = self.root.after(500, self._persist_settings)
        except tk.TclError:
            self._persist_settings()

    def _collect_settings(self, *, include_geometry: bool = True) -> AppSettings:
        """Snapshot current UI preferences for persistence."""
        model = self.model_size
        if hasattr(self, "model_var"):
            model = parse_model_choice(self.model_var.get()) or self.model_size
        try:
            beam_size = int(self.beam_var.get()) if hasattr(self, "beam_var") else self.beam_size
        except ValueError:
            beam_size = self.beam_size
        try:
            threshold = (
                float(self.threshold_var.get())
                if hasattr(self, "threshold_var")
                else self.settings.threshold
            )
        except ValueError:
            threshold = self.settings.threshold
        try:
            silence_duration = (
                float(self.silence_var.get())
                if hasattr(self, "silence_var")
                else self.settings.silence_duration
            )
        except ValueError:
            silence_duration = self.settings.silence_duration

        device_label = self.device_var.get() if hasattr(self, "device_var") else "システム既定"
        device_index = self._device_map.get(device_label)
        device_name = ""
        if device_label and device_label != "システム既定" and ": " in device_label:
            device_name = device_label.split(": ", 1)[1]

        width = self.settings.window_width
        height = self.settings.window_height
        x = self.settings.window_x
        y = self.settings.window_y
        if include_geometry and (
            self._geometry_persist_enabled or self.close_requested
        ):
            current = self._read_window_geometry()
            if current is not None:
                width, height, x, y = current

        last_frequency = (
            unicodedata.normalize("NFKC", self.frequency_var.get()).strip()
            if hasattr(self, "frequency_var")
            else self.settings.last_frequency
        )
        last_mode = (
            (self.mode_var.get() or "FM").strip() or "FM"
            if hasattr(self, "mode_var")
            else self.settings.last_mode
        )
        return AppSettings(
            model=normalize_model_key(model),
            language=self.language,
            beam_size=max(1, min(beam_size, 10)),
            threshold=max(0.0, threshold),
            silence_duration=silence_duration if silence_duration > 0 else 1.0,
            device_index=device_index,
            device_name=device_name,
            window_width=width,
            window_height=height,
            window_x=x,
            window_y=y,
            last_frequency=last_frequency,
            last_mode=last_mode,
            freq_mode_presets=normalize_freq_mode_presets(
                self.settings.freq_mode_presets
            ),
            report_presets=normalize_report_presets(self.settings.report_presets),
            time_display=normalize_time_display(self.settings.time_display),
            default_qsl_sent=normalize_qsl_sent_label(
                self.settings.default_qsl_sent
            ),
            default_qsl_received=normalize_qsl_received_label(
                self.settings.default_qsl_received
            ),
            default_qsl_via=str(self.settings.default_qsl_via or "").strip(),
            popup_enabled=bool(
                self.popup_enabled_var.get()
                if hasattr(self, "popup_enabled_var")
                else self.settings.popup_enabled
            ),
            popup_width=self.settings.popup_width,
            popup_height=self.settings.popup_height,
            popup_x=self.settings.popup_x,
            popup_y=self.settings.popup_y,
        )

    def _persist_settings(self) -> None:
        self._persist_after_id = None
        try:
            self.settings = self._collect_settings()
            save_settings(self.settings)
            self._settings_saved = True
        except Exception as exc:
            print(f"設定の保存に失敗しました: {exc}", file=sys.stderr)

    def _persist_settings_atexit(self) -> None:
        if self._settings_saved:
            return
        try:
            self._persist_settings()
        except Exception:
            try:
                save_settings(self.settings)
            except Exception:
                pass

    def _finish_close(self) -> None:
        self.candidate_popup.hide(destroy=True)
        self._persist_settings()
        if self.editing_qso_id is not None:
            choice = messagebox.askyesnocancel(
                "履歴編集中です",
                "履歴を編集中です。保存してから終了しますか？\n\n"
                "はい: 保存して終了\n"
                "いいえ: 破棄して終了\n"
                "キャンセル: アプリを閉じない",
                parent=self.root,
            )
            if choice is None:
                self.close_requested = False
                self.recognition_button.configure(state="normal")
                self._set_capture_settings_state("normal")
                self.status_var.set("履歴編集中のため終了を取り消しました")
                return
            if choice:
                if not self._save_history_edit(quiet=True):
                    self.close_requested = False
                    self.recognition_button.configure(state="normal")
                    self._set_capture_settings_state("normal")
                    return
            else:
                self._finish_history_edit_ui()
        if self.active_qso_id is not None:
            if not self._end_active_qso(closing=True):
                # Keep the app open so the operator can fix and save the QSO.
                self.close_requested = False
                self.recognition_button.configure(state="normal")
                self._set_capture_settings_state("normal")
                self.status_var.set("QSOが未保存のため終了を取り消しました")
                return
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def run_gui(
    model_size: str | None = None,
    language: str | None = None,
    beam_size: int | None = None,
) -> None:
    root = tk.Tk()
    VoiceLoggerApp(root, model_size=model_size, language=language, beam_size=beam_size)
    root.mainloop()