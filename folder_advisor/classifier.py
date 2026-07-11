"""ファイルの分類（ルールベース）。

拡張子・パス上の既存カテゴリ語・ファイル名トークンから一次分類する。
LLM 補助は proposer 側から呼び出し、ここではルールベースの確実な分類に集中する。

分類は「資料種別」を基本軸とする。目的別（部署・案件）はパスから推定できる場合の
補助情報として扱い、提案ツリーの第一階層に用いる（proposer 側）。
"""

from __future__ import annotations

import re
from collections import Counter

from .models import FileEntry

# 資料種別のキーワード辞書（ファイル名・パスに含まれると該当）。
# 上にあるものほど優先（最初にマッチしたものを採用）。
CATEGORY_RULES: list[tuple[str, re.Pattern]] = [
    ("契約・法務", re.compile(r"契約|覚書|規約|nda|法務|約款", re.IGNORECASE)),
    ("見積・請求", re.compile(r"見積|請求|発注|納品|検収|与信", re.IGNORECASE)),
    ("提案・企画", re.compile(r"提案|企画|プレゼン|proposal|plan", re.IGNORECASE)),
    ("議事録・会議", re.compile(r"議事録|会議|打合せ|打ち合わせ|minutes|mtg", re.IGNORECASE)),
    ("報告・レポート", re.compile(r"報告|レポート|報告書|report|週報|月報|日報", re.IGNORECASE)),
    ("マニュアル・手順", re.compile(r"マニュアル|手順|manual|procedure|ガイド|guide|運用", re.IGNORECASE)),
    ("仕様・設計", re.compile(r"仕様|設計|要件|spec|design|requirement", re.IGNORECASE)),
    ("画像・素材", re.compile(r"logo|素材|バナー|banner|icon|写真|photo", re.IGNORECASE)),
]

# 拡張子ベースの分類（キーワードに当たらない場合のフォールバック）。
EXT_CATEGORY: dict[str, str] = {
    "doc": "文書", "docx": "文書", "txt": "文書", "rtf": "文書", "md": "文書",
    "xls": "表計算", "xlsx": "表計算", "csv": "表計算",
    "ppt": "プレゼン", "pptx": "プレゼン",
    "pdf": "PDF",
    "png": "画像・素材", "jpg": "画像・素材", "jpeg": "画像・素材",
    "gif": "画像・素材", "bmp": "画像・素材", "svg": "画像・素材",
    "zip": "圧縮・アーカイブ", "7z": "圧縮・アーカイブ", "rar": "圧縮・アーカイブ",
    "mp4": "動画", "mov": "動画", "avi": "動画",
}

UNCLASSIFIED = "未分類"


def classify_one(entry: FileEntry) -> tuple[str, str]:
    """1 ファイルを分類し (category, source) を返す。source は 'rule'。"""
    hay = f"{entry.parent}/{entry.name}"
    for label, pat in CATEGORY_RULES:
        if pat.search(hay):
            return label, "rule"
    if entry.ext in EXT_CATEGORY:
        return EXT_CATEGORY[entry.ext], "rule"
    return UNCLASSIFIED, "rule"


def classify_all(files: list[FileEntry]) -> Counter:
    """全ファイルを分類し、カテゴリ別件数を返す（entry にも category を付与）。"""
    counts: Counter = Counter()
    for f in files:
        label, source = classify_one(f)
        f.category = label
        f.category_source = source
        counts[label] += 1
    return counts


def top_path_segment(entry: FileEntry) -> str:
    """パスの第一セグメント（部署・案件などの推定に使う）。"""
    if not entry.parent:
        return ""
    return entry.parent.split("/", 1)[0]
