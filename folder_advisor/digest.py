"""LLM に送るフォルダ体系ダイジェストの生成。

通信量・トークン削減の要点:
- ファイル一覧は送らない。フォルダ単位の 1 行サマリ（件数・容量・拡張子・
  代表ファイル名few件・版乱立度）に圧縮する。
- フォルダ数が上限（既定 400）を超える場合は、浅い階層と「重い」フォルダを
  優先して残し、省略分は親フォルダに「+N サブフォルダ省略」と注記する。
"""
from __future__ import annotations

from folder_advisor.models import FolderStat, ScanResult

DEFAULT_MAX_FOLDERS = 400
ALWAYS_KEEP_DEPTH = 2  # この深さまでは必ず含める


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n}B"


def _score(f: FolderStat) -> float:
    """ダイジェストに残す優先度。ファイル数・容量・課題シグナルが大きいほど高い。"""
    return f.n_files + f.size / 1_000_000 + 20 * f.max_series + 5 * (f.n_wip + f.n_final)


def select_folders(scan: ScanResult, max_folders: int = DEFAULT_MAX_FOLDERS) -> tuple[list[FolderStat], dict[str, int]]:
    """含めるフォルダの選定。返り値: (選定フォルダ, 親パス→省略サブフォルダ数)。"""
    folders = scan.folders
    if len(folders) <= max_folders:
        return folders, {}

    keep = {f.path for f in folders if f.depth <= ALWAYS_KEEP_DEPTH}
    rest = sorted((f for f in folders if f.path not in keep), key=_score, reverse=True)
    for f in rest:
        if len(keep) >= max_folders:
            break
        keep.add(f.path)
        # 選ばれたフォルダの祖先も文脈として必要
        parts = f.path.split("/")
        for i in range(1, len(parts)):
            keep.add("/".join(parts[:i]))

    omitted: dict[str, int] = {}
    for f in folders:
        if f.path not in keep:
            parent = f.path.rsplit("/", 1)[0] if "/" in f.path else ""
            omitted[parent] = omitted.get(parent, 0) + 1
    selected = [f for f in folders if f.path in keep]
    return selected, omitted


def build_digest(scan: ScanResult, max_folders: int = DEFAULT_MAX_FOLDERS) -> str:
    selected, omitted = select_folders(scan, max_folders)
    lines: list[str] = []
    for f in selected:
        path = f.path or "(ルート)"
        exts = ",".join(f"{k}:{v}" for k, v in sorted(f.exts.items(), key=lambda kv: -kv[1])[:5])
        parts = [f"{path} | files={f.n_files} subdirs={f.n_subdirs} size={_fmt_size(f.size)}"]
        if exts:
            parts.append(f"ext[{exts}]")
        if f.last_modified:
            parts.append(f"last={f.last_modified}")
        signals = []
        if f.max_series >= 2:
            signals.append(f"版乱立x{f.max_series}")
        if f.n_wip:
            signals.append(f"作業中{f.n_wip}")
        if f.n_final:
            signals.append(f"確定{f.n_final}")
        if signals:
            parts.append("signal[" + ",".join(signals) + "]")
        if f.samples:
            parts.append("例[" + "; ".join(f.samples[:4]) + "]")
        if f.path in omitted:
            parts.append(f"(+{omitted[f.path]}サブフォルダ省略)")
        lines.append("- " + " ".join(parts))
    if "" in omitted and not any(f.path == "" for f in selected):
        lines.append(f"- (ルート直下 +{omitted['']} フォルダ省略)")
    return "\n".join(lines)
