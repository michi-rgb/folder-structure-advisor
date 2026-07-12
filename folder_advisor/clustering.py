"""プロジェクト（案件）単位のクラスタリングと集約先フォルダの決定。

方針転換の中核。従来の proposer は「資料種別」で第一階層を作り直していたが、
それでは (1) 異なるプロジェクトが種別で混ざり、(2) 既存フォルダ構造を全面的に
壊してユーザーが慣れ直すコストが大きい、という問題があった。

ここでは代わりに:
  - LLM を主エンジンとして各ファイルを「プロジェクト/案件」に束ねる
    （ファイル名の類似性 + 既存フォルダ文脈で判断。ファイル種別では束ねない）。
  - 各プロジェクトの「ホームフォルダ」= そのプロジェクトのファイルが既に最も多く
    存在する**既存フォルダ**、を集約先に選ぶ（新フォルダを極力作らない）。

さらに「変更は最小限」を徹底するため、集約は次のように**絞り込む**:
  - **一時/個人置き場**（メール添付・個人フォルダ・デスクトップ・ダウンロード・
    一時 等）は**ホーム候補から除外**する。片付けたい置き場を集約先にしない。
  - 集約で動かすのは**一時/個人置き場にあるファイルだけ**。共有フォルダに既に
    整理されている正規ファイルや、ホームのサブフォルダ配下のファイルは動かさない
    （＝意味のあるサブ構造を平坦化しない）。
  - 空ファイル・自動生成の汎用名（無題 / スクリーンショット 等）は意味を持たない
    ため集約対象から外す（LLM の取りこぼしによる誤移動を防ぐ）。

このモジュールはルール／LLM を問わず「割当 dict」を入力に取り、集約先や集約可否を
計算する純粋ロジックに徹する（テスト容易性のため副作用を持たない）。
"""

from __future__ import annotations

from collections import defaultdict

from .filters import is_generic_basename, is_structural
from .models import FileEntry
from .versioning import normalize_name

# 一時・個人・自動生成の「置き場」を示す語（パスのいずれかのセグメントに含まれると該当）。
# これらは集約先（ホーム）にせず、かつ「ここに置かれたファイルだけ」を集約対象にする。
STAGING_KEYWORDS: tuple[str, ...] = (
    "メール添付", "添付", "受信",
    "個人フォルダ", "個人", "マイドキュメント",
    "デスクトップ", "desktop",
    "ダウンロード", "download", "downloads",
    "一時", "temp", "tmp", "temporary",
    "新しいフォルダ", "new folder", "無題フォルダ",
)


def is_staging_path(parent: str) -> bool:
    """親フォルダの相対パスが一時/個人置き場に該当するか。

    パスのいずれかのセグメントに STAGING_KEYWORDS を含めば該当。
    例: "デスクトップ整理/新しいフォルダ" → デスクトップ・新しいフォルダ の両方で該当。
    """
    if not parent:
        return False
    low_segments = [seg.lower() for seg in parent.split("/") if seg]
    for seg in low_segments:
        for kw in STAGING_KEYWORDS:
            if kw.lower() in seg:
                return True
    return False


def _is_meaningless(entry: FileEntry) -> bool:
    """空ファイル・汎用/自動生成名（版でも案件資料でもない）かを返す。"""
    if entry.size <= 0:
        return True
    return is_generic_basename(normalize_name(entry.stem))


def assign_projects(
    files: list[FileEntry], llm_map: dict[str, str] | None
) -> dict[str, str]:
    """各ファイルにプロジェクトラベルを割り当て {path: project} を返す。

    - LLM の割当（path -> ラベル）を一次情報とする。
    - 構成ファイル（.gitignore 等）・空/汎用名ファイルは対象外（据え置くため除外）。
    - ラベルが取れなかったファイルは戻り値に含めない（= クラスタ無し = 現在地維持）。
    """
    if not llm_map:
        return {}
    eligible = {
        f.path
        for f in files
        if not is_structural(f.name) and not _is_meaningless(f)
    }
    result: dict[str, str] = {}
    for path, label in llm_map.items():
        if not isinstance(label, str):
            continue
        label = label.strip()
        if label and path in eligible:
            result[path] = label
    return result


def _depth(parent: str) -> int:
    """親フォルダの相対パス深さ。ルート直下（""）は 0。"""
    return 0 if parent == "" else parent.count("/") + 1


def resolve_home_folders(
    files: list[FileEntry], project_of: dict[str, str]
) -> dict[str, str]:
    """各プロジェクトの「ホームフォルダ」（集約先の既存フォルダ）を決める。

    集約先は**一時/個人置き場を除いた既存フォルダ**の中から、そのプロジェクトの
    メンバーが最も多く存在するものを選ぶ。同数の場合は「浅い階層」→「パス文字列が
    小さい方」で決定的に選ぶ（＝なるべく上位・安定した共有フォルダにする）。

    プロジェクトのメンバーが一時/個人置き場にしか無い場合は、妥当な既存の集約先が
    無いためホームを持たせない（＝そのプロジェクトは集約しない）。

    戻り値は {project: parent_path}。parent_path はルートからの相対パスで、
    ルート直下は "" になる。
    """
    by_path = {f.path: f for f in files}
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for path, project in project_of.items():
        f = by_path.get(path)
        if f is None:
            continue
        # 一時/個人置き場はホーム候補にしない。
        if is_staging_path(f.parent):
            continue
        counts[project][f.parent] += 1

    home: dict[str, str] = {}
    for project, parent_counts in counts.items():
        if not parent_counts:
            continue
        # (件数の多さ, 浅さ, パス小ささ) で最良の親を選ぶ。
        best = min(
            parent_counts.items(),
            key=lambda kv: (-kv[1], _depth(kv[0]), kv[0]),
        )
        home[project] = best[0]
    return home


def consolidation_target(
    entry: FileEntry, project_of: dict[str, str], home_of: dict[str, str]
) -> str | None:
    """このファイルを集約すべき場合の集約先フォルダを返す。しない場合は None。

    集約するのは「一時/個人置き場にあり、ホームがそこと異なる」場合のみ。
    共有フォルダに既にあるファイルや、ホームのサブフォルダ配下のファイルは動かさない
    （最小変更・サブ構造の非平坦化）。空/汎用ファイルは assign_projects 段階で除外済み。
    """
    project = project_of.get(entry.path)
    if not project:
        return None
    home = home_of.get(project)
    if home is None:
        return None
    if entry.parent == home:
        return None  # 既にホーム
    if home and (entry.parent == home or entry.parent.startswith(home + "/")):
        return None  # ホームのサブフォルダ配下 → 平坦化しない
    if not is_staging_path(entry.parent):
        return None  # 共有フォルダの正規ファイル → 動かさない
    return home
