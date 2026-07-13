"""レポート出力。

- report.html      自己完結 HTML（外部 CDN 不要・オフライン閲覧可）
- 運用ルール案.md   命名・版管理・ガバナンス・ライフサイクルのルール文書
- move_plan.csv    フォルダ移行計画（Excel 用 BOM 付き UTF-8）
"""
from __future__ import annotations

import csv
import html
import os
from datetime import datetime

from folder_advisor.models import Finding, Proposal, ScanResult

_KIND_LABELS = {
    "deep_nesting": "深すぎる階層",
    "flat_overload": "ファイル平置き過多",
    "version_chaos": "版の乱立",
    "mixed_final_wip": "正式版と作業中の混在",
    "generic_name": "汎用名フォルダ",
    "empty": "空フォルダ",
    "stale": "長期未更新（アーカイブ候補）",
    "duplicate_folder_name": "同名フォルダの散在",
}


def _esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def _fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.0f}{unit}" if unit == "B" else f"{n:,.1f}{unit}"
        n /= 1024
    return f"{n}B"


def _tree_html(paths: list[tuple[str, str]], open_depth: int = 1) -> str:
    """(パス, 注記) のリストから <details> の入れ子ツリーを作る。"""
    tree: dict = {}
    notes: dict[str, str] = {}
    for path, note in paths:
        if not path:
            continue
        node = tree
        for part in path.split("/"):
            node = node.setdefault(part, {})
        notes[path] = note

    def render(node: dict, prefix: str, depth: int) -> str:
        out = []
        for name in sorted(node):
            full = f"{prefix}/{name}" if prefix else name
            note = notes.get(full, "")
            label = f"<span class='dir'>📁 {_esc(name)}</span>"
            if note:
                label += f" <span class='note'>{_esc(note)}</span>"
            if node[name]:
                op = " open" if depth < open_depth else ""
                out.append(f"<details{op}><summary>{label}</summary>{render(node[name], full, depth + 1)}</details>")
            else:
                out.append(f"<div class='leaf'>{label}</div>")
        return "".join(out)

    return f"<div class='tree'>{render(tree, '', 0)}</div>"


def _ul(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{_esc(i)}</li>" for i in items) + "</ul>" if items else "<p class='muted'>（なし）</p>"


_CSS = """
body{font-family:'Segoe UI','Hiragino Sans','Yu Gothic UI',sans-serif;margin:0;background:#f5f6f8;color:#1a1a2e}
.wrap{max-width:1100px;margin:0 auto;padding:24px}
h1{font-size:22px} h2{font-size:17px;border-left:4px solid #3b6fd4;padding-left:8px;margin-top:32px}
.cards{display:flex;gap:12px;flex-wrap:wrap}
.card{background:#fff;border-radius:8px;padding:12px 18px;box-shadow:0 1px 3px rgba(0,0,0,.08);min-width:120px}
.card .v{font-size:20px;font-weight:700} .card .k{font-size:12px;color:#667}
table{border-collapse:collapse;width:100%;background:#fff;font-size:13px}
th,td{border:1px solid #dde;padding:5px 8px;text-align:left;vertical-align:top;word-break:break-all}
th{background:#eef1f7} tr:nth-child(even) td{background:#fafbfd}
.badge{display:inline-block;border-radius:10px;padding:1px 8px;font-size:11px;background:#e8edf9;color:#2b4f9e}
.badge.warn{background:#fdecec;color:#b02a2a}
.tree{background:#fff;border-radius:8px;padding:12px 16px;font-size:13px;line-height:1.9;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.tree details{margin-left:16px} .tree .leaf{margin-left:32px}
.tree summary{cursor:pointer;list-style-position:outside}
.note{color:#667;font-size:12px} .muted{color:#889}
.gen{font-size:12px;color:#667;margin-top:4px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:800px){.cols{grid-template-columns:1fr}}
"""


def write_report(
    out_dir: str,
    scan: ScanResult,
    findings: list[Finding],
    proposal: Proposal,
) -> dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    paths = {
        "html": os.path.join(out_dir, "report.html"),
        "rules": os.path.join(out_dir, "運用ルール案.md"),
        "csv": os.path.join(out_dir, "move_plan.csv"),
    }
    _write_csv(paths["csv"], proposal)
    _write_rules_md(paths["rules"], proposal)
    _write_html(paths["html"], scan, findings, proposal)
    return paths


def _write_csv(path: str, proposal: Proposal) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["既存フォルダ", "移行先", "アクション", "根拠"])
        for m in proposal.folder_mapping:
            w.writerow([m.get("from", ""), m.get("to", ""), m.get("action", ""), m.get("reason", "")])


