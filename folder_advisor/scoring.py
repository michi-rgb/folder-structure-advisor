"""フォルダ構成の「整理健全度スコア」（100点満点）を改善前後で算出する。

移動計画（提案）を適用する前（before＝現状）と後（after＝提案どおり整頓した状態）
それぞれについて、4つの観点を 0〜100 で採点し、重み付き平均を総合点にする。
点数の根拠が分かるよう、観点ごとの内訳も返す（HTML レポートで表示）。

観点と重み:
  1. 重複の排除     (30%) … 冗長コピーが残っていないか
  2. 旧版の隔離     (25%) … 旧版が現役資料に紛れていないか（アーカイブ隔離）
  3. 散在の解消     (25%) … 一時/個人置き場にファイルが放置されていないか
  4. 案件のまとまり (20%) … 同じ案件（無ければ同名資料）が複数フォルダに分散していないか

いずれも「減点方式」。母数は構成ファイル（.gitignore 等）を除いた実ファイル数。
"""

from __future__ import annotations

from collections import defaultdict

from .clustering import is_staging_path
from .filters import is_generic_basename, is_structural
from .models import AnalysisResult, ScanResult
from .proposer import ARCHIVE_DIR
from .versioning import normalize_name

WEIGHTS = {
    "dedup": 0.30,
    "version": 0.25,
    "scatter": 0.25,
    "cohesion": 0.20,
}
LABELS = {
    "dedup": "重複の排除",
    "version": "旧版の隔離",
    "scatter": "散在の解消",
    "cohesion": "案件のまとまり",
}


def _parent(path: str) -> str:
    return "/".join(path.split("/")[:-1])


def _strip_archive(parent: str) -> str:
    """アーカイブ隔離先はその案件ホームと同じ場所とみなし、末尾のアーカイブ層を除く。"""
    segs = [s for s in parent.split("/") if s]
    if segs and segs[-1] == ARCHIVE_DIR:
        segs = segs[:-1]
    return "/".join(segs)


def _fragmentation(groups: dict[str, list[str]]) -> tuple[int, int]:
    """グループ（案件/同名資料）ごとのフォルダ分散度を数える。

    戻り値 (fragmentation, possible)。fragmentation は「余計に分かれているフォルダ数」、
    possible は「全ファイルがバラバラだった場合の最大分散数」。
    """
    frag = 0
    possible = 0
    for members_parents in groups.values():
        n = len(members_parents)
        if n < 2:
            continue
        distinct = len(set(members_parents))
        frag += distinct - 1
        possible += n - 1
    return frag, possible


def score_structure(scan: ScanResult, analysis: AnalysisResult) -> dict:
    """改善前後の整理健全度スコアと内訳を返す。"""
    plan = [m for m in analysis.move_plan if not is_structural(m.current_path.split("/")[-1])]
    total = len(plan)

    # 案件ラベルがあれば案件単位、無ければ同名資料（正規化名）単位で結束を測る。
    has_projects = any(m.project for m in plan)

    def group_key(m) -> str | None:
        if has_projects:
            return m.project or None
        stem = m.current_path.split("/")[-1].rsplit(".", 1)[0]
        base = normalize_name(stem)
        if not base or is_generic_basename(base):
            return None
        return base

    # --- before（現状）の減点要素 ---
    before_dup = sum(1 for m in plan if m.dup_flag)
    before_old = sum(1 for m in plan if m.old_version_flag)
    before_staging = sum(1 for m in plan if is_staging_path(_parent(m.current_path)))

    before_groups: dict[str, list[str]] = defaultdict(list)
    for m in plan:
        k = group_key(m)
        if k:
            before_groups[k].append(_parent(m.current_path))

    # --- after（提案適用後）の減点要素 ---
    # 冗長コピー（統合）と旧版隔離は解消される。統合されたファイルは消える。
    kept = [m for m in plan if m.action != "統合"]
    after_dup = 0  # 統合により冗長コピーは残らない
    after_old = 0  # 要確認＝アーカイブ隔離により現役から分離
    after_staging = sum(1 for m in kept if is_staging_path(_strip_archive(_parent(m.proposed_path))))

    after_groups: dict[str, list[str]] = defaultdict(list)
    for m in kept:
        k = group_key(m)
        if k:
            after_groups[k].append(_strip_archive(_parent(m.proposed_path)))

    def pct_good(bad: int) -> float:
        if total == 0:
            return 100.0
        return 100.0 * (1 - bad / total)

    # 分散は「改善前の最大分散数」を共通の分母にして、統合でファイルが減っても
    # スコアが不当に下がらない（改善が単調に反映される）ようにする。
    b_frag, b_pos = _fragmentation(before_groups)
    a_frag, _a_pos = _fragmentation(after_groups)
    denom = b_pos
    before_cohesion = 100.0 * (1 - b_frag / denom) if denom else 100.0
    after_cohesion = 100.0 * (1 - min(a_frag, denom) / denom) if denom else 100.0

    dims = []
    dim_defs = [
        ("dedup", pct_good(before_dup), pct_good(after_dup),
         f"冗長コピー {before_dup}→0 件", f"冗長コピー 0 件"),
        ("version", pct_good(before_old), pct_good(after_old),
         f"露出した旧版 {before_old} 件", f"旧版 {before_old} 件をアーカイブ隔離"),
        ("scatter", pct_good(before_staging), pct_good(after_staging),
         f"一時/個人置き場 {before_staging} 件", f"一時/個人置き場 残 {after_staging} 件"),
        ("cohesion", before_cohesion, after_cohesion,
         f"分散 {b_frag}/{b_pos} 箇所", f"分散 {a_frag}/{b_pos} 箇所"),
    ]
    before_total = 0.0
    after_total = 0.0
    for key, bscore, ascore, bdetail, adetail in dim_defs:
        w = WEIGHTS[key]
        before_total += w * bscore
        after_total += w * ascore
        dims.append({
            "key": key,
            "label": LABELS[key],
            "weight": int(w * 100),
            "before": round(bscore),
            "after": round(ascore),
            "before_detail": bdetail,
            "after_detail": adetail,
        })

    b = round(before_total)
    a = round(after_total)
    return {
        "total_files": total,
        "has_projects": has_projects,
        "dimensions": dims,
        "before_total": b,
        "after_total": a,
        "delta": a - b,
        "grade_before": _grade(b),
        "grade_after": _grade(a),
    }


def _grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    if score >= 40:
        return "D"
    return "E"
