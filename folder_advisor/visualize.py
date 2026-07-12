"""before / after の可視化データ生成。

- 折り畳みツリー・容量ヒートマップ（Treemap）用の、名前昇順で正規化した
  ネスト木データを生成する。
- グラフ JSON（nodes / edges）も生成し、他ツール連携・再描画に使える形で出す。
"""

from __future__ import annotations

from collections import defaultdict

from .models import ScanResult


# --- ネスト木データ（折り畳みツリー / Treemap 用） ---------------------------
# 縦に伸びる折り畳みツリーや面積表現の Treemap で使うため、名前昇順で正規化した
# ネスト木を生成する。ノードリンク図（mermaid 等）は大規模ツリーで字が潰れ・
# エッジが重なるため用いない。各ノードは name / count（直下ファイル数）/
# total（配下総ファイル数＝面積の重み）/ children を持つ。
def before_tree_data(scan: ScanResult) -> dict:
    """走査結果（現状）を name 昇順のネスト木データにする。"""
    children: dict[str, list[str]] = defaultdict(list)
    names: dict[str, str] = {}
    file_counts: dict[str, int] = {}
    total_counts: dict[str, int] = {}
    for d in scan.dirs:
        names[d.path] = d.name
        file_counts[d.path] = d.file_count
        total_counts[d.path] = d.total_file_count
        if d.path != "":
            children[d.parent].append(d.path)

    def node(path: str) -> dict:
        kids = sorted(children.get(path, []), key=lambda c: names.get(c, c))
        return {
            "name": names.get(path, "") or "（ルート）",
            "count": file_counts.get(path, 0),
            "total": total_counts.get(path, 0),
            "children": [node(c) for c in kids],
        }

    return node("")


def after_tree_data(proposed_tree: dict, root_name: str = "（改善後ルート）") -> dict:
    """提案ツリー（ネスト辞書）を name 昇順のネスト木データにする。"""

    def node(name: str, sub: dict) -> dict:
        files = sub.get("__files__", [])
        kids = sorted(k for k in sub if k != "__files__")
        children = [node(k, sub[k]) for k in kids]
        total = len(files) + sum(c["total"] for c in children)
        return {
            "name": name,
            "count": len(files),
            "total": total,
            "children": children,
        }

    root = node(root_name, proposed_tree)
    return root


# --- グラフ JSON -------------------------------------------------------------
def before_graph_json(scan: ScanResult) -> dict:
    """現状フォルダ構造を nodes/edges の JSON にする。"""
    nodes = []
    edges = []
    for d in sorted(scan.dirs, key=lambda d: (d.depth, d.name)):
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
        for name in sorted(k for k in node if k != "__files__"):
            child = node[name]
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
