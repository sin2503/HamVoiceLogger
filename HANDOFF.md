# HAM Voice Logger: Cursor Handoff

## Project Goal

Build a cross-platform amateur-radio QSO assistant. It listens to a microphone, transcribes speech locally, extracts Japanese callsign candidates, and lets the operator confirm callsigns and manage QSO logs. Recognition and QSO logging are intentionally independent.

Primary constraints: Python 3.12+ (prefer the latest 3.12.x patch), Windows/macOS/Linux, local speech recognition, no paid service dependency, and a GUI usable while radio logging software is open.

## Current State

The MVP runs as a Tkinter desktop application. The main window has recognition controls, transcription/callsign candidates, a QSO form, QSO history search, confirmed-candidate history, and ADIF export.

Recognition uses `sounddevice` + `numpy` RMS segmentation and a pluggable local ASR catalog in `decoder.MODEL_CATALOG`. Keys are `engine:label` (`faster-whisper:small`, `whisper.cpp:tiny`, `moonshine:tiny-ja`, …). Legacy short names such as `small` normalize to `faster-whisper:small`. Default backend remains faster-whisper (`small`, Japanese, CPU `int8`). Settings → 認識モデル lists all engines and downloads via `decoder.download_model` (HF `snapshot_download` for faster-whisper; Moonshine ONNX files from `UsefulSensors/moonshine` `onnx/merged/<label>/float/`; direct ggml URL for whisper.cpp). Japanese Moonshine ONNX is `tiny-ja` only (`base-ja` is not published in that layout). Progress UI state lives on the app instance across Settings close/reopen. The main combo shows downloaded models only as `engine · label`; recognition uses `local_files_only=True` for faster-whisper and never auto-fetches. Optional runtime deps (`pywhispercpp`, `useful-moonshine-onnx`) are installed automatically via `pip` into the current interpreter by `decoder.ensure_engine_runtime` when downloading that engine’s model or when recognition starts without the package. Beam size stays on the main window (ignored by Moonshine). CLI `--model` accepts catalog keys or legacy short names and may download on demand (`local_files_only=False`). Managed files also land under `~/.ham_voice_logger/models/` for whisper.cpp/Moonshine; faster-whisper still uses the Hugging Face cache. Call-sign extraction accepts direct callsigns and English/Japanese phonetics. Candidates are saved only after the operator clicks them. Clearing live display does not delete saved data.

The QSO form includes start date/time pickers, callsign, frequency, mode, received/sent signal reports, editable operator name/QTH/grid, a freeform JCC/JCG code, QSL sent/received status and route, and Remarks 1/2. Callsign input shows a local-DB dupe summary. 「前回値」 applies the only prior QSO directly, or opens a modal picker when several exist (Name/QTH/Code/GL; empty freq / mode). Callsign, frequency, reports, and code are NFKC-normalized to half-width on save. Code (JCC/JCG/ward) is resolved via `jcc_jcg.py` + bundled `data/jcc_jcg.csv`; matching fills empty QTH and offers 「QTHへ」 to overwrite.

GUI preferences persist in `~/.ham_voice_logger/settings.json` via `app_settings.py`: threshold, silence duration, model, beam size, input device (index + name), window geometry, last frequency/mode, frequency/mode presets, report presets, and history time display (`jst` / `utc` / `both`). Values are written on clean app exit and restored on the next launch (geometry is clamped to the current screen). CLI flags such as `--model` / `--beam-size` override saved values when provided. Downloaded model weights persist separately (HF cache / `~/.ham_voice_logger/models/`). Always-on-top candidate popup (`CandidatePopup` in `gui.py`) shows the latest transcript and callsign buttons; toggled from the header and persisted via `popup_enabled` / `popup_*` geometry in settings. Settings has tabs for recognition models, HamQTH, and QSO presets.

## Callbook Lookup

The QSO form has a manual `公開DXCC / Callbook照会` action. It never sends a lookup automatically for every transcription. After a QSO starts, the same button may be relabeled `HamQTHで照会` in the current GUI; both paths still choose public DXCC vs authenticated XML from stored credentials.

