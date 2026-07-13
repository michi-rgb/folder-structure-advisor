"""ルールベースの課題所見。

LLM が無くても動く一次診断であり、LLM プロンプトにも「観測された事実」として
渡すことで、提案の根拠を実データに固定する役割を持つ。

更新日時は OneDrive/SharePoint では開閉や同期で書き換わることがあるため
「参考値（低信頼）」として扱い、削除ではなくアーカイブ候補の提示に留める。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from folder_advisor.models import Finding, ScanResult, is_generic_dir_name, normalize_name

DEEP_NESTING_DEPTH = 6
FLAT_OVERLOAD_FILES = 100
STALE_YEARS = 2


def analyze(scan: ScanResult, now: datetime | None = None) -> list[Finding]:
    now = now or datetime.now()
    stale_before = f"{now.year - STALE_YEARS:04d}-{now.month:02d}"
    findings: list[Finding] = []
    by_norm_name: dict[str, list[str]] = defaultdict(list)

    for f in scan.folders:
        name = f.path.rsplit("/", 1)[-1]
        if f.path:
            by_norm_name[normalize_name(name)].append(f.path)

        if f.depth >= DEEP_NESTING_DEPTH:
            findings.append(Finding(
                "deep_nesting", f.path, f"階層が深すぎます（{f.depth} 階層）。目安は 4〜5 階層まで。", "warn"))
        if f.n_files >= FLAT_OVERLOAD_FILES:
            findings.append(Finding(
                "flat_overload", f.path, f"1 フォルダに {f.n_files} ファイルが平置きされています。", "warn"))
        if f.max_series >= 3:
            findings.append(Finding(
                "version_chaos", f.path,
                f"同名系列のファイルが最大 {f.max_series} 本並存（v1/最新/コピー等の乱立）。"
                "版管理ルールと旧版アーカイブが必要です。", "warn"))
        if f.n_wip and f.n_final:
            findings.append(Finding(
                "mixed_final_wip", f.path,
                f"確定版（{f.n_final} 件）と作業中（{f.n_wip} 件）が同居しており、正が判別できません。", "warn"))
        if f.path and is_generic_dir_name(name):
            findings.append(Finding(
                "generic_name", f.path, "「新しいフォルダ」「temp」等の汎用名フォルダです。目的が分かる名前が必要です。"))
        if f.path and f.n_files == 0 and f.n_subdirs == 0:
            findings.append(Finding("empty", f.path, "空フォルダです。"))
        if f.n_files and f.last_modified and f.last_modified < stale_before:
            findings.append(Finding(
                "stale", f.path,
                f"最終更新 {f.last_modified}（参考値）。{STALE_YEARS} 年以上更新がなくアーカイブ候補です。"))

    for norm, paths in by_norm_name.items():
        if len(paths) >= 2:
            findings.append(Finding(
                "duplicate_folder_name", paths[0],
                f"同名フォルダが {len(paths)} 箇所に散在: " + " / ".join(sorted(paths)[:5]), "warn"))

    order = {"warn": 0, "info": 1}
    findings.sort(key=lambda x: (order.get(x.severity, 9), x.kind, x.path))
    return findings


def summarize_findings(findings: list[Finding]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for f in findings:
        counts[f.kind] += 1
    return dict(counts)
