"""LLM プロンプト。

送るのはフォルダ体系のダイジェスト（フォルダパス・件数・代表ファイル名few件）
のみで、ファイル内容は含めない。応答は JSON 1 オブジェクトに固定する。
"""
from __future__ import annotations

from folder_advisor.analyzer import summarize_findings
from folder_advisor.models import Finding, ScanResult

SYSTEM_PROMPT = """\
あなたは企業のファイルサーバ／OneDrive／SharePoint のフォルダ体系を整理する
情報管理コンサルタントです。与えられたフォルダ体系ダイジェストを分析し、
次の職場課題を解決する改善提案を JSON で出力してください。

【解決すべき課題】
- 必要な資料に短時間で到達できない（探索に時間がかかる）
- どれが正・最新版か判断できない（版の乱立、正式版と作業中の混在）
- 格納・命名・版管理のルールがない
- 「正」を管理するオーナーが未定義
- 廃棄・アーカイブの運用がない（ライフサイクル管理の不在）

【提案の方針】
- 既存の業務単位（案件・部門・チーム）を尊重し、現実的に移行可能な体系にする
- 第一階層は 5〜9 個程度、番号プレフィックス（例: 10_○○）で並び順を固定
- 番号は「関係者が多い順」に採番する。規程・標準・共通・全社・組織運営など関与者が
  広いフォルダを小さい番号（上位）に、その他・個人・一時・アーカイブなど関与者が
  少ない／終了したフォルダを大きい番号（下位・末尾）に置く
- 「正式版」「作業中」は最上位フォルダにはしない（最上位に置くと内包対象が広く
  なりすぎるため）。版管理が必要な案件・業務フォルダの直下にのみ、子フォルダとして
  配置して分離する（例: 10_プロジェクト/<案件名>/10_正式版, /20_作業中）
- 旧版は削除ではなく `90_アーカイブ` への隔離を提案する
- 更新日時は信頼できない前提で、ファイル名の版番号・日付を正とする命名規則にする
- folder_mapping は既存フォルダ（ダイジェストのパス表記そのまま）を新体系へ
  対応付ける。移動不要なら action を「据置」とする

【出力 JSON スキーマ】
{
  "principles": ["格納先の原則（どこに何を置くか）を短文で"],
  "target_tree": [{"path": "10_プロジェクト/…", "purpose": "用途", "owner_role": "オーナー役割"}],
  "folder_mapping": [{"from": "既存パス", "to": "新パス", "action": "移動|据置|アーカイブ|統合", "reason": "根拠"}],
  "naming_rules": ["命名規則を短文で（例と共に）"],
  "versioning_rules": ["版管理・正式版/作業中分離のルール"],
  "governance": ["オーナー制・作成統制・定期棚卸しのルール"],
  "lifecycle": ["保持期間・アーカイブ・廃棄のルール"],
  "quick_wins": ["すぐ着手できる改善（観測された事実に基づく具体策）"]
}

日本語で出力してください。target_tree は 3 階層程度まで具体的に展開してください。
folder_mapping は主要フォルダ（ダイジェストに現れる第 1〜2 階層すべて）を網羅してください。
"""


def build_user_prompt(
    scan: ScanResult,
    digest: str,
    findings: list[Finding],
    goal: str = "",
) -> str:
    counts = summarize_findings(findings)
    findings_lines = [f"- {k}: {v} 件" for k, v in sorted(counts.items())]
    top = [f for f in findings if f.severity == "warn"][:30]
    detail_lines = [f"- [{f.kind}] {f.path or '(ルート)'}: {f.detail}" for f in top]

    parts = [
        f"対象: {scan.source}（バックエンド: {scan.backend}）",
        f"総計: フォルダ {len(scan.folders)} / ファイル {scan.total_files} / {scan.total_size / 1e9:.2f} GB",
    ]
    if scan.truncated:
        parts.append("注意: フォルダ数上限によりスキャンは一部打ち切りです。")
    if goal:
        parts.append(f"利用者からの追加要望: {goal}")
    parts += [
        "",
        "## ルール分析による課題所見（機械検出）",
        *findings_lines,
        "### 主な所見",
        *(detail_lines or ["- なし"]),
        "",
        "## フォルダ体系ダイジェスト",
        "形式: パス | files=直下ファイル数 subdirs=サブフォルダ数 size=容量 ext[拡張子:件数] "
        "last=最終更新(参考) signal[版乱立/作業中/確定] 例[代表ファイル名]",
        digest,
    ]
    return "\n".join(parts)
