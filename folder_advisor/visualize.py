"""before / after の可視化データ生成。

大規模なフォルダ構成では mermaid 図が巨大化して判読不能になるため廃止し、
代わりに HTML 側で描画する 2 種類のデータを生成する。

- **折り畳みツリー**：フォルダ／ファイルの親子ネスト木（名前・種別・サイズ・
  ファイル数）。HTML の JS が展開／折り畳み可能なツリーとして描画する。
- **Treemap（容量ヒートマップ）**：同じネスト木を面積＝容量で並べ、色の濃淡で
  容量の大小を表す。どのフォルダ・ファイルが容量を食っているかを一目で掴む。

グラフ JSON（nodes / edges）も従来どおり生成し、他ツール連携に使える形で出す。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from .models import AnalysisResult, ScanResult


# --- 折り畳みツリー / Treemap 用のネスト木 ------------------------------------
def _new_dir_node(name: str) -> dict[str, Any]:
    """ディレクトリノードの器を作る。size は配下の総バイト、file_count は直下ファイル数。"""
    return {"name": name, "type": "dir", "size": 0, "file_count": 0, "children": []}


def _finalize(node: dict[str, Any]) -> dict[str, Any]:
    """子を並べ替える（フォルダ→ファイル、各々サイズ降順）。"""
    if node.get("type") != "dir":
        return node
    for child in node["children"]:
        _finalize(child)
    node["children"].sort(
        key=lambda c: (0 if c["type"] == "dir" else 1, -c.get("size", 0), c["name"])
    )
    return node


def before_tree_data(scan: ScanResult) -> dict[str, Any]:
    """走査結果（現状）を折り畳みツリー / Treemap 用のネスト木にする。"""
    # 相対パス -> ディレクトリノード。ルートは "" をキーに持つ。
    by_path: dict[str, dict[str, Any]] = {}
    root_name = "（ルート）"
    for d in scan.dirs:
        by_path[d.path] = _new_dir_node(d.name or root_name)
        if d.path == "":
            root_name = d.name or root_name

    root = by_path.get("")
    if root is None:
        root = _new_dir_node(root_name)
        by_path[""] = root

    # ディレクトリを親子で連結する。
    for d in scan.dirs:
        if d.path == "":
            continue
        parent = by_path.get(d.parent)
        if parent is None:
            parent = root
        parent["children"].append(by_path[d.path])

    # ファイルを親フォルダに載せ、サイズを祖先へ加算する。
    for f in scan.files:
        parent = by_path.get(f.parent) or root
        parent["children"].append({"name": f.name, "type": "file", "size": f.size})
        parent["file_count"] += 1
        _add_size_up(by_path, f.parent, f.size)

    return _finalize(root)


def _add_size_up(by_path: dict[str, dict[str, Any]], start: str, size: int) -> None:
    """start フォルダから祖先まで size を加算する（区切りは "/"）。"""
    path = start
    while True:
        node = by_path.get(path)
        if node is not None:
            node["size"] += size
        if path == "":
            break
        idx = path.rfind("/")
        path = path[:idx] if idx >= 0 else ""


def after_tree_data(scan: ScanResult, analysis: AnalysisResult) -> dict[str, Any]:
    """提案（改善後）を折り畳みツリー / Treemap 用のネスト木にする。

    移動計画の提案パスにファイルを配置する。統合（冗長コピー）は正へ集約されて
    消えるためツリーには含めない（＝容量削減が Treemap の面積にも反映される）。
    サイズは元ファイル（current_path）の実バイト数を用いる。
    """
    size_of = {f.path: f.size for f in scan.files}
    root = _new_dir_node("（改善後ルート）")
    dir_index: dict[str, dict[str, Any]] = {"": root}

    for m in analysis.move_plan:
        if m.action == "統合":
            continue  # 冗長コピーは正へ集約され消える
        size = size_of.get(m.current_path, 0)
        parts = [p for p in m.proposed_path.split("/") if p != ""]
        if not parts:
            continue
        *dir_parts, file_name = parts
        parent = root
        acc = ""
        for seg in dir_parts:
            acc = f"{acc}/{seg}" if acc else seg
            child = dir_index.get(acc)
            if child is None:
                child = _new_dir_node(seg)
                parent["children"].append(child)
                dir_index[acc] = child
            parent = child
        parent["children"].append({"name": file_name, "type": "file", "size": size})
        parent["file_count"] += 1
        # サイズを祖先へ加算する。
        acc = ""
        chain = [root]
        for seg in dir_parts:
            acc = f"{acc}/{seg}" if acc else seg
            chain.append(dir_index[acc])
        for node in chain:
            node["size"] += size

    return _finalize(root)


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
