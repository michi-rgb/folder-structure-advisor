"""改善提案の生成（LLM 主エンジン + ルールベースフォールバック）。"""
from __future__ import annotations

import sys

from folder_advisor.analyzer import analyze
from folder_advisor.digest import build_digest
from folder_advisor.models import Finding, Proposal, ScanResult
from folder_advisor.prompts import SYSTEM_PROMPT, build_user_prompt


def make_proposal(
    scan: ScanResult,
    use_llm: bool = True,
    goal: str = "",
    max_digest_folders: int = 400,
    provider: str = "azure",
) -> tuple[Proposal, list[Finding]]:
    findings = analyze(scan)
    if use_llm:
        digest = build_digest(scan, max_digest_folders)
        try:
            from folder_advisor.llm import chat_json
            raw = chat_json(SYSTEM_PROMPT, build_user_prompt(scan, digest, findings, goal), provider=provider)
            print("[llm] LLM からの応答を受信しました（提案の生成に成功）。")
            return _from_llm_json(raw), findings
        except Exception as e:  # 設定不足・認証失敗・API エラーはすべてフォールバック
            print(f"[warn] LLM 呼び出しに失敗したためルールベース提案に切り替えます: {e}", file=sys.stderr)
    return _fallback_proposal(scan, findings), findings


def _str_list(raw: dict, key: str) -> list[str]:
    v = raw.get(key, [])
    return [str(x) for x in v if x] if isinstance(v, list) else []


def _dict_list(raw: dict, key: str, fields: tuple[str, ...]) -> list[dict[str, str]]:
    out = []
    v = raw.get(key, [])
    if not isinstance(v, list):
        return out
    for item in v:
        if isinstance(item, dict):
            out.append({f: str(item.get(f, "")) for f in fields})
    return out


def _from_llm_json(raw: dict) -> Proposal:
    return Proposal(
        principles=_str_list(raw, "principles"),
        target_tree=_dict_list(raw, "target_tree", ("path", "purpose", "owner_role")),
        folder_mapping=_dict_list(raw, "folder_mapping", ("from", "to", "action", "reason")),
        naming_rules=_str_list(raw, "naming_rules"),
        versioning_rules=_str_list(raw, "versioning_rules"),
        governance=_str_list(raw, "governance"),
        lifecycle=_str_list(raw, "lifecycle"),
        quick_wins=_str_list(raw, "quick_wins"),
        generated_by="llm",
    )


def _fallback_proposal(scan: ScanResult, findings: list[Finding]) -> Proposal:
    """LLM なしでも成立する汎用テンプレート提案 + 所見ベースの quick wins。"""
    top_dirs = sorted({f.path.split("/")[0] for f in scan.folders if f.path})
    mapping = [
        {"from": d, "to": f"10_プロジェクト/{d}", "action": "要検討",
         "reason": "既存トップフォルダ。新体系のどこに属するか棚卸しで決定する。"}
        for d in top_dirs[:30]
    ]
    quick = []
    for f in findings:
        if f.kind == "version_chaos":
            quick.append(f"「{f.path}」の旧版を 90_アーカイブ へ隔離し、正 1 本のみ残す。")
        elif f.kind == "generic_name":
            quick.append(f"汎用名フォルダ「{f.path}」を目的が分かる名前に変更または解体する。")
        elif f.kind == "duplicate_folder_name":
            quick.append(f"散在フォルダの統合を検討: {f.detail}")
    return Proposal(
        principles=[
            "正式版は共有の一元管理フォルダにのみ置き、個人フォルダ・メール添付を正としない。",
            "第一階層は番号プレフィックス付きの 5〜9 フォルダに固定し、勝手に増やさない。",
            "迷ったら 10_プロジェクト（案件単位）へ。資料種別でトップ階層を作らない。",
        ],
        target_tree=[
            {"path": "10_プロジェクト", "purpose": "案件・プロジェクト単位の正式資料", "owner_role": "各案件責任者"},
            {"path": "10_プロジェクト/<案件名>/10_正式版", "purpose": "確定・提出済みの正のみ", "owner_role": "案件責任者"},
            {"path": "10_プロジェクト/<案件名>/20_作業中", "purpose": "ドラフト・検討中", "owner_role": "担当者"},
            {"path": "20_定常業務", "purpose": "繰り返し業務の手順・様式", "owner_role": "業務オーナー"},
            {"path": "30_組織運営", "purpose": "会議体・計画・報告", "owner_role": "部門管理者"},
            {"path": "80_テンプレート", "purpose": "雛形の正式版", "owner_role": "業務オーナー"},
            {"path": "90_アーカイブ", "purpose": "旧版・終了案件（読み取り専用運用）", "owner_role": "部門管理者"},
        ],
        folder_mapping=mapping,
        naming_rules=[
            "ファイル名は「YYYYMMDD_案件名_資料名_vNN.ext」（例: 20260713_A社提案_見積書_v02.xlsx）。",
            "「最新」「final」「コピー」をファイル名に使わない。版は必ず v 番号で表す。",
            "フォルダ名に日付を入れない（並び順は番号プレフィックスで管理）。",
        ],
        versioning_rules=[
            "正式版は 10_正式版 フォルダに 1 本のみ。旧版は確定時に 90_アーカイブ へ移す。",
            "更新日時・作成者メタデータは信頼しない。版判定はファイル名の v 番号と改訂履歴シートを正とする。",
            "作業中ファイルを正式版フォルダに置かない（レビュー完了後に版番号を確定して移動）。",
        ],
        governance=[
            "各トップフォルダと重要資料にオーナー（管理責任者）を 1 名割り当て、一覧表を 30_組織運営 に置く。",
            "新しいトップフォルダ・チーム・サイトの作成は申請制とし、目的とオーナーの登録を必須にする。",
            "四半期ごとに棚卸しを実施し、オーナー不在・未使用フォルダを整理する。",
        ],
        lifecycle=[
            "終了案件は完了後 3 か月で 90_アーカイブ へ移動する。",
            "アーカイブの保持期間（例: 5 年）を定め、超過分は廃棄判定にかける。",
            "2 年以上更新のないフォルダは棚卸し時にアーカイブ候補として自動リストアップする。",
        ],
        quick_wins=quick[:10] or ["課題所見は検出されませんでした。命名規則の周知から始めてください。"],
        generated_by="rules",
        model_note="LLM 未使用（設定不足または失敗）。汎用テンプレートに基づく提案です。",
    )
