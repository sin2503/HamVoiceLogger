"""Run live microphone monitoring and local transcription."""

from __future__ import annotations

import argparse

from app_settings import (
    DEFAULT_BEAM_SIZE,
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL,
    DEFAULT_SILENCE_DURATION,
    DEFAULT_THRESHOLD,
    load_settings,
)
from audio_monitor import AudioMonitor
from callsign_extractor import extract_callsigns, partially_decode_phonetics
from decoder import MODEL_CATALOG, SpeechDecoder, format_model_choice, normalize_model_key


def run_cli(args: argparse.Namespace) -> None:
    settings = load_settings()
    model_key = normalize_model_key(args.model or settings.model or DEFAULT_MODEL)
    language = args.language or settings.language or DEFAULT_LANGUAGE
    beam_size = (
        args.beam_size if args.beam_size is not None else settings.beam_size
    ) or DEFAULT_BEAM_SIZE
    threshold = (
        args.threshold if args.threshold is not None else settings.threshold
    )
    silence_duration = (
        args.silence_duration
        if args.silence_duration is not None
        else settings.silence_duration
    )
    device = args.device if args.device is not None else settings.device_index

    print(
        f"文字起こしモデルを準備中: {format_model_choice(model_key)} ({language})",
        flush=True,
    )
    decoder = SpeechDecoder(
        model_size=model_key,
        language=language,
        beam_size=beam_size,
        local_files_only=False,
    )
    monitor = AudioMonitor(
        threshold=threshold if threshold is not None else DEFAULT_THRESHOLD,
        silence_duration=(
            silence_duration
            if silence_duration is not None
            else DEFAULT_SILENCE_DURATION
        ),
        device=device,
    )

    try:
        with monitor:
            print("マイクを監視中です。終了するには Ctrl+C を押してください。", flush=True)
            while True:
                audio = monitor.get_segment()
                text = decoder.transcribe(audio, sample_rate=monitor.sample_rate)
                if text:
                    print(f"文字起こし: {text}", flush=True)
                    callsigns = extract_callsigns(text)
                    print(
                        "コールサイン候補: " + (", ".join(callsigns) or "なし"),
                        flush=True,
                    )
                    if not callsigns:
                        partial = partially_decode_phonetics(text)
                        if partial:
                            print(f"一部解読: {partial}", flush=True)
    except KeyboardInterrupt:
        print("\n監視を終了しました。")


def main() -> None:
    parser = argparse.ArgumentParser(description="HAM Voice Logger desktop application")
    parser.add_argument("--cli", action="store_true", help="Run the terminal interface")
    catalog_keys = ", ".join(spec.key for spec in MODEL_CATALOG)
    parser.add_argument(
        "--model",
        default=None,
        metavar="NAME",
        help=(
            "Model key (engine:label), legacy faster-whisper short name, "
            f"or local path. Overrides saved settings. Catalog: {catalog_keys}"
        ),
    )
    parser.add_argument(
        "--language",
        default=None,
        help=f"Language code (default: saved setting or {DEFAULT_LANGUAGE})",
    )
    parser.add_argument(
        "--beam-size",
        type=int,
        default=None,
        help=f"Beam size (default: saved setting or {DEFAULT_BEAM_SIZE})",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help=f"RMS threshold (default: saved setting or {DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--silence-duration",
        type=float,
        default=None,
        help=(
            "Silence duration seconds "
            f"(default: saved setting or {DEFAULT_SILENCE_DURATION})"
        ),
    )
    parser.add_argument("--device", type=int, default=None, help="Input device ID")
    args = parser.parse_args()

    if args.cli:
        run_cli(args)
        return

    from gui import run_gui

    run_gui(
        model_size=normalize_model_key(args.model) if args.model else None,
        language=args.language,
        beam_size=args.beam_size,
    )


if __name__ == "__main__":
    main()
