"""SQLite persistence for confirmed callsigns and QSO sessions."""

from __future__ import annotations

import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _halfwidth(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


class LogStore:
    """Store confirmed recognition candidates separately from QSO sessions."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        if database_path is None:
            database_path = Path.home() / ".ham_voice_logger" / "logger.sqlite3"
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS qso_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    callsign TEXT NOT NULL DEFAULT '',
                    frequency TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT 'FM',
                    received_report TEXT NOT NULL DEFAULT '',
                    sent_report TEXT NOT NULL DEFAULT '',
                    operator_name TEXT NOT NULL DEFAULT '',
                    qth TEXT NOT NULL DEFAULT '',
                    station_code TEXT NOT NULL DEFAULT '',
                    country TEXT NOT NULL DEFAULT '',
                    dxcc TEXT NOT NULL DEFAULT '',
                    gridsquare TEXT NOT NULL DEFAULT '',
                    cq_zone TEXT NOT NULL DEFAULT '',
                    itu_zone TEXT NOT NULL DEFAULT '',
                    dxcc_details TEXT NOT NULL DEFAULT '',
                    continent TEXT NOT NULL DEFAULT '',
                    lookup_provider TEXT NOT NULL DEFAULT '',
                    lookup_at TEXT NOT NULL DEFAULT '',
                    qsl_sent TEXT NOT NULL DEFAULT '',
                    qsl_received TEXT NOT NULL DEFAULT '',
                    qsl_via TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    remarks2 TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS confirmed_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    confirmed_at TEXT NOT NULL,
                    callsign TEXT NOT NULL,
                    transcript TEXT NOT NULL,
                    qso_id INTEGER REFERENCES qso_logs(id)
                );
                CREATE INDEX IF NOT EXISTS idx_qso_started_at
                    ON qso_logs(started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_qso_callsign
                    ON qso_logs(callsign);
                CREATE INDEX IF NOT EXISTS idx_candidate_confirmed_at
                    ON confirmed_candidates(confirmed_at DESC);
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(qso_logs)")
            }
            metadata_columns = (
                "received_report",
                "sent_report",
                "operator_name",
                "qth",
                "station_code",
                "country",
                "dxcc",
                "gridsquare",
                "cq_zone",
                "itu_zone",
                "dxcc_details",
                "continent",
                "lookup_provider",
                "lookup_at",
                "qsl_sent",
                "qsl_received",
                "qsl_via",
                "remarks2",
                "is_dx",
                "is_locked",
            )
            for column in metadata_columns:
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE qso_logs ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                    )

    def start_qso(
        self,
        frequency: str = "",
        mode: str = "FM",
        started_at: str | None = None,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO qso_logs (started_at, frequency, mode) VALUES (?, ?, ?)",
                (started_at or _timestamp(), _halfwidth(frequency), _halfwidth(mode) or "FM"),
            )
            return int(cursor.lastrowid)

    def find_qso_id(
        self,
        callsign: str,
        started_at: str,
        frequency: str = "",
        mode: str = "",
    ) -> int | None:
        """Return an existing QSO id when callsign+start(+freq/mode) already match."""
        normalized = _halfwidth(callsign).upper()
        if not normalized or not started_at:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """SELECT id FROM qso_logs
                   WHERE callsign = ? AND started_at = ?
                     AND (? = '' OR frequency = ?)
                     AND (? = '' OR UPPER(mode) = UPPER(?))
                   ORDER BY id DESC LIMIT 1""",
                (
                    normalized,
                    started_at,
                    _halfwidth(frequency),
                    _halfwidth(frequency),
                    _halfwidth(mode),
                    _halfwidth(mode) or "FM",
                ),
            ).fetchone()
        return int(row["id"]) if row is not None else None

    def set_qso_locked(self, qso_ids: list[int], locked: bool) -> int:
        """Set or clear the protect-from-delete lock on QSO rows."""
        ids = sorted({int(qso_id) for qso_id in qso_ids if int(qso_id) > 0})
        if not ids:
            return 0
        flag = "1" if locked else ""
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE qso_logs SET is_locked = ? WHERE id IN ({placeholders})",
                [flag, *ids],
            )
            return int(cursor.rowcount)

    def delete_qsos(self, qso_ids: list[int]) -> int:
        """Delete QSO rows that are not locked. Detach linked candidates first."""
        ids = sorted({int(qso_id) for qso_id in qso_ids if int(qso_id) > 0})
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as connection:
            unlocked = [
                int(row["id"])
                for row in connection.execute(
                    f"""SELECT id FROM qso_logs
                        WHERE id IN ({placeholders})
                          AND (is_locked IS NULL OR is_locked = '' OR is_locked != '1')""",
                    ids,
                ).fetchall()
            ]
            if not unlocked:
                return 0
            unlocked_placeholders = ",".join("?" for _ in unlocked)
            connection.execute(
                f"UPDATE confirmed_candidates SET qso_id = NULL WHERE qso_id IN ({unlocked_placeholders})",
                unlocked,
            )
            cursor = connection.execute(
                f"DELETE FROM qso_logs WHERE id IN ({unlocked_placeholders})",
                unlocked,
            )
            return int(cursor.rowcount)

    def add_completed_qso(self, record: dict[str, Any]) -> int:
        """Insert a finished QSO row (used by ADIF / HAMLOG CSV import)."""
        callsign = _halfwidth(str(record.get("callsign", ""))).upper()
        started_at = str(record.get("started_at") or "").strip()
        if not callsign or not started_at:
            raise ValueError("callsign and started_at are required")
        ended_at = str(record.get("ended_at") or started_at).strip() or started_at
        dx_flag = (
            "1"
            if str(record.get("is_dx", "")).strip().lower() in {"1", "true", "yes", "y"}
            else ""
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO qso_logs (
                       started_at, ended_at, callsign, frequency, mode,
                       received_report, sent_report, operator_name, qth,
                       country, dxcc, gridsquare, cq_zone, itu_zone,
                       dxcc_details, continent, lookup_provider, lookup_at,
                       station_code, qsl_sent, qsl_received, qsl_via,
                       notes, remarks2, is_dx
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    started_at,
                    ended_at,
                    callsign,
                    _halfwidth(str(record.get("frequency", ""))),
                    _halfwidth(str(record.get("mode", ""))) or "FM",
                    _halfwidth(str(record.get("received_report", ""))),
                    _halfwidth(str(record.get("sent_report", ""))),
                    str(record.get("operator_name", "") or ""),
                    str(record.get("qth", "") or ""),
                    str(record.get("country", "") or ""),
                    str(record.get("dxcc", "") or ""),
                    str(record.get("gridsquare", "") or ""),
                    str(record.get("cq_zone", "") or ""),
                    str(record.get("itu_zone", "") or ""),
                    str(record.get("dxcc_details", "") or ""),
                    str(record.get("continent", "") or ""),
                    str(record.get("lookup_provider", "") or ""),
                    str(record.get("lookup_at", "") or ""),
                    _halfwidth(str(record.get("station_code", ""))),
                    _halfwidth(str(record.get("qsl_sent", ""))),
                    _halfwidth(str(record.get("qsl_received", ""))),
                    _halfwidth(str(record.get("qsl_via", ""))),
                    str(record.get("notes", "") or "").strip(),
                    str(record.get("remarks2", "") or "").strip(),
                    dx_flag,
                ),
            )
            return int(cursor.lastrowid)

    def save_candidate(
        self,
        callsign: str,
        transcript: str,
        qso_id: int | None = None,
    ) -> int:
        normalized_callsign = _halfwidth(callsign).upper()
        if not normalized_callsign:
            raise ValueError("callsign cannot be empty")

        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO confirmed_candidates
                   (confirmed_at, callsign, transcript, qso_id)
                   VALUES (?, ?, ?, ?)""",
                (_timestamp(), normalized_callsign, transcript.strip(), qso_id),
            )
            if qso_id is not None:
                connection.execute(
                    "UPDATE qso_logs SET callsign = ? WHERE id = ? AND ended_at IS NULL",
                    (normalized_callsign, qso_id),
                )
            return int(cursor.lastrowid)

    def end_qso(
        self,
        qso_id: int,
        callsign: str,
        frequency: str,
        mode: str,
        notes: str,
        started_at: str | None = None,
        received_report: str = "",
        sent_report: str = "",
        callbook_info: dict[str, str] | None = None,
        qsl_sent: str = "",
        qsl_received: str = "",
        qsl_via: str = "",
        remarks2: str = "",
        is_dx: bool | str = False,
    ) -> None:
        callbook_info = callbook_info or {}
        dx_flag = "1" if str(is_dx).strip().lower() in {"1", "true", "yes", "y"} else ""
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE qso_logs
                   SET started_at = ?, ended_at = ?, callsign = ?, frequency = ?, mode = ?,
                       received_report = ?, sent_report = ?, operator_name = ?, qth = ?,
                       country = ?, dxcc = ?, gridsquare = ?, cq_zone = ?, itu_zone = ?,
                       dxcc_details = ?, continent = ?, lookup_provider = ?, lookup_at = ?,
                       station_code = ?, qsl_sent = ?, qsl_received = ?, qsl_via = ?,
                       notes = ?, remarks2 = ?, is_dx = ?
                   WHERE id = ? AND ended_at IS NULL""",
                (
                    started_at or _timestamp(),
                    _timestamp(),
                    _halfwidth(callsign).upper(),
                    _halfwidth(frequency),
                    _halfwidth(mode) or "FM",
                    _halfwidth(received_report),
                    _halfwidth(sent_report),
                    callbook_info.get("operator_name", ""),
                    callbook_info.get("qth", ""),
                    callbook_info.get("country", ""),
                    callbook_info.get("dxcc", ""),
                    callbook_info.get("gridsquare", ""),
                    callbook_info.get("cq_zone", ""),
                    callbook_info.get("itu_zone", ""),
                    callbook_info.get("dxcc_details", ""),
                    callbook_info.get("continent", ""),
                    callbook_info.get("lookup_provider", ""),
                    callbook_info.get("lookup_at", ""),
                    _halfwidth(callbook_info.get("station_code", "")),
                    _halfwidth(qsl_sent),
                    _halfwidth(qsl_received),
                    _halfwidth(qsl_via),
                    notes.strip(),
                    remarks2.strip(),
                    dx_flag,
                    qso_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("QSO session is not active")

    def get_qso(self, qso_id: int) -> dict[str, Any] | None:
        """Return one QSO row by id, or None."""
        with self._connect() as connection:
            row = connection.execute(
                """SELECT id, started_at, ended_at, callsign, frequency, mode,
                          received_report, sent_report, operator_name, qth, country,
                          dxcc, gridsquare, cq_zone, itu_zone, dxcc_details, continent,
                          lookup_provider, lookup_at, station_code, qsl_sent, qsl_received,
                          qsl_via, notes, remarks2, is_dx, is_locked
                   FROM qso_logs WHERE id = ?""",
                (int(qso_id),),
            ).fetchone()
        return dict(row) if row is not None else None

    def update_qso(self, qso_id: int, record: dict[str, Any]) -> None:
        """Update an existing QSO (history edit). Preserves is_locked."""
        existing = self.get_qso(qso_id)
        if existing is None:
            raise ValueError("QSO was not found")
        callsign = _halfwidth(str(record.get("callsign", existing["callsign"]))).upper()
        started_at = str(record.get("started_at") or existing["started_at"]).strip()
        if not callsign or not started_at:
            raise ValueError("callsign and started_at are required")
        ended_at = record.get("ended_at", existing["ended_at"])
        ended_text = str(ended_at).strip() if ended_at else None
        dx_flag = (
            "1"
            if str(record.get("is_dx", existing.get("is_dx") or "")).strip().lower()
            in {"1", "true", "yes", "y"}
            else ""
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE qso_logs
                   SET started_at = ?, ended_at = ?, callsign = ?, frequency = ?, mode = ?,
                       received_report = ?, sent_report = ?, operator_name = ?, qth = ?,
                       country = ?, dxcc = ?, gridsquare = ?, cq_zone = ?, itu_zone = ?,
                       dxcc_details = ?, continent = ?, lookup_provider = ?, lookup_at = ?,
                       station_code = ?, qsl_sent = ?, qsl_received = ?, qsl_via = ?,
                       notes = ?, remarks2 = ?, is_dx = ?
                   WHERE id = ?""",
                (
                    started_at,
                    ended_text,
                    callsign,
                    _halfwidth(str(record.get("frequency", existing["frequency"]))),
                    _halfwidth(str(record.get("mode", existing["mode"]))) or "FM",
                    _halfwidth(
                        str(record.get("received_report", existing["received_report"]))
                    ),
                    _halfwidth(str(record.get("sent_report", existing["sent_report"]))),
                    str(record.get("operator_name", existing["operator_name"]) or ""),
                    str(record.get("qth", existing["qth"]) or ""),
                    str(record.get("country", existing["country"]) or ""),
                    str(record.get("dxcc", existing["dxcc"]) or ""),
                    str(record.get("gridsquare", existing["gridsquare"]) or ""),
                    str(record.get("cq_zone", existing["cq_zone"]) or ""),
                    str(record.get("itu_zone", existing["itu_zone"]) or ""),
                    str(record.get("dxcc_details", existing["dxcc_details"]) or ""),
                    str(record.get("continent", existing["continent"]) or ""),
                    str(
                        record.get("lookup_provider", existing["lookup_provider"]) or ""
                    ),
                    str(record.get("lookup_at", existing["lookup_at"]) or ""),
                    _halfwidth(
                        str(record.get("station_code", existing["station_code"]))
                    ),
                    _halfwidth(str(record.get("qsl_sent", existing["qsl_sent"]))),
                    _halfwidth(
                        str(record.get("qsl_received", existing["qsl_received"]))
                    ),
                    _halfwidth(str(record.get("qsl_via", existing["qsl_via"]))),
                    str(record.get("notes", existing["notes"]) or "").strip(),
                    str(record.get("remarks2", existing["remarks2"]) or "").strip(),
                    dx_flag,
                    int(qso_id),
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("QSO was not updated")

    def force_end_qso(self, qso_id: int, note: str = "") -> None:
        """Mark an open QSO as ended even when a normal save cannot complete."""
        marker = note.strip() or "[アプリ終了時に強制終了]"
        with self._connect() as connection:
            row = connection.execute(
                "SELECT notes, ended_at FROM qso_logs WHERE id = ?",
                (qso_id,),
            ).fetchone()
            if row is None:
                raise ValueError("QSO session was not found")
            if row["ended_at"]:
                return
            existing = (row["notes"] or "").strip()
            notes = f"{existing}\n{marker}".strip() if existing else marker
            connection.execute(
                "UPDATE qso_logs SET ended_at = ?, notes = ? WHERE id = ?",
                (_timestamp(), notes, qso_id),
            )

    def lookup_callsign_history(
        self, callsign: str, limit: int = 8
    ) -> dict[str, Any]:
        """Return prior QSO summary for an exact callsign (dupe check)."""
        normalized = _halfwidth(callsign).upper()
        empty = {
            "callsign": normalized,
            "total": 0,
            "completed": 0,
            "latest": None,
            "recent": [],
        }
        if not normalized:
            return empty

        with self._connect() as connection:
            total = connection.execute(
                "SELECT COUNT(*) AS n FROM qso_logs WHERE callsign = ?",
                (normalized,),
            ).fetchone()["n"]
            completed = connection.execute(
                """SELECT COUNT(*) AS n FROM qso_logs
                   WHERE callsign = ? AND ended_at IS NOT NULL AND ended_at != ''""",
                (normalized,),
            ).fetchone()["n"]
            rows = connection.execute(
                """SELECT id, started_at, ended_at, callsign, frequency, mode,
                          received_report, sent_report, operator_name, qth,
                          gridsquare, station_code, notes, remarks2, is_dx
                   FROM qso_logs
                   WHERE callsign = ?
                   ORDER BY started_at DESC, id DESC
                   LIMIT ?""",
                (normalized, limit),
            ).fetchall()
        recent = [dict(row) for row in rows]
        return {
            "callsign": normalized,
            "total": int(total),
            "completed": int(completed),
            "latest": recent[0] if recent else None,
            "recent": recent,
        }

    def search_qsos(
        self,
        query: str = "",
        *,
        mode: str = "",
        dx_only: bool = False,
        qsl: str = "",
        date_from: str = "",
        date_to: str = "",
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        """Search QSO logs with optional structured filters.

        ``qsl`` accepts: ``sent_pending``, ``recv_pending``, ``sent``, ``recv``.
        ``date_from`` / ``date_to`` are ``YYYY-MM-DD`` (inclusive) against ``started_at``.
        """
        normalized = _halfwidth(query)
        select_sql = """SELECT id, started_at, ended_at, callsign, frequency, mode,
                                  received_report, sent_report, operator_name, qth, country,
                                  dxcc, gridsquare, cq_zone, itu_zone, dxcc_details, continent,
                                  lookup_provider, lookup_at, station_code, qsl_sent, qsl_received,
                                  qsl_via, notes, remarks2, is_dx, is_locked
                   FROM qso_logs"""
        clauses: list[str] = []
        params: list[Any] = []

        if normalized.casefold() in {"dx", "is_dx", "dx:1"}:
            dx_only = True
            normalized = ""

        if normalized:
            pattern = f"%{normalized}%"
            clauses.append(
                """(
                    callsign LIKE ? OR frequency LIKE ? OR mode LIKE ?
                    OR received_report LIKE ? OR sent_report LIKE ? OR operator_name LIKE ?
                    OR qth LIKE ? OR country LIKE ? OR dxcc LIKE ? OR gridsquare LIKE ?
                    OR cq_zone LIKE ? OR itu_zone LIKE ? OR dxcc_details LIKE ?
                    OR continent LIKE ? OR station_code LIKE ? OR qsl_sent LIKE ?
                    OR qsl_received LIKE ? OR qsl_via LIKE ? OR notes LIKE ?
                    OR remarks2 LIKE ?
                )"""
            )
            params.extend([pattern] * 20)

        mode_text = _halfwidth(mode).upper()
        if mode_text and mode_text not in {"すべて", "ALL", "*"}:
            clauses.append("UPPER(mode) = ?")
            params.append(mode_text)

        if dx_only:
            clauses.append("is_dx = '1'")

        qsl_key = _halfwidth(qsl).casefold()
        if qsl_key in {"sent_pending", "未送信"}:
            clauses.append("(qsl_sent = '' OR qsl_sent = 'N')")
        elif qsl_key in {"recv_pending", "未受領"}:
            clauses.append("(qsl_received = '' OR qsl_received = 'N')")
        elif qsl_key in {"sent", "送信済"}:
            clauses.append("qsl_sent = 'Y'")
        elif qsl_key in {"recv", "受領済"}:
            clauses.append("qsl_received = 'Y'")
        elif qsl_key in {"queued", "送信待ち"}:
            clauses.append("qsl_sent = 'Q'")
        elif qsl_key in {"requested", "請求中"}:
            clauses.append("qsl_received = 'R'")
        elif qsl_key in {"invalid_sent", "送信無効"}:
            clauses.append("qsl_sent = 'I'")
        elif qsl_key in {"invalid_recv", "受信無効"}:
            clauses.append("qsl_received = 'I'")

        from_text = _halfwidth(date_from)
        if from_text:
            clauses.append("substr(started_at, 1, 10) >= ?")
            params.append(from_text[:10])
        to_text = _halfwidth(date_to)
        if to_text:
            clauses.append("substr(started_at, 1, 10) <= ?")
            params.append(to_text[:10])

        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""{select_sql}
                    {where_sql}
                    ORDER BY started_at DESC LIMIT ?""",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_candidates(self, query: str = "", limit: int = 100) -> list[dict[str, Any]]:
        pattern = f"%{query.strip()}%"
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id, confirmed_at, callsign, transcript, qso_id
                   FROM confirmed_candidates
                   WHERE callsign LIKE ? OR transcript LIKE ?
                   ORDER BY confirmed_at DESC, id DESC LIMIT ?""",
                (pattern, pattern, limit),
            ).fetchall()
        return [dict(row) for row in rows]