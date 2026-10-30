"""JCC / JCG / ward code lookup for QTH helper text."""

from __future__ import annotations

import csv
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

_DATA_PATH = Path(__file__).resolve().parent / "data" / "jcc_jcg.csv"

_KIND_SUFFIX = {
    "City": "市",
    "Gun": "郡",
    "Ku": "区",
}


@dataclass(frozen=True)
class JccEntry:
    code: str
    name_ja: str
    pref_ja: str
    kind: str
    deleted: bool
    qth: str
    label: str


def _ja_part(value: str) -> str:
    text = (value or "").strip()
    if " / " in text:
        return text.split(" / ", 1)[1].strip()
    return text


def _normalize_pref(pref_ja: str) -> str:
    pref = pref_ja.strip()
    if pref == "東京":
        return "東京都"
    return pref


def _with_suffix(name: str, kind: str) -> str:
    place = name.strip()
    if not place:
        return ""
    if place.endswith(("市", "区", "郡", "町", "村")):
        return place
    suffix = _KIND_SUFFIX.get(kind, "")
    return f"{place}{suffix}" if suffix else place


def _format_qth(
    code: str,
    name_ja: str,
    pref_ja: str,
    kind: str,
    by_code: dict[str, dict[str, str]],
) -> str:
    pref = _normalize_pref(pref_ja)
    place = _with_suffix(name_ja, kind)

    # Ordinance-city wards: 010101 → parent city 0101 → 札幌市中央区
    if len(code) == 6 and kind == "Ku":
        parent = by_code.get(code[:4])
        if parent and parent.get("kind") == "City" and parent.get("deleted") != "true":
            city = _with_suffix(_ja_part(parent["name"]), "City")
            ward = _with_suffix(name_ja, "Ku")
            return f"{city}{ward}"

    # Tokyo 23 wards use 1001xx and parent city 1001 is deleted.
    if len(code) == 6 and code.startswith("1001"):
        ward = _with_suffix(name_ja, "Ku")
        return f"東京都{ward}"

    if kind == "Gun":
        return f"{pref}{place}" if pref else place

    if kind == "City":
        # Tokyo special-ward rows are typed City in the source CSV.
        if len(code) == 6 and code.startswith("1001"):
            ward = _with_suffix(name_ja, "Ku")
            return f"東京都{ward}"
        return f"{pref}{place}" if pref else place

    if kind == "Ku":
        ward = _with_suffix(name_ja, "Ku")
        return f"{pref}{ward}" if pref else ward

    return f"{pref}{place}" if pref else place


def _load_rows(path: Path) -> dict[str, dict[str, str]]:
    by_code: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            code = (row.get("CNTY") or "").strip()
            if not code:
                continue
            deleted = (row.get("Deleted") or "").strip().lower() == "true"
            by_code[code] = {
                "code": code,
                "name": row.get("CNTY Name") or "",
                "pref": row.get("STATE Name") or "",
                "kind": (row.get("Type") or "").strip() or "City",
                "deleted": "true" if deleted else "false",
            }
    return by_code


@lru_cache(maxsize=1)
def _catalog() -> dict[str, JccEntry]:
    path = _DATA_PATH
    if not path.is_file():
        return {}
    raw = _load_rows(path)
    catalog: dict[str, JccEntry] = {}
    for code, row in raw.items():
        name_ja = _ja_part(row["name"])
        pref_ja = _ja_part(row["pref"])
        kind = row["kind"]
        deleted = row["deleted"] == "true"
        qth = _format_qth(code, name_ja, pref_ja, kind, raw)
        if deleted:
            status = "廃止"
        elif kind == "Gun":
            status = "JCG"
        elif kind == "Ku" or (len(code) == 6 and code.startswith("1001")):
            status = "区"
        else:
            status = "JCC"
        label = f"{code}: {qth}（{status}）" if qth else f"{code}（{status}）"
        catalog[code] = JccEntry(
            code=code,
            name_ja=name_ja,
            pref_ja=_normalize_pref(pref_ja),
            kind=kind,
            deleted=deleted,
            qth=qth,
            label=label,
        )
    return catalog


def normalize_station_code(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").strip()


def lookup_station_code(code: str) -> JccEntry | None:
    """Exact lookup for a JCC / JCG / ward code."""
    normalized = normalize_station_code(code)
    if not normalized:
        return None
    return _catalog().get(normalized)


def search_station_codes(query: str, limit: int = 12) -> list[JccEntry]:
    """Prefix/code or Japanese name substring search (active entries first)."""
    normalized = normalize_station_code(query)
    if len(normalized) < 2:
        return []
    catalog = _catalog()
    exact = catalog.get(normalized)
    if exact is not None:
        return [exact]

    hits: list[JccEntry] = []
    for entry in catalog.values():
        if entry.code.startswith(normalized) or normalized in entry.qth or normalized in entry.name_ja:
            hits.append(entry)
    hits.sort(key=lambda item: (item.deleted, item.code))
    return hits[:limit]


def iter_entries() -> Iterable[JccEntry]:
    return _catalog().values()
