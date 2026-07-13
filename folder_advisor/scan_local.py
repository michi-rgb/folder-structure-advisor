"""ローカル / OneDrive 同期フォルダのスキャン（メタデータのみ）。

通信量削減の要点:
- ファイルを一切 open しない。os.scandir の stat 情報だけを使う。
  OneDrive「ファイルオンデマンド」のプレースホルダ（クラウド専用ファイル）は
  内容にアクセスしない限りダウンロードされないため、このスキャンで発生する
  ネットワーク通信はゼロ。
- Windows ではファイル属性からクラウド専用ファイルを識別してカウントする
  （FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS / OFFLINE）。
"""
from __future__ import annotations

import fnmatch
import os
import stat as stat_mod
from datetime import datetime, timezone

from folder_advisor.models import (
    FolderStat,
    ScanResult,
    name_signals,
    series_key,
)

SAMPLES_PER_DIR = 8

DEFAULT_EXCLUDES = [
    ".git", ".svn", "__pycache__", "node_modules", ".venv", "venv",
    "$RECYCLE.BIN", "System Volume Information", ".Trash*",
    "Thumbs.db", "desktop.ini", "~$*", "*.tmp", ".DS_Store",
]

# Windows: クラウド専用（未ダウンロード）を示す属性
_ATTR_RECALL_ON_DATA_ACCESS = 0x00400000
_ATTR_RECALL_ON_OPEN = 0x00040000
_ATTR_OFFLINE = 0x00001000
_CLOUD_ATTRS = _ATTR_RECALL_ON_DATA_ACCESS | _ATTR_RECALL_ON_OPEN | _ATTR_OFFLINE


def _is_cloud_only(st: os.stat_result) -> bool:
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & _CLOUD_ATTRS)


def _excluded(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def _month(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m")
    except (OverflowError, OSError, ValueError):
        return ""


def scan_local(
    source: str,
    excludes: list[str] | None = None,
    max_folders: int = 20000,
) -> ScanResult:
    root = os.path.abspath(source)
    if not os.path.isdir(root):
        raise FileNotFoundError(f"フォルダが見つかりません: {source}")
    patterns = DEFAULT_EXCLUDES + (excludes or [])

    result = ScanResult(
        source=root,
        backend="local",
        scanned_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    stack = [root]
    while stack:
        if len(result.folders) >= max_folders:
            result.truncated = True
            break
        dirpath = stack.pop()
        rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
        rel = "" if rel == "." else rel
        fs = FolderStat(path=rel, depth=0 if not rel else rel.count("/") + 1)

        series: dict[str, int] = {}
        latest_ts = 0.0
        try:
            entries = sorted(os.scandir(dirpath), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            if _excluded(entry.name, patterns):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    fs.n_subdirs += 1
                    stack.append(entry.path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            fs.n_files += 1
            fs.size += st.st_size
            latest_ts = max(latest_ts, st.st_mtime)
            if _is_cloud_only(st):
                fs.n_cloud_only += 1
            ext = os.path.splitext(entry.name)[1].lstrip(".").lower() or "(なし)"
            fs.exts[ext] = fs.exts.get(ext, 0) + 1
            if len(fs.samples) < SAMPLES_PER_DIR:
                fs.samples.append(entry.name)
            has_ver, is_wip, is_final = name_signals(entry.name)
            fs.n_versioned += has_ver
            fs.n_wip += is_wip
            fs.n_final += is_final
            key = series_key(entry.name)
            if key:
                series[key] = series.get(key, 0) + 1
        fs.max_series = max(series.values(), default=0)
        if latest_ts:
            fs.last_modified = _month(latest_ts)
        result.folders.append(fs)

    result.folders.sort(key=lambda f: f.path)
    return result
