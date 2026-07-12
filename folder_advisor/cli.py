"""コマンドラインインターフェース。

サブコマンド:
  scan    走査＋ハッシュ＋信頼度 → scan.json
  report  scan.json（または --source から走査）→ 分析・提案・HTML/CSV/グラフ
  run     scan → analyze → report を一括実行

出力はすべて UTF-8。stdout への進捗表示は最小限にする（Windows コンソールの
文字コード差異を避けるため）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .classifier import classify_all
from .duplicates import detect_exact_duplicates
from .enrich import enrich
from .filters import is_structural
from .llm import LLMHelper
from .models import AnalysisResult, ScanResult
from .proposer import build_proposal
from .report import write_report
from .scanner import scan_source
from .versioning import detect_version_series


def _do_scan(source: str, mode: str, max_files, compute_hashes: bool) -> ScanResult:
    scan = scan_source(source, mode=mode, max_files=max_files)
    enrich(scan, compute_hashes=compute_hashes)
    return scan


def _save_json(obj: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _analyze(scan: ScanResult, use_llm: bool, llm_provider: str = "auto") -> AnalysisResult:
    # 資料種別は補助メタ情報（レポートの内訳列）として保持。整頓の主軸ではない。
    category_counts = classify_all(scan.files)

    project_of = None
    llm_used = False
    if use_llm:
        helper = LLMHelper(provider_name=llm_provider)
        if helper.available:
            print(f"  [情報] LLM補助プロバイダ: {helper.provider_name}", file=sys.stderr)
            # 主エンジン：ファイルをプロジェクト/案件単位に束ねる（種別では束ねない）。
            entries = [
                {"path": f.path, "name": f.name}
                for f in scan.files
                if not is_structural(f.name)
            ]
            project_of = helper.cluster_projects(entries)
            if project_of:
                llm_used = True
                print(f"  [情報] プロジェクト割当: {len(project_of)} 件", file=sys.stderr)
        else:
            print("  [情報] LLM接続情報（Azure OpenAI / Mistral）が無いため"
                  "、プロジェクト集約は行わず据置中心（重複統合・旧版隔離のみ）で"
                  "実行します。", file=sys.stderr)

    dups = detect_exact_duplicates(scan.files)
    series = detect_version_series(scan.files)
    analysis = build_proposal(scan, dups, series, category_counts, project_of)
    analysis.llm_used = llm_used
    return analysis


def cmd_scan(args) -> int:
    scan = _do_scan(args.source, args.mode, args.max_files, not args.no_hash)
    out_dir = Path(args.out)
    _save_json(scan.to_dict(), out_dir / "scan.json")
    print(f"走査完了: files={len(scan.files)} dirs={len(scan.dirs)} "
          f"skipped={len(scan.skipped)} -> {out_dir / 'scan.json'}")
    return 0


def cmd_report(args) -> int:
    if args.scan:
        data = json.loads(Path(args.scan).read_text(encoding="utf-8"))
        scan = ScanResult.from_dict(data)
    elif args.source:
        scan = _do_scan(args.source, args.mode, args.max_files, not args.no_hash)
    else:
        print("エラー: --scan か --source のいずれかを指定してください。", file=sys.stderr)
        return 2

    analysis = _analyze(scan, use_llm=args.llm, llm_provider=args.llm_provider)
    out_dir = Path(args.out)
    _save_json(analysis.to_dict(), out_dir / "analysis.json")
    paths = write_report(scan, analysis, out_dir)
    _print_summary(analysis, paths)
    return 0


def cmd_run(args) -> int:
    scan = _do_scan(args.source, args.mode, args.max_files, not args.no_hash)
    out_dir = Path(args.out)
    _save_json(scan.to_dict(), out_dir / "scan.json")
    analysis = _analyze(scan, use_llm=args.llm, llm_provider=args.llm_provider)
    _save_json(analysis.to_dict(), out_dir / "analysis.json")
    paths = write_report(scan, analysis, out_dir)
    _print_summary(analysis, paths)
    return 0


def _print_summary(analysis: AnalysisResult, paths: dict) -> None:
    s = analysis.summary
    print("=== 分析サマリ ===")
    print(f"  総ファイル数     : {s.get('total_files')}")
    print(f"  完全重複グループ : {s.get('duplicate_groups')} "
          f"(冗長コピー {s.get('redundant_copies')} 件)")
    print(f"  旧版の可能性     : {s.get('old_versions')} 件")
    print(f"  プロジェクト     : {s.get('projects', 0)} 件（集約の束ね単位）")
    print(f"  アクション内訳   : {s.get('actions')}")
    print(f"  LLM補助          : {'有効' if analysis.llm_used else '無効(ルールベース)'}")
    print("=== 出力 ===")
    print(f"  レポート : {paths['report']}")
    print(f"  移動計画 : {paths['csv']}")
    print(f"  グラフ   : {paths['graph']}")


def _add_common(p) -> None:
    p.add_argument("--mode", choices=["local", "sharepoint"], default="local",
                   help="対象種別（既定: local）")
    p.add_argument("--out", default="out", help="出力ディレクトリ（既定: out）")
    p.add_argument("--max-files", type=int, default=None, dest="max_files",
                   help="走査するファイル数の上限（動作確認用）")
    p.add_argument("--no-hash", action="store_true",
                   help="内容ハッシュ計算を省略（高速だが完全重複検出は無効）")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="folder_advisor",
        description="フォルダ構成の改善を提案する（提案・可視化のみ／実ファイルは変更しない）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="走査のみ（scan.json 出力）")
    p_scan.add_argument("--source", required=True,
                        help="対象フォルダ（ローカル / SharePoint 同期パス）")
    p_scan.add_argument("--mode", choices=["local", "sharepoint"], default="local")
    p_scan.add_argument("--out", default="out")
    p_scan.add_argument("--max-files", type=int, default=None, dest="max_files")
    p_scan.add_argument("--no-hash", action="store_true")
    p_scan.set_defaults(func=cmd_scan)

    p_report = sub.add_parser("report", help="分析・提案・レポート生成")
    p_report.add_argument("--scan", help="既存の scan.json を入力に使う")
    p_report.add_argument("--source", help="対象フォルダ（--scan 未指定時に走査）")
    _add_common(p_report)
    p_report.add_argument("--llm", action="store_true",
                          help="LLM補助を使う（接続情報が必要）")
    p_report.add_argument("--llm-provider", choices=["auto", "azure", "mistral"],
                          default="auto", dest="llm_provider",
                          help="LLM補助のプロバイダ（既定 auto）")
    p_report.set_defaults(func=cmd_report)

    p_run = sub.add_parser("run", help="走査→分析→レポートを一括実行")
    _add_common(p_run)
    p_run.add_argument("--source", required=True,
                       help="対象フォルダ（ローカル / SharePoint 同期パス）")
    p_run.add_argument("--llm", action="store_true",
                       help="LLM補助を使う（接続情報が必要）")
    p_run.add_argument("--llm-provider", choices=["auto", "azure", "mistral"],
                       default="auto", dest="llm_provider",
                       help="LLM補助のプロバイダ（既定 auto）")
    p_run.set_defaults(func=cmd_run)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
