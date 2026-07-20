"""コマンドライン。

  python -m folder_advisor scan    --source <フォルダパス> --out out
  python -m folder_advisor propose --scan out/scan.json --out out [--no-llm] [--goal "..."] [--llm-provider azure|mistral]
  python -m folder_advisor run     --source ... --out out [--no-llm] [--llm-provider azure|mistral]

OneDrive は同期済みローカルフォルダを --source に指定する（メタデータのみ読むため
クラウド専用ファイルはダウンロードされない）。
--llm-provider は既定 azure。API キー・モデル名などの秘匿情報は環境変数から読み取る
（Azure: AZURE_OPENAI_*、Mistral: MISTRAL_API_KEY / MISTRAL_MODEL）。
"""
from __future__ import annotations

import argparse
import os
import sys

from folder_advisor.models import ScanResult
from folder_advisor.propose import make_proposal
from folder_advisor.report import write_report


def _do_scan(args: argparse.Namespace) -> ScanResult:
    os.makedirs(args.out, exist_ok=True)
    from folder_advisor.scan_local import scan_local
    scan = scan_local(args.source, excludes=args.exclude, max_folders=args.max_folders)
    scan_path = os.path.join(args.out, "scan.json")
    scan.save(scan_path)
    cloud = sum(f.n_cloud_only for f in scan.folders)
    print(f"[scan] フォルダ {len(scan.folders)} / ファイル {scan.total_files} "
          f"/ {scan.total_size / 1e9:.2f} GB → {scan_path}")
    if cloud:
        print(f"[scan] クラウド専用ファイル {cloud} 件はダウンロードせずメタデータのみ取得しました。")
    if scan.truncated:
        print(f"[scan] 注意: --max-folders={args.max_folders} に達したため一部打ち切りました。", file=sys.stderr)
    return scan


def _do_propose(scan: ScanResult, args: argparse.Namespace) -> None:
    proposal, findings = make_proposal(
        scan, use_llm=not args.no_llm, goal=args.goal,
        max_digest_folders=args.max_digest_folders,
        provider=args.llm_provider,
    )
    paths = write_report(args.out, scan, findings, proposal)
    if proposal.generated_by == "llm":
        engine = "Mistral API" if args.llm_provider == "mistral" else "Azure OpenAI"
    else:
        engine = "ルールベース（LLM 未使用）"
    print(f"[propose] 提案エンジン: {engine} / 所見 {len(findings)} 件")
    for k, p in paths.items():
        print(f"[out] {p}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="folder_advisor",
        description="フォルダ体系を取得し、Azure OpenAI または Mistral API（--llm-provider で切替）で"
                    "改善提案するツール。ファイル内容は読まない・送らない（メタデータのみ）。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_scan_opts(p: argparse.ArgumentParser) -> None:
        p.add_argument("--source", required=True,
                       help="対象フォルダ（ローカル、または OneDrive/SharePoint の同期済みローカルパス）")
        p.add_argument("--exclude", action="append", default=[],
                       help="除外パターン（fnmatch。複数指定可）")
        p.add_argument("--max-folders", type=int, default=20000, help="走査フォルダ数上限")

    def add_propose_opts(p: argparse.ArgumentParser) -> None:
        p.add_argument("--no-llm", action="store_true", help="LLM を使わずルールベース提案のみ")
        p.add_argument("--goal", default="", help="LLM への追加要望（自由文）")
        p.add_argument("--max-digest-folders", type=int, default=400,
                       help="LLM に送るダイジェストのフォルダ数上限（通信量・トークン削減）")
        p.add_argument("--llm-provider", choices=["azure", "mistral"], default="azure",
                       help="使用する LLM（既定: azure）。API キー・モデル名は環境変数から読み取ります。")

    p_scan = sub.add_parser("scan", help="フォルダ体系の取得のみ（scan.json 出力）")
    add_scan_opts(p_scan)
    p_scan.add_argument("--out", default="out", help="出力フォルダ")

    p_prop = sub.add_parser("propose", help="scan.json から分析・提案・レポート生成")
    p_prop.add_argument("--scan", required=True, help="scan.json のパス")
    p_prop.add_argument("--out", default="out", help="出力フォルダ")
    add_propose_opts(p_prop)

    p_run = sub.add_parser("run", help="scan → propose を一括実行")
    add_scan_opts(p_run)
    p_run.add_argument("--out", default="out", help="出力フォルダ")
    add_propose_opts(p_run)

    args = parser.parse_args(argv)
    try:
        if args.command == "scan":
            _do_scan(args)
        elif args.command == "propose":
            scan = ScanResult.load(args.scan)
            _do_propose(scan, args)
        elif args.command == "run":
            scan = _do_scan(args)
            _do_propose(scan, args)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    return 0
