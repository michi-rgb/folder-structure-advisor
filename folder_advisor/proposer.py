"""改善後フォルダ構成の提案生成。

方針：
- 第一階層を「資料種別（category）」に統一し、散在を解消する。
- 日付が名前から取れる場合は第二階層に「年度」を置く。
- 完全重複は「正」1 本に統合し、冗長コピーは移動先を持たない（統合）。
- 旧版（系列の最新以外）は各種別配下の `_アーカイブ(旧版)` に隔離提案する。
- 実ファイルは動かさない。結果は移動計画（MovePlanItem）とネスト木で返す。
"""

from __future__ import annotations

import re
from typing import Optional

from .classifier import UNCLASSIFIED
from .models import (
    AnalysisResult,
    DuplicateGroup,
    FileEntry,
    MovePlanItem,
    ScanResult,
    VersionSeries,
)
from .versioning import normalize_name

ARCHIVE_DIR = "_アーカイブ(旧版)"
_DATE_IN_NAME = re.compile(r"(19|20)(\d{2})[-_]?\d{2}[-_]?\d{2}|\b(19|20)(\d{2})\b")


def _fiscal_year(entry: FileEntry) -> Optional[str]:
    """ファイル名から年（西暦）を推定する。更新日には依存しない。"""
    m = _DATE_IN_NAME.search(entry.stem)
    if not m:
        return None
    year = m.group(2) or m.group(4)
    if year:
        full = "20" + year if len(year) == 2 else year
        return f"{full}年"
    return None


def _clean_segment(name: str) -> str:
    """フォルダ名に使えるよう不正文字を除去する。"""
    s = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    return s or "未分類"


def _proposed_path_for(entry: FileEntry, is_old_version: bool) -> str:
    """1 ファイルの提案パスを組み立てる。"""
    category = _clean_segment(entry.category or UNCLASSIFIED)
    parts = [category]
    if is_old_version:
        parts.append(ARCHIVE_DIR)
    year = _fiscal_year(entry)
    if year:
        parts.append(year)
    parts.append(entry.name)
    return "/".join(parts)


def build_proposal(
    scan: ScanResult,
    duplicate_groups: list[DuplicateGroup],
    version_series: list[VersionSeries],
    category_counts: dict[str, int],
    naming_suggestion: Optional[dict] = None,
) -> AnalysisResult:
    """分析結果から改善後ツリーと移動計画を生成する。"""
    by_path = {f.path: f for f in scan.files}

    # 統合対象（冗長コピー）と旧版のパス集合を作る。
    redundant_paths: set[str] = set()
    dup_primary_of: dict[str, str] = {}  # redundant path -> primary path
    for g in duplicate_groups:
        for r in g.redundant:
            redundant_paths.add(r)
            dup_primary_of[r] = g.primary

    old_version_paths: set[str] = set()
    for v in version_series:
        for p in v.older:
            old_version_paths.add(p)

    move_plan: list[MovePlanItem] = []
    kept_files: list[FileEntry] = []  # 提案ツリー構築に使う（統合で消えるものは除く）

    for f in scan.files:
        is_redundant = f.path in redundant_paths
        is_old = f.path in old_version_paths

        if is_redundant:
            # 完全重複の冗長コピーは「正」に統合（移動先は持たない）。
            primary = by_path.get(dup_primary_of[f.path])
            primary_prop = _proposed_path_for(primary, primary.path in old_version_paths) if primary else "(正)"
            move_plan.append(
                MovePlanItem(
                    current_path=f.path,
                    proposed_path=primary_prop,
                    category=f.category or UNCLASSIFIED,
                    action="統合",
                    reason=f"内容が完全一致する重複。正: {dup_primary_of[f.path]} に集約",
                    dup_flag=True,
                    old_version_flag=is_old,
                )
            )
            continue

        proposed = _proposed_path_for(f, is_old)
        if is_old:
            action = "要確認"
            reason = "旧版の可能性（系列の最新以外）。アーカイブへ隔離を提案"
        elif proposed == f.path:
            action = "据置"
            reason = "既に適切な位置にあります"
        else:
            action = "移動"
            reason = f"資料種別『{f.category}』に集約"

        move_plan.append(
            MovePlanItem(
                current_path=f.path,
                proposed_path=proposed,
                category=f.category or UNCLASSIFIED,
                action=action,
                reason=reason,
                dup_flag=False,
                old_version_flag=is_old,
            )
        )
        kept_files.append(f)

    proposed_tree = _build_tree(
        [(_proposed_path_for(f, f.path in old_version_paths)) for f in kept_files]
    )

    # 命名規約案（LLM が無ければ既定案）。
    naming = naming_suggestion or _default_naming_suggestion()

    result = AnalysisResult(
        duplicate_groups=duplicate_groups,
        version_series=version_series,
        move_plan=move_plan,
        category_counts=dict(category_counts),
        proposed_tree=proposed_tree,
    )
    result.summary = _summarize(scan, duplicate_groups, version_series, move_plan, naming)
    return result


def _default_naming_suggestion() -> dict:
    return {
        "naming_rule": "YYYYMMDD_案件名_資料名_版（例: 20240415_A社_見積書_v2）",
        "folder_policy": "第一階層=資料種別、第二階層=年度（必要時）。旧版は各種別の _アーカイブ(旧版) に隔離。",
        "examples": [
            "20240415_定例_議事録",
            "20240401_A社_見積書_v2",
            "20240310_新製品_提案書_確定",
        ],
    }


def _build_tree(paths: list[str]) -> dict:
    """相対パスのリストをネスト辞書に変換する（葉は None）。"""
    tree: dict = {}
    for p in paths:
        node = tree
        parts = p.split("/")
        for i, seg in enumerate(parts):
            is_leaf = i == len(parts) - 1
            if is_leaf:
                node.setdefault("__files__", []).append(seg)
            else:
                node = node.setdefault(seg, {})
    return tree


def _summarize(scan, dups, series, move_plan, naming) -> dict:
    from .duplicates import bytes_reclaimable

    action_counts: dict[str, int] = {}
    for m in move_plan:
        action_counts[m.action] = action_counts.get(m.action, 0) + 1

    return {
        "total_files": len(scan.files),
        "total_dirs": len(scan.dirs),
        "duplicate_groups": len(dups),
        "redundant_copies": sum(len(g.redundant) for g in dups),
        "reclaimable_bytes": bytes_reclaimable(dups),
        "version_series": len(series),
        "old_versions": sum(len(v.older) for v in series),
        "actions": action_counts,
        "naming_suggestion": naming,
    }