def _write_rules_md(path: str, p: Proposal) -> None:
    def sec(title: str, items: list[str]) -> str:
        body = "\n".join(f"- {i}" for i in items) or "- （提案なし）"
        return f"## {title}\n\n{body}\n"

    tree_lines = "\n".join(
        f"- `{t.get('path','')}` — {t.get('purpose','')}（オーナー: {t.get('owner_role','未定')}）"
        for t in p.target_tree
    )
    content = "\n".join([
        "# フォルダ運用ルール案",
        "",
        f"生成: {datetime.now():%Y-%m-%d} / エンジン: {p.generated_by}",
        "",
        sec("1. 格納先の原則", p.principles),
        "## 2. 標準フォルダ体系\n",
        tree_lines or "- （提案なし）",
        "",
        sec("3. 命名規則", p.naming_rules),
        sec("4. 版管理（正式版/作業中の分離）", p.versioning_rules),
        sec("5. オーナー制・作成統制", p.governance),
        sec("6. ライフサイクル（保持・アーカイブ・廃棄）", p.lifecycle),
        sec("7. すぐ着手できる改善", p.quick_wins),
    ])
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(content)


def _write_html(path: str, scan: ScanResult, findings: list[Finding], p: Proposal) -> None:
    n_warn = sum(1 for f in findings if f.severity == "warn")
    cloud_only = sum(f.n_cloud_only for f in scan.folders)

    before = _tree_html([
        (f.path, f"{f.n_files} 件 / {_fmt_size(f.size)}" if f.n_files else "")
        for f in scan.folders
    ])
    after = _tree_html([
        (t.get("path", ""), t.get("purpose", ""))
        for t in p.target_tree
    ], open_depth=2)

    findings_rows = "".join(
        f"<tr><td><span class='badge {f.severity}'>{_esc(_KIND_LABELS.get(f.kind, f.kind))}</span></td>"
        f"<td>{_esc(f.path or '(ルート)')}</td><td>{_esc(f.detail)}</td></tr>"
        for f in findings
    )
    mapping_rows = "".join(
        f"<tr><td>{_esc(m.get('from',''))}</td><td>{_esc(m.get('to',''))}</td>"
        f"<td>{_esc(m.get('action',''))}</td><td>{_esc(m.get('reason',''))}</td></tr>"
        for m in p.folder_mapping
    )
    tree_rows = "".join(
        f"<tr><td>{_esc(t.get('path',''))}</td><td>{_esc(t.get('purpose',''))}</td>"
        f"<td>{_esc(t.get('owner_role',''))}</td></tr>"
        for t in p.target_tree
    )
    gen_note = "Azure OpenAI による提案" if p.generated_by == "llm" else f"ルールベース提案（{_esc(p.model_note)}）"

    doc = f"""<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>フォルダ体系 改善提案レポート</title><style>{_CSS}</style></head><body><div class="wrap">
<h1>フォルダ体系 改善提案レポート</h1>
<p class="gen">対象: {_esc(scan.source)} ／ バックエンド: {_esc(scan.backend)} ／ スキャン: {_esc(scan.scanned_at)} ／ {gen_note}</p>
<div class="cards">
<div class="card"><div class="v">{len(scan.folders):,}</div><div class="k">フォルダ</div></div>
<div class="card"><div class="v">{scan.total_files:,}</div><div class="k">ファイル</div></div>
<div class="card"><div class="v">{_fmt_size(scan.total_size)}</div><div class="k">合計容量</div></div>
<div class="card"><div class="v">{n_warn:,}</div><div class="k">要対応の所見</div></div>
<div class="card"><div class="v">{cloud_only:,}</div><div class="k">クラウド専用ファイル<br>（ダウンロードせず走査）</div></div>
</div>

<h2>課題所見（ルール分析）</h2>
<p class="muted">最終更新日はファイルの開閉・同期で書き換わるため参考値です。削除ではなくアーカイブ隔離を前提にしています。</p>
<table><tr><th>種別</th><th>フォルダ</th><th>内容</th></tr>{findings_rows or "<tr><td colspan=3>所見なし</td></tr>"}</table>

<h2>格納先の原則</h2>{_ul(p.principles)}

<h2>改善後フォルダ体系（案）</h2>
<table><tr><th>フォルダ</th><th>用途</th><th>オーナー役割</th></tr>{tree_rows or "<tr><td colspan=3>提案なし</td></tr>"}</table>

<h2>現状と改善後の構造比較</h2>
<div class="cols">
<div><h3>Before（現状）</h3>{before}</div>
<div><h3>After（提案）</h3>{after}</div>
</div>

<h2>フォルダ移行計画</h2>
<p class="muted">実ファイルは変更していません。この表（move_plan.csv にも出力）に沿って合意のうえ移行してください。</p>
<table><tr><th>既存フォルダ</th><th>移行先</th><th>アクション</th><th>根拠</th></tr>{mapping_rows or "<tr><td colspan=4>提案なし</td></tr>"}</table>

<h2>命名規則</h2>{_ul(p.naming_rules)}
<h2>版管理（正式版／作業中の分離）</h2>{_ul(p.versioning_rules)}
<h2>オーナー制・作成統制</h2>{_ul(p.governance)}
<h2>ライフサイクル（保持・アーカイブ・廃棄）</h2>{_ul(p.lifecycle)}
<h2>すぐ着手できる改善</h2>{_ul(p.quick_wins)}
</div></body></html>"""
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(doc)
