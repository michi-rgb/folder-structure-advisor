"""メタデータの信頼度付与と内容ハッシュ計算。

課題 B（信頼性の判断不能）を踏まえ、更新日・作成者などのメタデータは値を
そのまま信頼せず「信頼度」を付ける。重複判定の一次情報としては、当てにできる
**内容ハッシュ（SHA-256）** を計算する。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from .models import (
    FileEntry,
    RELIABILITY_HIGH,
    RELIABILITY_LOW,
    ScanResult,
)

# ハッシュ計算をスキップする上限サイズ（既定 512MB）。巨大ファイルは名前で扱う。
DEFAULT_HASH_MAX_BYTES = 512 * 1024 * 1024
_CHUNK = 1024 * 1024


def compute_hash(abs_path: Path, max_bytes: int = DEFAULT_HASH_MAX_BYTES) -> Optional[str]:
    """ファイルの SHA-256 を返す。読めない/大きすぎる場合は None。"""
    try:
        if abs_path.stat().st_size > max_bytes:
            return None
    except OSError:
        return None
    h = hashlib.sha256()
    try:
        with abs_path.open("rb") as fp:
            for chunk in iter(lambda: fp.read(_CHUNK), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def assess_reliability(entry: FileEntry) -> dict[str, str]:
    """各メタデータの信頼度を判定する。

    - modified（更新日）：開閉だけで書き換わり、最終改訂を示さない → 低信頼
    - author（作成者）：雛形作成者が残るため実責任者を示さない → 低信頼
      （走査では author を取得しないため、常に低信頼として明示）
    - hash（内容ハッシュ）：内容そのものに基づく → 高信頼
    """
    return {
        "modified": RELIABILITY_LOW,
        "created": RELIABILITY_LOW,
        "author": RELIABILITY_LOW,
        "hash": RELIABILITY_HIGH if entry.content_hash else RELIABILITY_LOW,
    }


def enrich(
    scan: ScanResult,
    compute_hashes: bool = True,
    hash_max_bytes: int = DEFAULT_HASH_MAX_BYTES,
) -> ScanResult:
    """走査結果に内容ハッシュと信頼度を付与する（in-place で更新して返す）。"""
    root = Path(scan.root)
    for entry in scan.files:
        if compute_hashes:
            entry.content_hash = compute_hash(root / entry.path, hash_max_bytes)
        entry.reliability = assess_reliability(entry)
    return scan
