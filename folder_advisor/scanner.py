"""フォルダ走査。

`SourceBackend` を抽象境界として持ち、第一版は `LocalBackend`（os.walk）を実装する。
SharePoint は OneDrive 同期済みフォルダをローカルパスとして扱うため、同じ
`LocalBackend` で走査できる。将来 Graph API 直結を追加する場合は同じインターフェースで
`GraphBackend` を実装すればよい。
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .models import DirEntry, FileEntry, ScanResult

# 走査から除外するファイル名・拡張子・フォルダ名。
DEFAULT_EXCLUDE_FILE_PREFIXES = ("~$",)          # Office 一時ファイル
DEFAULT_EXCLUDE_FILE_NAMES = {"Thumbs.db", "desktop.ini", ".DS_Store"}
DEFAULT_EXCLUDE_EXTS = {"tmp", "temp", "lnk"}
DEFAULT_EXCLUDE_DIRS = {".git", ".svn", "__pycache__", "node_modules", ".venv"}


def _iso(ts: float) -> Optional[str]:
    """POSIX タイムスタンプを ISO 8601（ローカルタイム）に。失敗時 None。"""
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _normalize_rel(path: Path, root: Path) -> str:
    """ルート相対パスを "/" 区切りの文字列に。ルート自身は ""。"""
    rel = path.relative_to(root)
    s = rel.as_posix()
    return "" if s == "." else s


class SourceBackend(ABC):
    """走査バックエンドの抽象。"""

    @abstractmethod
    def scan(self) -> ScanResult:  # pragma: no cover - 抽象
        ...


class LocalBackend(SourceBackend):
    """ローカル / SharePoint 同期フォルダを os.walk で走査する。"""

    def __init__(
        self,
        root: str,
        mode: str = "local",
        max_files: Optional[int] = None,
        exclude_dirs: Optional[set[str]] = None,
        exclude_exts: Optional[set[str]] = None,
        follow_symlinks: bool = False,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.mode = mode
        self.max_files = max_files
        self.exclude_dirs = exclude_dirs or set(DEFAULT_EXCLUDE_DIRS)
        self.exclude_exts = exclude_exts or set(DEFAULT_EXCLUDE_EXTS)
        self.follow_symlinks = follow_symlinks

    # -- 除外判定 --
    def _skip_file(self, name: str, ext: str) -> Optional[str]:
        if name in DEFAULT_EXCLUDE_FILE_NAMES:
            return "excluded-name"
        if any(name.startswith(p) for p in DEFAULT_EXCLUDE_FILE_PREFIXES):
            return "temp-file"
        if ext in self.exclude_exts:
            return "excluded-ext"
        return None

    def scan(self) -> ScanResult:
        if not self.root.exists():
            raise FileNotFoundError(f"対象フォルダが存在しません: {self.root}")
        if not self.root.is_dir():
            raise NotADirectoryError(f"フォルダではありません: {self.root}")

        result = ScanResult(
            root=str(self.root),
            mode=self.mode,
            scanned_at=datetime.now().astimezone().isoformat(),
        )
        # 直下ファイル数の集計用（path -> count）。
        direct_counts: dict[str, int] = {}
        reached_limit = False

        for dirpath, dirnames, filenames in os.walk(
            self.root, followlinks=self.follow_symlinks
        ):
            # 除外フォルダは走査対象から外す（in-place で dirnames を編集）。
            dirnames[:] = [d for d in dirnames if d not in self.exclude_dirs]

            cur = Path(dirpath)
            rel = _normalize_rel(cur, self.root)
            depth = 0 if rel == "" else rel.count("/") + 1
            parent = "" if rel == "" else _normalize_rel(cur.parent, self.root)

            n_direct = 0
            for fname in filenames:
                if self.max_files is not None and len(result.files) >= self.max_files:
                    reached_limit = True
                    break

                fpath = cur / fname
                ext = fpath.suffix.lower().lstrip(".")
                skip_reason = self._skip_file(fname, ext)
                if skip_reason:
                    result.skipped.append(
                        {"path": _normalize_rel(fpath, self.root), "reason": skip_reason}
                    )
                    continue

                try:
                    st = fpath.stat()
                except OSError as exc:
                    result.skipped.append(
                        {
                            "path": _normalize_rel(fpath, self.root),
                            "reason": "stat-error",
                            "detail": str(exc),
                        }
                    )
                    continue

                frel = _normalize_rel(fpath, self.root)
                result.files.append(
                    FileEntry(
                        path=frel,
                        name=fname,
                        stem=fpath.stem,
                        ext=ext,
                        parent=rel,
                        size=st.st_size,
                        created=_iso(st.st_ctime),
                        modified=_iso(st.st_mtime),
                    )
                )
                n_direct += 1

            direct_counts[rel] = n_direct
            result.dirs.append(
                DirEntry(path=rel, name=(cur.name if rel else self.root.name),
                         parent=parent, depth=depth, file_count=n_direct)
            )

            if reached_limit:
                break

        _accumulate_totals(result.dirs)
        return result


def _accumulate_totals(dirs: list[DirEntry]) -> None:
    """各フォルダの配下総ファイル数（total_file_count）を集計する。"""
    by_path = {d.path: d for d in dirs}
    # 深い順に親へ積み上げる。
    for d in sorted(dirs, key=lambda x: x.depth, reverse=True):
        d.total_file_count += d.file_count
        if d.path == "":
            continue
        parent = by_path.get(d.parent)
        if parent is not None:
            parent.total_file_count += d.total_file_count


def get_backend(source: str, mode: str = "local", **kwargs) -> SourceBackend:
    """モードに応じたバックエンドを返す。

    現状 local / sharepoint はいずれも LocalBackend（同期パス前提）。
    """
    if mode in ("local", "sharepoint"):
        return LocalBackend(source, mode=mode, **kwargs)
    raise ValueError(f"未対応の mode: {mode}")


def scan_source(source: str, mode: str = "local", **kwargs) -> ScanResult:
    return get_backend(source, mode=mode, **kwargs).scan()
