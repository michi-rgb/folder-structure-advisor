"""データモデル。

スキャン結果は「フォルダ単位の集計」だけを持つ。ファイル内容はもちろん、
ファイル一覧そのものも保存しない（サンプル名を少数残すのみ）。これにより
- OneDrive のクラウド専用ファイルをダウンロードせずに済む（通信量ゼロ）
- LLM に送るデータが小さくなる（トークン・通信量の削減）
- 個別ファイル名の大量送信を避けられる（情報漏えいリスクの低減）
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Any

SCAN_SCHEMA_VERSION = 2

# 版・作業中・確定を示すファイル名/フォルダ名トークン
_VERSION_RE = re.compile(
    r"(?:^|[_\-\s(（\[])(?:v|ver|rev|版|第\d+版)[\.\s]?\d+|\d{8}|\d{4}[-_.]\d{2}[-_.]\d{2}",
    re.IGNORECASE,
)
_WIP_RE = re.compile(r"作業中|下書き|draft|wip|tmp|temp|メモ|コピー|copy|叩き台|途中", re.IGNORECASE)
_FINAL_RE = re.compile(r"最終|確定|正式|final|fix|完成|提出|最新", re.IGNORECASE)
_GENERIC_DIR_RE = re.compile(
    r"^(新しいフォルダ.*|無題.*|その他|一時|temp|tmp|misc|old|新規|test|テスト|あたらしい.*|untitled.*)$",
    re.IGNORECASE,
)
_STRIP_FOR_SERIES_RE = re.compile(
    r"(?:[_\-\s(（\[]*(?:v|ver|rev)[\.\s]?\d+(?:[\.\d]+)?|第\d+版|\d{8}|\d{4}[-_.]\d{2}[-_.]\d{2}"
    r"|最終|確定|正式|final|最新|old|旧|コピー|copy|\(\d+\)|（\d+）)[)）\]]*",
    re.IGNORECASE,
)


def normalize_name(name: str) -> str:
    """全半角・大小文字を吸収した比較用の正規化名。"""
    return unicodedata.normalize("NFKC", name).strip().lower()


def series_key(filename: str) -> str:
    """版番号・日付・確定/コピー等の装飾を除いた「系列キー」。

    同じ系列キーのファイルが同一フォルダに複数あれば、版の乱立と見なせる。
    """
    stem = filename.rsplit(".", 1)[0]
    stem = _STRIP_FOR_SERIES_RE.sub("", normalize_name(stem))
    return re.sub(r"[\s_\-‐–—()（）\[\]]+", "", stem)


def name_signals(filename: str) -> tuple[bool, bool, bool]:
    """(版番号あり, 作業中トークンあり, 確定トークンあり)"""
    return (
        bool(_VERSION_RE.search(filename)),
        bool(_WIP_RE.search(filename)),
        bool(_FINAL_RE.search(filename)),
    )


def is_generic_dir_name(name: str) -> bool:
    return bool(_GENERIC_DIR_RE.match(normalize_name(name)))


@dataclass
class FolderStat:
    """1 フォルダ分の集計。ファイル個々の情報は持たない。"""

    path: str  # ルートからの相対パス（"/" 区切り。ルートは ""）
    depth: int = 0
    n_files: int = 0  # 直下のファイル数
    n_subdirs: int = 0  # 直下のサブフォルダ数
    size: int = 0  # 直下ファイルの合計バイト数
    last_modified: str = ""  # 直下ファイルの最終更新 "YYYY-MM"（低信頼の参考値）
    exts: dict[str, int] = field(default_factory=dict)  # 拡張子 → 件数
    samples: list[str] = field(default_factory=list)  # 代表ファイル名（最大 SAMPLES_PER_DIR）
    n_versioned: int = 0  # 版番号/日付付きファイル数
    n_wip: int = 0  # 作業中トークン付きファイル数
    n_final: int = 0  # 確定トークン付きファイル数
    max_series: int = 0  # 同一系列キーの最大重複数（版の乱立度）
    n_cloud_only: int = 0  # クラウド専用（未ダウンロード）ファイル数

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FolderStat":
        return cls(**d)


@dataclass
class ScanResult:
    schema_version: int = SCAN_SCHEMA_VERSION
    source: str = ""
    backend: str = "local"  # local / onedrive-graph
    scanned_at: str = ""
    folders: list[FolderStat] = field(default_factory=list)
    truncated: bool = False  # max-folders 到達で打ち切ったか

    @property
    def total_files(self) -> int:
        return sum(f.n_files for f in self.folders)

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.folders)

    def save(self, path: str) -> None:
        data = {
            "schema_version": self.schema_version,
            "source": self.source,
            "backend": self.backend,
            "scanned_at": self.scanned_at,
            "truncated": self.truncated,
            "folders": [f.to_dict() for f in self.folders],
        }
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=1)

    @classmethod
    def load(cls, path: str) -> "ScanResult":
        with open(path, encoding="utf-8") as fp:
            data = json.load(fp)
        if data.get("schema_version") != SCAN_SCHEMA_VERSION:
            raise ValueError(
                f"scan.json のスキーマ版が不一致です（期待 {SCAN_SCHEMA_VERSION}, 実際 {data.get('schema_version')}）。"
                "再スキャンしてください。"
            )
        return cls(
            schema_version=data["schema_version"],
            source=data.get("source", ""),
            backend=data.get("backend", "local"),
            scanned_at=data.get("scanned_at", ""),
            truncated=data.get("truncated", False),
            folders=[FolderStat.from_dict(f) for f in data.get("folders", [])],
        )


@dataclass
class Finding:
    """ルールベースで検出した課題所見。"""

    kind: str  # deep_nesting / flat_overload / version_chaos / ...
    path: str
    detail: str
    severity: str = "info"  # info / warn


@dataclass
class Proposal:
    """LLM（またはフォールバック）が生成する改善提案一式。

    添付課題の原因 1〜6 に対応:
      principles: 格納先の原則（原因1・5）
      target_tree: 改善後フォルダ体系（原因1）
      folder_mapping: 既存フォルダ → 新体系の対応（移行計画）
      naming_rules: 命名規則（原因1・6）
      versioning_rules: 版管理・正式版/作業中の分離（原因1・6）
      governance: オーナー・作成統制・棚卸し（原因2・3）
      lifecycle: 保持期間・アーカイブ・廃棄（原因4）
      quick_wins: すぐ着手できる改善
    """

    principles: list[str] = field(default_factory=list)
    target_tree: list[dict[str, str]] = field(default_factory=list)  # {path, purpose, owner_role}
    folder_mapping: list[dict[str, str]] = field(default_factory=list)  # {from, to, action, reason}
    naming_rules: list[str] = field(default_factory=list)
    versioning_rules: list[str] = field(default_factory=list)
    governance: list[str] = field(default_factory=list)
    lifecycle: list[str] = field(default_factory=list)
    quick_wins: list[str] = field(default_factory=list)
    generated_by: str = "rules"  # "llm" / "rules"
    model_note: str = ""
