"""HamQTH callbook lookup and secure credential storage."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import datetime
from threading import Lock
from time import monotonic
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


_KEYRING_SERVICE = "ham-voice-logger.hamqth"
_HAMQTH_ENDPOINT = "https://www.hamqth.com/xml.php"
_HAMQTH_DXCC_ENDPOINT = "https://www.hamqth.com/dxcc_json.php"


class CallbookError(RuntimeError):
    """An expected lookup or credential error suitable for showing in the GUI."""


def lookup_public_dxcc(callsign: str, timeout: float = 12) -> dict[str, str]:
    """Look up public DXCC metadata without a HamQTH account."""
    normalized_callsign = callsign.strip().upper()
    if not normalized_callsign:
        raise CallbookError("照会するコールサインを入力してください。")

    request = Request(
        f"{_HAMQTH_DXCC_ENDPOINT}?{urlencode({'callsign': normalized_callsign})}",
        headers={"User-Agent": "HAMVoiceLogger/0.1"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except HTTPError as exc:
        raise CallbookError(f"HamQTH公開照会に失敗しました (HTTP {exc.code})。") from None
    except (URLError, TimeoutError, OSError):
        raise CallbookError("HamQTH公開照会へ接続できません。ネットワークを確認してください。") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise CallbookError("HamQTHから不正なJSON応答を受け取りました。") from None

    if not isinstance(payload, dict) or not payload.get("adif"):
        raise CallbookError("コールサインに対応するDXCC情報が見つかりませんでした。")

    return {
        "callsign": normalized_callsign,
        "operator_name": "",
        "qth": "",
        "country": str(payload.get("name", "")),
        "dxcc": str(payload.get("adif", "")),
        "gridsquare": "",
        "cq_zone": str(payload.get("waz", "")),
        "itu_zone": str(payload.get("itu", "")),
        "dxcc_details": str(payload.get("details", "")),
        "continent": str(payload.get("continent", "")),
        "lookup_provider": "HamQTH DXCC",
        "lookup_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def save_hamqth_credentials(username: str, password: str) -> None:
    username = username.strip()
    if not username or not password:
        raise CallbookError("ユーザー名とパスワードを入力してください。")
    try:
        import keyring

        keyring.set_password(_KEYRING_SERVICE, "username", username)
        keyring.set_password(_KEYRING_SERVICE, "password", password)
    except Exception as exc:
        raise CallbookError(f"OSの資格情報ストアへ保存できません: {exc}") from exc


def load_hamqth_credentials() -> tuple[str, str]:
    try:
        import keyring

        username = keyring.get_password(_KEYRING_SERVICE, "username")
        password = keyring.get_password(_KEYRING_SERVICE, "password")
    except Exception as exc:
        raise CallbookError(f"OSの資格情報ストアを利用できません: {exc}") from exc

    if not username or not password:
        raise CallbookError("HamQTHの照会設定がありません。先にユーザー名とパスワードを設定してください。")
    return username, password


def _is_session_error(message: str) -> bool:
    normalized = message.casefold()
    return "session" in normalized and (
        "expired" in normalized or "does not exist" in normalized
    )


def _supplement_public_dxcc_fields(result: dict[str, str]) -> dict[str, str]:
    """Fill continent / DXCC details from the public JSON when XML omits them."""
    if result.get("continent") and result.get("dxcc_details"):
        return result
    try:
        public = lookup_public_dxcc(result["callsign"])
    except CallbookError:
        return result

    if not result.get("continent") and public.get("continent"):
        result["continent"] = public["continent"]
    if not result.get("dxcc_details") and public.get("dxcc_details"):
        result["dxcc_details"] = public["dxcc_details"]
    return result


class HamQTHClient:
    def __init__(self, username: str, password: str, timeout: float = 12) -> None:
        self.username = username
        self.password = password
        self.timeout = timeout
        self._session_id: str | None = None
        self._session_expires_at = 0.0
        self._session_lock = Lock()

    def lookup(self, callsign: str) -> dict[str, str]:
        normalized_callsign = callsign.strip().upper()
        if not normalized_callsign:
            raise CallbookError("照会するコールサインを入力してください。")
        result = self._lookup_xml(normalized_callsign, retry_expired_session=True)
        return _supplement_public_dxcc_fields(result)

    def _lookup_xml(
        self, normalized_callsign: str, retry_expired_session: bool
    ) -> dict[str, str]:
        session_id = self._get_session_id()
        result_root = self._request_xml(
            {
                "id": session_id,
                "callsign": normalized_callsign,
                "prg": "HamVoiceLogger",
            }
        )
        error = self._find_text(result_root, "error")
        if error:
            if _is_session_error(error):
                self._clear_session()
                if retry_expired_session:
                    return self._lookup_xml(
                        normalized_callsign, retry_expired_session=False
                    )
            raise CallbookError(error)

        if self._find_text(result_root, "callsign") is None:
            raise CallbookError("HamQTHから局情報を取得できませんでした。")

        return {
            "callsign": normalized_callsign,
            "operator_name": self._find_text(result_root, "nick") or "",
            "qth": self._find_text(result_root, "qth") or "",
            "country": self._find_text(result_root, "country") or "",
            "dxcc": self._find_text(result_root, "adif") or "",
            "gridsquare": self._find_text(result_root, "grid") or "",
            "cq_zone": self._find_text(result_root, "cq") or "",
            "itu_zone": self._find_text(result_root, "itu") or "",
            "dxcc_details": "",
            "continent": self._find_text(result_root, "continent") or "",
            "lookup_provider": "HamQTH",
            "lookup_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    def _get_session_id(self) -> str:
        with self._session_lock:
            if self._session_id and monotonic() < self._session_expires_at:
                return self._session_id

            session_root = self._request_xml({"u": self.username, "p": self.password})
            session_id = self._find_text(session_root, "session_id")
            if not session_id:
                raise CallbookError(
                    self._find_text(session_root, "error")
                    or "HamQTHの認証に失敗しました。"
                )

            self._session_id = session_id
            self._session_expires_at = monotonic() + 45 * 60
            return session_id

    def _clear_session(self) -> None:
        with self._session_lock:
            self._session_id = None
            self._session_expires_at = 0.0

    def _request_xml(self, params: dict[str, str]) -> ET.Element:
        request = Request(
            f"{_HAMQTH_ENDPOINT}?{urlencode(params)}",
            headers={"User-Agent": "HAMVoiceLogger/0.1"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
        except HTTPError as exc:
            raise CallbookError(f"HamQTHへの接続に失敗しました (HTTP {exc.code})。") from None
        except (URLError, TimeoutError, OSError):
            raise CallbookError("HamQTHへ接続できません。ネットワークを確認してください。") from None

        try:
            return ET.fromstring(payload)
        except ET.ParseError as exc:
            raise CallbookError("HamQTHから不正なXML応答を受け取りました。") from exc

    @staticmethod
    def _find_text(root: ET.Element, name: str) -> str | None:
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] == name:
                return (element.text or "").strip()
        return None