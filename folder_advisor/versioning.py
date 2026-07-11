"""旧版・作業中ファイルの検出と、ファイル名の正規化。

ファイル名のパターンから「同一系列（同じ資料の別版）」をまとめ、最新候補と
旧版候補を提示する。※あくまで判断材料の提示であり、自動削除はしない。

版の新旧判定は、当てにできない更新日ではなく **ファイル名中の版番号・日付** を
一次情報とし、それが無い場合のみ補助的に更新日を用いる。
"""

from __future__ import annotations

import re
from typing import Optional

from .models import FileEntry, VersionSeries

# 版・状態を表すトークン（正規化時に除去する対象）。
_VERSION_PATTERNS = [
    re.compile(r"[ _\-（(]*(v|ver|version|rev|r)\.?\s*\d+(\.\d+)*[ _\-）)]*", re.IGNORECASE),
    re.compile(r"[ _\-（(]*第?\s*\d+\s*版[ _\-）)]*"),
    re.compile(r"[ _\-（(]*(最新|最終|確定|正式|旧|old|final|fix|コピー|copy|作業中|wip|draft|下書き|ドラフト|メモ|tmp|temp)[ _\-）)]*", re.IGNORECASE),
    re.compile(r"[ _\-（(]*-?\s*コピー\s*(\(\d+\))?[ _\-）)]*"),
]
# 日付表記（YYYYMMDD / YYYY-MM-DD / YYYY_MM_DD / YYMMDD）。
_DATE_PATTERN = re.compile(r"(19|20)\d{2}[-_]?\d{2}[-_]?\d{2}|\d{6,8}")
# 版番号抽出（新旧比較用）。
_VER_NUM = re.compile(r"(?:v|ver|version|rev)\.?\s*(\d+(?:\.\d+)*)", re.IGNORECASE)
_KANJI_VER = re.compile(r"第?\s*(\d+)\s*版")

# 「最新」を示す語（含まれていれば最新候補として優先）。
_LATEST_WORDS = re.compile(r"(最新|最終|確定|正式|final|fix)", re.IGNORECASE)
# 「旧」を示す語。
_OLD_WORDS = re.compile(r"(旧|old|作業中|wip|draft|下書き|ドラフト|コピー|copy)", re.IGNORECASE)


def normalize_name(stem: str) -> str:
    """版・状態・日付表記を除いた基準名を返す（近似重複・系列判定に使う）。"""
    s = stem
    s = _DATE_PATTERN.sub(" ", s)
    for pat in _VERSION_PATTERNS:
        s = pat.sub(" ", s)
    # 連続する区切り・空白を 1 つに畳んで整える。
    s = re.sub(r"[ _\-]+", " ", s).strip()
    return s.lower()


def _version_score(entry: FileEntry) -> tuple:
    """新しいほど大きくなるスコア。(明示最新, 版番号, 日付, 更新日) の順で比較。"""
    name = entry.stem
    is_latest_word = 1 if _LATEST_WORDS.search(name) else 0
    is_old_word = -1 if _OLD_WORDS.search(name) else 0

    ver = 0.0
    m = _VER_NUM.search(name)
    if m:
        parts = m.group(1).split(".")
        ver = float(parts[0]) + (float(parts[1]) / 100 if len(parts) > 1 else 0)
    else:
        mk = _KANJI_VER.search(name)
        if mk:
            ver = float(mk.group(1))

    date_val = 0
    md = _DATE_PATTERN.search(name)
    if md:
        digits = re.sub(r"\D", "", md.group(0))
        date_val = int(digits) if digits else 0

    # 更新日は低信頼のため最後の tiebreaker のみ。
    mtime = entry.modified or ""
    return (is_latest_word + is_old_word, ver, date_val, mtime)


def detect_version_series(files: list[FileEntry]) -> list[VersionSeries]:
    """同一基準名でまとまり、かつ複数版があるものを系列として返す。"""
    groups: dict[str, list[FileEntry]] = {}
    for f in files:
        base = normalize_name(f.stem)
        if not base:
            continue
        # 拡張子違いは別系列にしない方が実務的だが、まずは基準名+拡張子で束ねる。
        key = f"{base}|{f.ext}"
        groups.setdefault(key, []).append(f)

    series_list: list[VersionSeries] = []
    idx = 0
    for key, members in groups.items():
        if len(members) < 2:
            continue
        idx += 1
        sid = f"series-{idx:04d}"
        ranked = sorted(members, key=_version_score, reverse=True)
        latest = ranked[0]
        older = ranked[1:]
        base_name = key.split("|", 1)[0]
        for f in members:
            f.version_series = sid
            f.is_latest_in_series = f.path == latest.path
        series_list.append(
            VersionSeries(
                series_id=sid,
                base_name=base_name,
                members=[f.path for f in ranked],
                latest=latest.path,
                older=[f.path for f in older],
            )
        )
    return series_list
