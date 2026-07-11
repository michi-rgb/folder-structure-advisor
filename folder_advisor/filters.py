"""重複・旧版・移動提案から除外すべき「構成ファイル」の判定。

`.gitignore` や `__init__.py`、`README.md` のように、各フォルダ／各プロジェクトに
存在するのが正常で、複数箇所に同名・同内容があっても「重複」「旧版」ではない
ファイルを判定する。これらを統合候補・旧版系列から除外し、移動提案でも据え置く。
"""

from __future__ import annotations

import re

# ファイル名そのもの（小文字）で構成ファイルと判定するもの。
STRUCTURAL_FILENAMES: set[str] = {
    ".gitignore", ".gitkeep", ".gitattributes", ".gitmodules",
    ".npmignore", ".dockerignore", ".editorconfig", ".prettierrc",
    ".env", ".env.example", "py.typed", "catkin_ignore",
    "dockerfile", "makefile", "cmakelists.txt",
    "requirements.txt", "package.json", "package-lock.json",
    "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "pipfile", "pipfile.lock",
    "go.mod", "go.sum", "cargo.toml", "cargo.lock",
    "thumbs.db", "desktop.ini", ".ds_store",
}

# 拡張子を除いた語幹（小文字）で構成ファイルと判定するもの。
STRUCTURAL_STEMS: set[str] = {
    "__init__", "__main__", "readme", "license", "licence", "notice",
    "changelog", "changes", "history", "contributing", "authors",
    "codeowners", "index", "conftest", "setup", "manifest", "version",
}


# 版・日付を取り除いた「基準名」が以下の汎用語だけになる場合、自動生成の
# 連番ファイル（スクリーンショット等）であり、資料の「版」ではないため系列にしない。
GENERIC_BASENAMES: set[str] = {
    "screenshot", "スクリーンショット", "スクショ", "capture", "キャプチャ",
    "figure", "image", "img", "photo", "picture", "pic", "写真", "画像",
    "scan", "スキャン", "document", "doc", "untitled", "無題", "名称未設定",
    "new", "新規", "新しいファイル", "download", "ダウンロード", "copy", "コピー",
}


def is_generic_basename(normalized_base: str) -> bool:
    """正規化後の基準名が汎用語（自動生成の連番名など）かを返す。

    末尾の連番 `(0)` や数字（例: "figure (0)" / "screenshot 2"）は自動生成の
    インデックスなので取り除いてから判定する。さらに先頭語が汎用語なら該当と
    みなす（"figure 説明" 等の合成名も自動生成として扱う）。
    """
    b = normalized_base.strip().lower()
    b = re.sub(r"\s*\(\s*\d+\s*\)\s*$", "", b)   # 末尾の (N)
    b = re.sub(r"[\s_\-]*\d+\s*$", "", b).strip()  # 末尾の連番
    if not b:
        return False
    if b in GENERIC_BASENAMES:
        return True
    first = b.split()[0]
    return first in GENERIC_BASENAMES


def is_structural(name: str) -> bool:
    """ファイル名が構成ファイル（各所に存在して当然のもの）かを返す。"""
    low = name.lower()
    if low in STRUCTURAL_FILENAMES:
        return True
    stem = low.rsplit(".", 1)[0] if "." in low[1:] else low
    return stem in STRUCTURAL_STEMS
