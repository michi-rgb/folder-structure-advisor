"""データモデル定義。

走査・分析・提案の各段階で受け渡すレコードを dataclass で定義する。
JSON への直列化を前提に、値は基本型（str / int / float / bool / None / list / dict）で保持する。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# --- メタデータ信頼度 ---------------------------------------------------------
# 課題 B を踏まえ、値そのものではなく「その値がどれだけ当てにできるか」を持つ。
RELIABILITY_HIGH = "high"
RELIABILITY_LOW = "low"


@dataclass
class FileEntry:
    """走査で得た 1 ファイルの情報。"""

    path: str                    # ルートからの相対パス（区切りは "/" に正規化）
    name: str                    # ファイル名（拡張子込み）
    stem: str                    # 拡張子を除いたファイル名
    ext: str                     # 拡張子（小文字・ドット無し。無い場合は ""）
    parent: str                  # 親フォルダの相対パス（ルート直下は ""）
    size: int                    # バイト数
    created: Optional[str]       # 作成日時（ISO 8601 文字列）
    modified: Optional[str]      # 更新日時（ISO 8601 文字列。※低信頼）
    content_hash: Optional[str] = None   # SHA-256（重複判定の一次情報）
    reliability: dict[str, str] = field(default_factory=dict)  # 各メタの信頼度

    # 分析段階で付与される項目
    dup_group: Optional[str] = None      # 完全重複グループ ID
    version_series: Optional[str] = None  # 旧版系列 ID
    is_latest_in_series: Optional[bool] = None
    category: Optional[str] = None       # 分類ラベル
    category_source: Optional[str] = None  # "rule" / "llm"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DirEntry:
    """走査で得た 1 フォルダの情報。"""

    path: str                    # ルートからの相対パス（ルート自身は ""）
    name: str                    # フォルダ名（ルート自身はルートのベース名）
    parent: str                  # 親フォルダの相対パス
    depth: int                   # ルートからの深さ（ルート = 0）
    file_count: int = 0          # 直下のファイル数
    total_file_count: int = 0    # 配下（再帰）の総ファイル数

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScanResult:
    """走査全体の結果。"""

    root: str                    # 走査したルートの絶対パス
    mode: str                    # "local" / "sharepoint"
    scanned_at: str              # 走査日時（ISO 8601）
    files: list[FileEntry] = field(default_factory=list)
    dirs: list[DirEntry] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)  # 除外・エラー記録

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "mode": self.mode,
            "scanned_at": self.scanned_at,
            "files": [f.to_dict() for f in self.files],
            "dirs": [d.to_dict() for d in self.dirs],
            "skipped": self.skipped,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScanResult":
        result = cls(
            root=data["root"],
            mode=data.get("mode", "local"),
            scanned_at=data.get("scanned_at", ""),
        )
        result.files = [_file_from_dict(d) for d in data.get("files", [])]
        result.dirs = [_dir_from_dict(d) for d in data.get("dirs", [])]
        result.skipped = data.get("skipped", [])
        return result


def _file_from_dict(d: dict[str, Any]) -> FileEntry:
    known = {f for f in FileEntry.__dataclass_fields__}  # noqa: PLC0208
    return FileEntry(**{k: v for k, v in d.items() if k in known})


def _dir_from_dict(d: dict[str, Any]) -> DirEntry:
    known = {f for f in DirEntry.__dataclass_fields__}  # noqa: PLC0208
    return DirEntry(**{k: v for k, v in d.items() if k in known})


# --- 分析結果 -----------------------------------------------------------------
@dataclass
class DuplicateGroup:
    """完全重複（内容ハッシュ一致）のグループ。"""

    group_id: str
    content_hash: str
    size: int
    paths: list[str]                    # 重複している全ファイルの相対パス
    primary: str                        # 「正」候補（1 本）
    redundant: list[str] = field(default_factory=list)  # 統合対象（正以外）

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VersionSeries:
    """同一系列とみなした旧版グループ。"""

    series_id: str
    base_name: str                      # 版表記を除いた基準名
    members: list[str] = field(default_factory=list)      # 相対パス
    latest: Optional[str] = None        # 最新候補
    older: list[str] = field(default_factory=list)        # 旧版候補

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MovePlanItem:
    """移動計画 1 行。実行はしない（提案のみ）。"""

    current_path: str
    proposed_path: str
    category: str
    action: str                         # 移動 / 統合 / 据置 / 要確認
    reason: str
    project: str = ""                   # 束ねたプロジェクト/案件（クラスタ無しは空）
    dup_flag: bool = False
    old_version_flag: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnalysisResult:
    """分析・提案の全体結果。"""

    duplicate_groups: list[DuplicateGroup] = field(default_factory=list)
    version_series: list[VersionSeries] = field(default_factory=list)
    move_plan: list[MovePlanItem] = field(default_factory=list)
    category_counts: dict[str, int] = field(default_factory=dict)
    proposed_tree: dict[str, Any] = field(default_factory=dict)  # 改善後ツリー（ネスト）
    summary: dict[str, Any] = field(default_factory=dict)
    llm_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "duplicate_groups": [g.to_dict() for g in self.duplicate_groups],
            "version_series": [v.to_dict() for v in self.version_series],
            "move_plan": [m.to_dict() for m in self.move_plan],
            "category_counts": self.category_counts,
            "proposed_tree": self.proposed_tree,
            "summary": self.summary,
            "llm_used": self.llm_used,
        }
