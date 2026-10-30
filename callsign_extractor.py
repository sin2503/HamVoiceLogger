"""Extract amateur radio callsign candidates from transcription text."""

from __future__ import annotations

import re
import unicodedata


_CALLSIGN_PATTERN = re.compile(
    r"(?<![A-Z0-9])(?=[7-9JQMS][A-Z0-9]{5,6}(?![A-Z0-9]))"
    r"(?=[7-9JQMS][A-Z0-9]{0,5}[0-9])[7-9JQMS][A-Z0-9]{5,6}(?![A-Z0-9])"
)

_PHONETIC_ALIASES = {
    "A": ("ALFA", "ALPHA", "AMERICA", "ABLE", "アルファ", "アルファー", "アメリカ", "エイブル"),
    "B": ("BRAVO", "BOSTON", "BAKER", "ブラボー", "ブラヴォー", "ボストン", "ベーカー"),
    "C": ("CHARLIE", "COLOMBIA", "CANADA", "チャーリー", "コロンビア", "カナダ"),
    "D": ("DELTA", "DENMARK", "デルタ", "デンマーク"),
    "E": ("ECHO", "EDWARD", "エコー", "エドワード"),
    "F": ("FOXTROT", "FLORIDA", "FRANCE", "フォックストロット", "フロリダ", "フランス"),
    "G": ("GOLF", "GERMANY", "ゴルフ", "ジャーマニー"),
    "H": ("HOTEL", "HAWAII", "HONOLULU", "HENRY", "ホテル", "ハワイ", "ホノルル", "ヘンリー"),
    "I": ("INDIA", "ITALY", "インディア", "イタリー"),
    "J": (
        "JULIET",
        "JULIETT",
        "JAPAN",
        "JACK",
        "ジュリエット",
        "ジャパン",
        "ジャック",
    ),
    "K": ("KILO", "KENTUCKY", "KING", "キロ", "ケンタッキー", "キング"),
    "L": ("LIMA", "LONDON", "リマ", "ロンドン"),
    "M": ("MIKE", "MEXICO", "MARY", "マイク", "メキシコ", "メリー", "メアリー"),
    "N": ("NOVEMBER", "NANCY", "NORWAY", "ノベンバー", "ノーベンバー", "ナンシー", "ノルウェー"),
    "O": ("OSCAR", "OCEAN", "ONTARIO", "オスカー", "オーシャン", "オンタリオ"),
    "P": ("PAPA", "PETER", "パパ", "ピーター"),
    "Q": ("QUEBEC", "QUEEN", "ケベック", "クィーン", "クイーン"),
    "R": ("ROMEO", "RADIO", "ROBERT", "ロメオ", "レディオ", "ロバート"),
    "S": ("SIERRA", "SPAIN", "SUGAR", "SANTIAGO", "シエラ", "スペイン", "シュガー", "サンチャゴ"),
    "T": ("TANGO", "TOKYO", "タンゴ", "トーキョー"),
    "U": ("UNIFORM", "UNCLE", "ユニフォーム", "ユニホーム", "アンクル"),
    "V": ("VICTOR", "VICTORY", "ビクター", "ヴィクター", "ビクトリー"),
    "W": ("WHISKEY", "WASHINGTON", "ウィスキー", "ウイスキー", "ワシントン"),
    "X": ("X-RAY", "XRAY", "エックスレイ", "エックスレー"),
    "Y": ("YANKEE", "YOKOHAMA", "YOKE", "ヤンキー", "ヨコハマ", "ヨーク"),
    "Z": ("ZULU", "ZEBRA", "ZANZIBAR", "ズールー", "ズルー", "ゼブラ", "ザンジバル"),
    "0": ("ZERO", "ゼロ"),
    "1": ("ONE", "ワン", "いち", "一"),
    "2": ("TWO", "ツー", "に", "二"),
    "3": ("THREE", "スリー", "さん", "三"),
    "4": ("FOUR", "フォー", "よん", "四"),
    "5": ("FIVE", "ファイブ", "ご", "五"),
    "6": ("SIX", "シックス", "ろく", "六"),
    "7": ("SEVEN", "セブン", "なな", "しち", "七"),
    "8": ("EIGHT", "エイト", "はち", "八"),
    "9": ("NINE", "ナイン", "きゅう", "く", "九"),
}

_ALIAS_TO_CHARACTER = {
    unicodedata.normalize("NFKC", alias).casefold(): character
    for character, aliases in _PHONETIC_ALIASES.items()
    for alias in aliases
}
_SORTED_ALIASES = sorted(_ALIAS_TO_CHARACTER, key=len, reverse=True)
_TOKEN_SEPARATOR_PATTERN = re.compile(r"[\s・,，。、;；:：]+")


def _decode_run(run: str) -> tuple[str, str]:
    """Decode aliases from the start of a token and preserve its unknown tail."""
    normalized = unicodedata.normalize("NFKC", run).casefold()
    decoded = []
    partially_decoded = []
    cursor = 0

    while cursor < len(run):
        alias = next(
            (
                candidate
                for candidate in _SORTED_ALIASES
                if normalized.startswith(candidate, cursor)
                and not (
                    candidate.isascii()
                    and cursor + len(candidate) < len(run)
                    and normalized[cursor + len(candidate)].isascii()
                    and normalized[cursor + len(candidate)].isalnum()
                )
            ),
            None,
        )
        if alias is not None:
            character = _ALIAS_TO_CHARACTER[alias]
            decoded.append(character)
            partially_decoded.append(character)
            cursor += len(alias)
        elif run[cursor] in "-–—":
            cursor += 1
        elif run[cursor].isdigit():
            # ASR often glues digits onto phonetics, e.g. "ノベンバー1".
            digit = unicodedata.normalize("NFKC", run[cursor])
            decoded.append(digit)
            partially_decoded.append(digit)
            cursor += 1
        else:
            partially_decoded.append(run[cursor:])
            break

    return "".join(decoded), "".join(partially_decoded)


def decode_phonetics(text: str) -> str:
    """Return characters decoded from recognized phonetic words."""
    return "".join(
        _decode_run(run)[0]
        for run in _TOKEN_SEPARATOR_PATTERN.split(unicodedata.normalize("NFKC", text))
    )


def partially_decode_phonetics(text: str) -> str:
    """Decode known phonetic words while preserving unknown words in the phrase."""
    normalized = unicodedata.normalize("NFKC", text)
    decoded_runs = []
    partially_decoded_runs = []
    for run in _TOKEN_SEPARATOR_PATTERN.split(normalized):
        decoded, partial = _decode_run(run)
        decoded_runs.append(decoded)
        partially_decoded_runs.append(partial)

    if not any(decoded_runs):
        return ""
    return "".join(partially_decoded_runs)


def extract_callsigns(text: str) -> list[str]:
    """Return unique callsign candidates found directly or via phonetics.

    Priority order (first wins for duplicates):
    1. Callsigns written directly in the transcript
    2. Fully decoded phonetic sequences
    3. Partial phonetic decode, when the result still matches the callsign pattern
    """
    normalized = unicodedata.normalize("NFKC", text).upper()
    candidates = [match.group() for match in _CALLSIGN_PATTERN.finditer(normalized)]
    decoded = decode_phonetics(text).upper()
    candidates.extend(match.group() for match in _CALLSIGN_PATTERN.finditer(decoded))
    partial = partially_decode_phonetics(text).upper()
    candidates.extend(match.group() for match in _CALLSIGN_PATTERN.finditer(partial))
    return list(dict.fromkeys(candidates))