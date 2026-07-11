"""重複検出。

- 完全重複：内容ハッシュ（SHA-256）一致でグループ化。当てにできる一次情報。
- 近似重複：版・日付表記を除いた基準名の一致で候補提示（内容は別でも名前が酷似）。

「正」候補の選び方（完全重複時）：パスが浅い → パスに正式版らしい語を含む →
名前が短い（コピー等の付加が少ない）→ パス文字列、の順で決定的に選ぶ。
"""

from __future__ import annotations

import re
from collections import defaultdict

from .filters import is_structural
from .models import DuplicateGroup, FileEntry
from .versioning import normalize_name

# 「正」らしさを示す語（パスに含まれると優先度が上がる）。
_OFFICIAL_HINT = re.compile(r"(正式|最新|確定|公開|master|current|正)", re.IGNORECASE)
# 「正」らしくない語（コピー・個人保管・旧など）。
_UNOFFICIAL_HINT = re.compile(r"(コピー|copy|旧|old|backup|bak|作業中|wip|個人|temp|tmp|一時)", re.IGNORECASE)


def _primary_score(entry: FileEntry) -> tuple:
    """大きいほど「正」にふさわしい。"""
    depth = entry.path.count("/")
    official = 1 if _OFFICIAL_HINT.search(entry.path) else 0
    unofficial = 1 if _UNOFFICIAL_HINT.search(entry.path) else 0
    # 浅い / official語あり / unofficial語なし / 名前が短い / パス辞書順（安定化）
    return (-depth, official, -unofficial, -len(entry.name), entry.path)


def detect_exact_duplicates(
    files: list[FileEntry],
    min_size: int = 1,
    exclude_structural: bool = True,
) -> list[DuplicateGroup]:
    """内容ハッシュ一致の完全重複グループを返す。

    ノイズ除外:
    - `min_size` 未満（既定 1）＝ 0 バイトの空ファイルは対象外。空ファイルは
      全て同一ハッシュになり、無関係なファイルが 1 グループに束ねられてしまうため。
    - 構成ファイル（.gitignore / __init__.py 等）は各所に存在して当然で「統合」
      すべきでないため対象外。
    """
    by_hash: dict[str, list[FileEntry]] = defaultdict(list)
    for f in files:
        if not f.content_hash:
            continue
        if f.size < min_size:
            continue
        if exclude_structural and is_structural(f.name):
            continue
        by_hash[f.content_hash].append(f)

    groups: list[DuplicateGroup] = []
    idx = 0
    for content_hash, members in by_hash.items():
        if len(members) < 2:
            continue
        idx += 1
        gid = f"dup-{idx:04d}"
        primary = max(members, key=_primary_score)
        redundant = [f.path for f in members if f.path != primary.path]
        for f in members:
            f.dup_group = gid
        groups.append(
            DuplicateGroup(
                group_id=gid,
                content_hash=content_hash,
                size=primary.size,
                paths=[f.path for f in members],
                primary=primary.path,
                redundant=redundant,
            )
        )
    # 影響の大きい順（冗長数 × サイズ）に並べる。
    groups.sort(key=lambda g: len(g.redundant) * g.size, reverse=True)
    return groups


def detect_near_duplicates(files: list[FileEntry]) -> list[dict]:
    """基準名一致の近似重複候補（完全重複ではないもの）を返す。"""
    by_base: dict[str, list[FileEntry]] = defaultdict(list)
    for f in files:
        base = normalize_name(f.stem)
        if base:
            by_base[f"{base}|{f.ext}"].append(f)

    result: list[dict] = []
    for key, members in by_base.items():
        if len(members) < 2:
            continue
        hashes = {f.content_hash for f in members if f.content_hash}
        # 内容が全て同一なら完全重複側で扱うのでスキップ。
        if len(hashes) <= 1 and None not in {f.content_hash for f in members}:
            continue
        result.append(
            {
                "base_name": key.split("|", 1)[0],
                "ext": key.split("|", 1)[1],
                "paths": [f.path for f in members],
                "count": len(members),
            }
        )
    result.sort(key=lambda x: x["count"], reverse=True)
    return result


def bytes_reclaimable(groups: list[DuplicateGroup]) -> int:
    """完全重複を統合した場合に削減できる概算バイト数。"""
    return sum(g.size * len(g.redundant) for g in groups)
