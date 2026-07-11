"""before / after の可視化。

- mermaid（flowchart TD）テキストを生成。大規模ツリーは深さ・子ノード数の上限で
  「他 N 件」の集約ノードに折り畳み、mermaid が破綻しないようにする。
- グラフ JSON（nodes / edges）も生成し、他ツール連携・再描画に使える形で出す。
"""

from __future__ import annotations

import re
from collections import defaultdict

from .models import ScanResult

DEFAULT_MAX_DEPTH = 3
DEFAULT_MAX_CHILDREN = 12
# mermaid のフロー方向。LR=左から右、TD=上から下。
DEFAULT_DIRECTION = "LR"


# --- mermaid 用のユーティリティ ---------------------------------------------
def _mid(counter: list[int]) -> str:
    counter[0] += 1
    return f"n{counter[0]}"


def _esc(label: str) -> str:
    """mermaid ラベル内で問題になる文字を無害化する。"""
    label = label.replace('"', "'").replace("\n", " ")
    # 角括弧・波括弧はノード構文と衝突するため全角化。
    label = label.replace("[", "［").replace("]", "］")
    return label


def _tree_from_dirs(scan: ScanResult) -> dict:
    """走査結果（before）を親子ネスト辞書にする。値は子 dict、__files__ に件数。"""
    children: dict[str, list[str]] = defaultdict(list)
    file_counts: dict[str, int] = {}
    names: dict[str, str] = {}
    for d in scan.dirs:
        names[d.path] = d.name
        file_counts[d.path] = d.file_count
        if d.path != "":
            children[d.parent].append(d.path)
    return {"children": children, "file_counts": file_counts, "names": names}


def before_mermaid(
    scan: ScanResult,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_children: int = DEFAULT_MAX_CHILDREN,
    direction: str = DEFAULT_DIRECTION,
) -> str:
    """走査結果（現状）を mermaid flowchart にする。"""
    info = _tree_from_dirs(scan)
    children = info["children"]
    file_counts = info["file_counts"]
    names = info["names"]
    counter = [0]
    lines = [f"flowchart {direction}"]

    def emit(path: str, depth: int) -> str:
        nid = _mid(counter)
        label = names.get(path, path) or "（ルート）"
        fc = file_counts.get(path, 0)
        suffix = f"<br/>{fc}ファイル" if fc else ""
        lines.append(f'    {nid}["{_esc(label)}{suffix}"]')
        if depth >= max_depth:
            subdirs = children.get(path, [])
            if subdirs:
                cid = _mid(counter)
                lines.append(f'    {cid}["... 他 {len(subdirs)} フォルダ"]')
                lines.append(f"    {nid} --> {cid}")
            return nid
        kids = children.get(path, [])
        for child in kids[:max_children]:
            child_id = emit(child, depth + 1)
            lines.append(f"    {nid} --> {child_id}")
        if len(kids) > max_children:
            cid = _mid(counter)
            lines.append(f'    {cid}["... 他 {len(kids) - max_children} フォルダ"]')
            lines.append(f"    {nid} --> {cid}")
        return nid

    emit("", 0)
    return "\n".join(lines)


def after_mermaid(
    proposed_tree: dict,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_children: int = DEFAULT_MAX_CHILDREN,
    direction: str = DEFAULT_DIRECTION,
) -> str:
    """提案ツリー（ネスト辞書）を mermaid flowchart にする。"""
    counter = [0]
    lines = [f"flowchart {direction}"]
    root_id = _mid(counter)
    lines.append(f'    {root_id}["（改善後ルート）"]')

    def emit(node: dict, parent_id: str, depth: int) -> None:
        subdirs = [(k, v) for k, v in node.items() if k != "__files__"]
        files = node.get("__files__", [])
        if depth >= max_depth:
            total = len(subdirs)
            if total:
                cid = _mid(counter)
                lines.append(f'    {cid}["... 他 {total} フォルダ"]')
                lines.append(f"    {parent_id} --> {cid}")
            return
        for name, child in subdirs[:max_children]:
            nid = _mid(counter)
            fc = len(child.get("__files__", []))
            suffix = f"<br/>{fc}ファイル" if fc else ""
            lines.append(f'    {nid}["{_esc(name)}{suffix}"]')
            lines.append(f"    {parent_id} --> {nid}")
            emit(child, nid, depth + 1)
        if len(subdirs) > max_children:
            cid = _mid(counter)
            lines.append(f'    {cid}["... 他 {len(subdirs) - max_children} フォルダ"]')
            lines.append(f"    {parent_id} --> {cid}")
        # ルート直下ファイルがある場合のみ件数表示。
        if files and depth == 0:
            fid = _mid(counter)
            lines.append(f'    {fid}["直下 {len(files)} ファイル"]')
            lines.append(f"    {parent_id} --> {fid}")

    emit(proposed_tree, root_id, 0)
    return "\n".join(lines)


# --- グラフ JSON -------------------------------------------------------------
def before_graph_json(scan: ScanResult) -> dict:
    """現状フォルダ構造を nodes/edges の JSON にする。"""
    nodes = []
    edges = []
    for d in scan.dirs:
        nid = d.path or "__root__"
        nodes.append(
            {
                "id": nid,
                "label": d.name,
                "type": "dir",
                "depth": d.depth,
                "file_count": d.file_count,
                "total_file_count": d.total_file_count,
            }
        )
        if d.path != "":
            edges.append({"source": d.parent or "__root__", "target": nid})
    return {"nodes": nodes, "edges": edges}


def after_graph_json(proposed_tree: dict) -> dict:
    """提案ツリーを nodes/edges の JSON にする。"""
    nodes = [{"id": "__root__", "label": "（改善後ルート）", "type": "dir", "depth": 0}]
    edges = []
    counter = [0]

    def walk(node: dict, parent_id: str, depth: int) -> None:
        for name, child in node.items():
            if name == "__files__":
                continue
            counter[0] += 1
            nid = f"{parent_id}/{name}"
            fc = len(child.get("__files__", []))
            nodes.append(
                {"id": nid, "label": name, "type": "dir", "depth": depth, "file_count": fc}
            )
            edges.append({"source": parent_id, "target": nid})
            walk(child, nid, depth + 1)

    walk(proposed_tree, "__root__", 1)
    return {"nodes": nodes, "edges": edges}