- Without HamQTH credentials, it calls the public HamQTH DXCC JSON endpoint. This was tested with `JA1YAA`; it returned Japan, DXCC 339, CQ 25, ITU 45, and continent AS without an account.
- If HamQTH credentials exist, it uses the detailed XML callbook API. Credentials are kept in the OS credential store through `keyring`, not in the repository or SQLite. Username/password settings are available from Settings → HamQTH.
- Authenticated XML fields mapped into the app: nick → name, qth, country, adif → DXCC, grid, cq/itu, continent. Address, email, birth year, and biography are excluded. DXCC `details` is not in the XML callbook payload; it is filled from the public DXCC JSON when that call succeeds.
- The user reviews returned data before attaching it to the current QSO. Stored callbook fields are name, QTH, country, DXCC, grid, CQ/ITU zones, details, continent, provider, and lookup timestamp.
- The HamQTH XML session ID is cached in memory for 45 minutes. The API documents a one-hour session lifetime. If the API reports that the session expired or does not exist, the client clears the cache, logs in again, and retries the lookup once. Saving new credentials resets the in-app client/session.

Research sources: [HamQTH developer documentation](https://www.hamqth.com/developers.php), [ADIF 3.1.4 specification](https://adif.org/314/ADIF_314.htm), [QRZ XML data service](https://www.qrz.com/page/xml_data.html).

## Running on This Mac

This workspace was set up on Apple Silicon macOS using Homebrew Python 3.12 (currently 3.12.16+). Tk support is installed with `python-tk@3.12`; the virtual environment is `.venv/`.

```bash
source .venv/bin/activate
python main.py
```

The terminal interface remains available with:

```bash
python main.py --cli
```

Tests:

```bash
python -m unittest discover -s tests
```

On a fresh machine, install Python 3.12+ with Tcl/Tk support (prefer the latest 3.12.x patch), create a venv, upgrade `pip` / `certifi` (`python -m pip install -U "pip>=26.2.2" certifi`), then run `python -m pip install -r requirements.txt`. Download a model from Settings before the first GUI recognition start (no auto-download of model weights). Choosing whisper.cpp / Moonshine installs their pip packages automatically. Model and beam size can be changed in the GUI or with `--model` / `--beam-size`.

## Data and Compatibility

SQLite data lives at `~/.ham_voice_logger/logger.sqlite3`. `LogStore` adds missing columns at startup, so older databases are migrated in place. Do not store API passwords in the database.

The QSO history tab can export or import ADI `.adi` / ADX `.adx` and Turbo HAMLOG CSV against the current search filters. ADI export converts timestamps to UTC and maps reports/QSL/`CNTY`; ASCII mode omits non-ASCII optional fields, UTF-8 ADI and ADX keep Japanese Name/QTH/etc. ADI import (`adif_import.py`) reads UTF-8/CP932 ADI and inserts completed QSOs. HAMLOG CSV export uses the classic 14-column append layout (no record numbers), JST `yy/mm/dd` + `HH:MMJ`, CP932, CRLF. HAMLOG CSV import (`hamlog_csv_import.py`) accepts 14 columns or a leading record-number column. Duplicate rows (same callsign + started_at + freq/mode) are skipped.

History rows can be edited (double-click / 「選択を編集」) via `LogStore.update_qso` into the main QSO form (`editing_qso_id`). QSL defaults for new QSOs live in `settings.json`. QTH→Code uses `jcc_jcg.search_station_codes` from the 「地名→Code」 dialog.

History delete mode shows a checkbox column (☐/☑); clicking any cell (except 保護) toggles that row so discontinuous multi-select is easy. 「選択を削除」 confirms then calls `LogStore.delete_qsos` (candidates detach first). Outside delete mode the tree uses `selectmode=extended` (⌘/Ctrl discontinuous, Shift range) for ロック / ロック解除. Rows can also be locked via the 保護 column; `is_locked='1'` is persisted. Locked rows and the active QSO (`active_qso_id`, 記録中) are not deletable; `delete_qsos` skips locked rows.
Callsign extraction is Japan-oriented: prefix `[7-9JQMS]`, total length 6–7, must contain a digit. `JA1YAA` and `7L1ABC` match; 5-character calls such as `JA1AA` and foreign calls such as `W1AW` do not. Candidate sources, in priority order: direct text matches, fully decoded phonetics, then partial phonetic decode when the partial string still matches the callsign regex. Bare digits glued onto phonetic tokens (e.g. `ノベンバー1`) are accepted during decode.

## Key Files

- `main.py`: GUI by default; `--cli` selects the terminal interface.
- `gui.py`: Tkinter UI, worker-thread recognition, manual callbook lookup, QSO editing/history, and ADIF export controls.
- `app_settings.py`: JSON persistence for GUI preferences and window geometry.
- `audio_monitor.py`: microphone capture and RMS/silence-based segmentation.
- `decoder.py`: multi-engine local transcription and model download catalog.
- `callsign_extractor.py`: direct and phonetic callsign extraction plus partial decoding.
- `callbook.py`: HamQTH public DXCC JSON lookup, authenticated XML lookup, and keyring helpers.
- `log_store.py`: SQLite schema, migrations, QSO/candidate persistence and search.
- `adif_export.py` / `adif_import.py`: ADI writer and importer.
- `hamlog_csv_export.py` / `hamlog_csv_import.py`: Turbo HAMLOG CSV writer and importer.
- `jcc_jcg.py` + `data/jcc_jcg.csv`: JCC/JCG/ward code → Japanese QTH helper.
- `time_format.py`: JST/UTC display helpers and Japan-entity check for DX auto-flag.
- `tests/`: `unittest` coverage including callsign extractor, ADIF, and decoder model helpers.
- `requirements.txt`: core runtime dependencies; optional `pywhispercpp` / `useful-moonshine-onnx` are commented. `numpy<2` is intentional due a NumPy 2.x/faster-whisper feature-extraction warning observed on this Mac.
- `.cursorrules`: commit message and project conventions for this repo (not a leftover from another app).

## Feature Priority (HAMLOG-inspired, for this app)

1. Done: dupe check + apply previous Name/QTH/Code/GL/freq/mode from local DB (modal picker when multiple).
2. Done: frequency / mode presets (combo + add-current; Settings editor; last freq/mode restored).
3. Done: report presets (e.g. 59/59 buttons; Settings editor).
4. Done: JCC/JCG/ward code → QTH helper (`jcc_jcg.py`, bundled CSV).
5. Done: DX checkbox (auto-on for non-Japan callbook); JST/UTC/both history display; export keeps ADIF=UTC / HAMLOG=JST and tags DX in COMMENT / Remarks1.
6. Done: history compound search (text + mode / DX / QSL / date range); exports use current filters.
7. Done: ADIF import + HAMLOG CSV import (duplicate skip; history-tab buttons).
8. Done: history delete mode (checkboxes + confirm; detach candidates then delete QSO rows) plus lock / in-progress protection.
9. Done: edit-from-history; QSL defaults; QTH→Code reverse lookup UI; UTF-8 ADI + ADX export.
10. Done: always-on-top candidate popup (header toggle; geometry persisted).
11. Optional: segment audio archive (personal use; retention limits) — not a full HAMLOG clone feature.
12. Still useful: smoke-test HAMLOG CSV against the operator’s Turbo HAMLOG/Win.

## Known Gaps / Next Work

- Speech accuracy and false segmentation need more real-radio testing. The current VAD is RMS thresholding, not a speech/noise classifier. Prefer on-air checks over synthetic audio tests for this gap.
- HAMLOG CSV should still be smoke-tested against the operator’s Turbo HAMLOG/Win version (some builds append hQSL/user columns on export).
- QSL/Code field semantics should be checked against the exact Turbo HAMLOG workflow the operator uses. `Code` is stored as free text, exported as ADIF `CNTY`, and as the HAMLOG CSV Code column.
- Closing the app while a QSO is open tries to end the QSO first. On failure the operator can retry, force-end (`LogStore.force_end_qso`), or cancel quit so the window stays open. Settings are saved before that end-QSO attempt.
- Callbook button label is `公開DXCC照会` or `HamQTH照会` from stored credentials while a QSO is active.
- No automatic cloud sync or automatic QSO creation is intended; callbook lookup is manual and candidate saving requires confirmation.

## Workspace Notes

The worktree may still be uncommitted. Preserve existing user changes. `.vscode/settings.json` is present in the workspace; do not overwrite it. No credentials are included in this handoff.
