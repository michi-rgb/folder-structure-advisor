"""改善後フォルダ構成の提案生成。

方針（構造変更を最小化しつつ働き方改革の目的を達成する）:
- **既存のフォルダ構造を土台に据え、既定はすべて「据置」**（現在地維持）。
  第一階層を資料種別で作り直すような大改造はしない（ユーザーが慣れ直す負担を避ける）。
- **散在の集約はプロジェクト単位**で行う。LLM が束ねたプロジェクトのメンバーが
  複数フォルダに散らばっている場合のみ、最も多く集まっている**既存フォルダ**
  （ホーム）へ寄せる提案を出す。ファイル種別では束ねない。
- **完全重複**は「正」1 本へ統合（冗長コピーは移動先を持たない）。
- **旧版**（系列の最新以外）は、そのプロジェクトのホーム（無ければ現在地）配下の
  `_アーカイブ(旧版)` にローカル隔離する。
- 実ファイルは動かさない。結果は移動計画（MovePlanItem）とネスト木で返す。
"""

from __future__ import annotations

import re
from typing import Optional

from .classifier import UNCLASSIFIED
from .clustering import assign_projects, consolidation_target, resolve_home_folders
from .filters import is_structural
from .models import (
    AnalysisResult,
    DuplicateGroup,
    FileEntry,
    MovePlanItem,
    ScanResult,
    VersionSeries,
)

ARCHIVE_DIR = "_アーカイブ(旧版)"


def _clean_segment(name: str) -> str:
    """フォルダ名に使えるよう不正文字を除去する。"""
    s = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    return s or "未分類"


def _join(parts: list[str]) -> str:
    """空セグメントを除いて相対パスに連結する。"""
    return "/".join(p for p in parts if p != "")


def build_proposal(
    scan: ScanResult,
    duplicate_groups: list[DuplicateGroup],
    version_series: list[VersionSeries],
    category_counts: dict[str, int],
    project_of: Optional[dict[str, str]] = None,
) -> AnalysisResult:
    """分析結果から改善後ツリーと移動計画を生成する。

    project_of は {相対パス: プロジェクトラベル}（LLM 由来が主、無ければ空）。
    与えられた場合のみ、散在したプロジェクトのメンバーをホームフォルダへ集約する。
    """
    by_path = {f.path: f for f in scan.files}

    # プロジェクト割当（構成ファイル除外・不正ラベル除去）とホームフォルダ決定。
    projects = assign_projects(scan.files, project_of)
    home_of = resolve_home_folders(scan.files, projects)

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

    def base_folder_for(f: FileEntry) -> str:
        """ファイルの提案上の所属フォルダ（既存フォルダ）を返す。

        集約対象（一時/個人置き場にある散在メンバー）のみホームへ寄せ、それ以外は
        現在の親のまま（＝据置）。判定は clustering.consolidation_target に委譲する。
        """
        target = consolidation_target(f, projects, home_of)
        return target if target is not None else f.parent

    def proposed_path_for(f: FileEntry, is_old: bool) -> str:
        """1 ファイルの提案パスを組み立てる。"""
        base = base_folder_for(f)
        segments = [_clean_segment(s) for s in base.split("/") if s != ""]
        if is_old:
            segments.append(ARCHIVE_DIR)
        segments.append(f.name)
        return _join(segments)

    move_plan: list[MovePlanItem] = []
    # 提案ツリー構築に使う提案パス（統合で消える冗長コピーは含めない）。
    kept_proposed: list[str] = []

    for f in scan.files:
        project = projects.get(f.path, "")

        # 構成ファイル（.gitignore / __init__.py 等）は移動せず据え置く。
        # 各フォルダに存在することに意味があり、集約するとプロジェクトが壊れるため。
        if is_structural(f.name):
            move_plan.append(
                MovePlanItem(
                    current_path=f.path,
                    proposed_path=f.path,
                    category=f.category or UNCLASSIFIED,
                    action="据置",
                    reason="プロジェクト構成ファイル（各フォルダに存在して当然）のため据置",
                    project=project,
                )
            )
            kept_proposed.append(f.path)
            continue

        is_redundant = f.path in redundant_paths
        is_old = f.path in old_version_paths

        if is_redundant:
            # 完全重複の冗長コピーは「正」に統合（移動先は持たない）。
            primary = by_path.get(dup_primary_of[f.path])
            primary_prop = (
                proposed_path_for(primary, primary.path in old_version_paths)
                if primary
                else "(正)"
            )
            move_plan.append(
                MovePlanItem(
                    current_path=f.path,
                    proposed_path=primary_prop,
                    category=f.category or UNCLASSIFIED,
                    action="統合",
                    reason=f"内容が完全一致する重複。正: {dup_primary_of[f.path]} に集約",
                    project=project,
                    dup_flag=True,
                    old_version_flag=is_old,
                )
            )
            continue

        proposed = proposed_path_for(f, is_old)
        if is_old:
            action = "要確認"
            home = base_folder_for(f) or "（ルート）"
            reason = f"旧版の可能性（系列の最新以外）。{home} 配下の {ARCHIVE_DIR} へ隔離を提案"
        elif proposed == f.path:
            action = "据置"
            if project:
                reason = f"プロジェクト『{project}』の集約先に既に在るため据置"
            else:
                reason = "現状維持（構造変更なし）"
        else:
            action = "移動"
            home = base_folder_for(f) or "（ルート）"
            reason = (
                f"プロジェクト『{project}』のファイルが複数フォルダに散在。"
                f"集約先 {home} に寄せる"
            )

        move_plan.append(
            MovePlanItem(
                current_path=f.path,
                proposed_path=proposed,
                category=f.category or UNCLASSIFIED,
                action=action,
                reason=reason,
                project=project,
                dup_flag=False,
                old_version_flag=is_old,
            )
        )
        kept_proposed.append(proposed)

    proposed_tree = _build_tree(kept_proposed)

    result = AnalysisResult(
        duplicate_groups=duplicate_groups,
        version_series=version_series,
        move_plan=move_plan,
        category_counts=dict(category_counts),
        proposed_tree=proposed_tree,
    )
    result.summary = _summarize(scan, duplicate_groups, version_series, move_plan)
    return result


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


def _summarize(scan, dups, series, move_plan) -> dict:
    from .duplicates import bytes_reclaimable

    action_counts: dict[str, int] = {}
    for m in move_plan:
        action_counts[m.action] = action_counts.get(m.action, 0) + 1

    projects = {m.project for m in move_plan if m.project}

    return {
        "total_files": len(scan.files),
        "total_dirs": len(scan.dirs),
        "duplicate_groups": len(dups),
        "redundant_copies": sum(len(g.redundant) for g in dups),
        "reclaimable_bytes": bytes_reclaimable(dups),
        "version_series": len(series),
        "old_versions": sum(len(v.older) for v in series),
        "projects": len(projects),
        "actions": action_counts,
    }
