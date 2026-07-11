"""HTML レポート生成と移動計画 CSV 出力。

レポートは 1 ファイルで完結する HTML。mermaid 図はブラウザで描画するため
mermaid.js を CDN から読み込むが、読み込めない環境（社内ネットワーク等）でも
図のソースを折り畳みで確認できるようフォールバックを用意する。
"""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path

from .models import AnalysisResult, ScanResult
from .visualize import (
    after_graph_json,
    after_mermaid,
    before_graph_json,
    before_mermaid,
)

_MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"


def _h(text) -> str:
    return html.escape(str(text))


def _fmt_bytes(n: int) -> str:
    val = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024 or unit == "TB":
            return f"{val:.1f}{unit}" if unit != "B" else f"{int(val)}B"
        val /= 1024
    return f"{n}B"


def write_move_plan_csv(analysis: AnalysisResult, out_path: Path) -> None:
    """移動計画を CSV（UTF-8 BOM 付き＝Excel で文字化けしない）で出力。"""
    with out_path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(
            ["現在パス", "提案パス", "分類", "アクション", "重複", "旧版", "根拠"]
        )
        for m in analysis.move_plan:
            writer.writerow(
                [
                    m.current_path,
                    m.proposed_path,
                    m.category,
                    m.action,
                    "○" if m.dup_flag else "",
                    "○" if m.old_version_flag else "",
                    m.reason,
                ]
            )


def _mermaid_block(title: str, code: str) -> str:
    esc_code = _h(code)
    return f"""
      <div class="diagram">
        <h3>{_h(title)}</h3>
        <div class="mermaid">{esc_code}</div>
        <details><summary>mermaid ソース（コピー用・図が表示されない場合）</summary>
          <pre class="mmsrc">{esc_code}</pre>
        </details>
      </div>"""


def _summary_cards(summary: dict) -> str:
    cards = [
        ("総ファイル数", summary.get("total_files", 0)),
        ("総フォルダ数", summary.get("total_dirs", 0)),
        ("完全重複グループ", summary.get("duplicate_groups", 0)),
        ("冗長コピー", summary.get("redundant_copies", 0)),
        ("削減見込み", _fmt_bytes(summary.get("reclaimable_bytes", 0))),
        ("旧版の可能性", summary.get("old_versions", 0)),
    ]
    items = "".join(
        f'<div class="card"><div class="num">{_h(v)}</div>'
        f'<div class="lbl">{_h(k)}</div></div>'
        for k, v in cards
    )
    return f'<div class="cards">{items}</div>'


def _dup_table(analysis: AnalysisResult) -> str:
    if not analysis.duplicate_groups:
        return "<p>完全重複は検出されませんでした。</p>"
    rows = []
    for g in analysis.duplicate_groups:
        red = "<br/>".join(_h(p) for p in g.redundant)
        rows.append(
            f"<tr><td>{_h(g.group_id)}</td><td>{_fmt_bytes(g.size)}</td>"
            f"<td class='ok'>{_h(g.primary)}</td><td>{red}</td></tr>"
        )
    return (
        "<table><thead><tr><th>グループ</th><th>サイズ</th>"
        "<th>正（残す）</th><th>統合対象（冗長コピー）</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _version_table(analysis: AnalysisResult) -> str:
    if not analysis.version_series:
        return "<p>旧版系列は検出されませんでした。</p>"
    rows = []
    for v in analysis.version_series:
        older = "<br/>".join(_h(p) for p in v.older)
        rows.append(
            f"<tr><td>{_h(v.base_name)}</td><td class='ok'>{_h(v.latest)}</td>"
            f"<td>{older}</td></tr>"
        )
    return (
        "<table><thead><tr><th>基準名</th><th>最新候補</th>"
        "<th>旧版候補（要確認）</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _category_table(analysis: AnalysisResult) -> str:
    if not analysis.category_counts:
        return ""
    rows = "".join(
        f"<tr><td>{_h(k)}</td><td>{_h(v)}</td></tr>"
        for k, v in sorted(analysis.category_counts.items(), key=lambda x: -x[1])
    )
    return (
        "<table><thead><tr><th>資料種別</th><th>件数</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _move_plan_table(analysis: AnalysisResult, limit: int = 500) -> str:
    rows = []
    for m in analysis.move_plan[:limit]:
        cls = {
            "統合": "act-merge",
            "移動": "act-move",
            "据置": "act-keep",
            "要確認": "act-check",
        }.get(m.action, "")
        rows.append(
            f"<tr><td>{_h(m.current_path)}</td><td>{_h(m.proposed_path)}</td>"
            f"<td>{_h(m.category)}</td><td class='{cls}'>{_h(m.action)}</td>"
            f"<td>{_h(m.reason)}</td></tr>"
        )
    note = ""
    if len(analysis.move_plan) > limit:
        note = f"<p>※ 表示は先頭 {limit} 件。全件は move_plan.csv を参照。</p>"
    return (
        "<table><thead><tr><th>現在パス</th><th>提案パス</th><th>分類</th>"
        "<th>アクション</th><th>根拠</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>{note}"
    )


def _naming_block(summary: dict) -> str:
    ns = summary.get("naming_suggestion") or {}
    if not ns:
        return ""
    examples = "".join(f"<li>{_h(e)}</li>" for e in ns.get("examples", []))
    return f"""
      <p><b>命名規約案：</b>{_h(ns.get('naming_rule', ''))}</p>
      <p><b>フォルダ体系方針：</b>{_h(ns.get('folder_policy', ''))}</p>
      {'<ul>' + examples + '</ul>' if examples else ''}"""


def build_html(scan: ScanResult, analysis: AnalysisResult) -> str:
    before = before_mermaid(scan)
    after = after_mermaid(analysis.proposed_tree)
    summary = analysis.summary
    llm_note = (
        "LLM補助（Azure OpenAI）：有効"
        if analysis.llm_used
        else "LLM補助（Azure OpenAI）：無効（ルールベースのみ）"
    )
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>フォルダ構成 改善提案レポート</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: "Segoe UI", "Meiryo", system-ui, sans-serif; margin: 0;
         line-height: 1.6; background:#f6f7f9; color:#1a1a1a; }}
  header {{ background:#1f3a5f; color:#fff; padding:20px 28px; }}
  header h1 {{ margin:0; font-size:1.4rem; }}
  header .meta {{ opacity:.85; font-size:.85rem; margin-top:4px; }}
  main {{ max-width:1100px; margin:0 auto; padding:24px 16px 80px; }}
  section {{ background:#fff; border:1px solid #e2e5ea; border-radius:10px;
            padding:18px 22px; margin:18px 0; }}
  h2 {{ font-size:1.15rem; border-left:5px solid #1f3a5f; padding-left:10px; }}
  .cards {{ display:flex; flex-wrap:wrap; gap:12px; }}
  .card {{ flex:1 1 140px; background:#eef2f8; border-radius:10px; padding:14px;
          text-align:center; }}
  .card .num {{ font-size:1.6rem; font-weight:700; color:#1f3a5f; }}
  .card .lbl {{ font-size:.8rem; color:#555; }}
  .diagrams {{ display:flex; flex-wrap:wrap; gap:16px; }}
  .diagram {{ flex:1 1 460px; min-width:320px; background:#fafbfc;
             border:1px solid #e2e5ea; border-radius:8px; padding:10px; overflow:auto; }}
  table {{ border-collapse:collapse; width:100%; font-size:.85rem; }}
  th, td {{ border:1px solid #dfe3e8; padding:6px 8px; text-align:left;
           vertical-align:top; word-break:break-all; }}
  th {{ background:#eef2f8; position:sticky; top:0; }}
  .ok {{ color:#0a7d33; font-weight:600; }}
  .act-merge {{ color:#b5651d; font-weight:600; }}
  .act-move {{ color:#1f5fa8; font-weight:600; }}
  .act-keep {{ color:#666; }}
  .act-check {{ color:#b02a37; font-weight:600; }}
  .scroll {{ max-height:520px; overflow:auto; }}
  pre.mmsrc {{ white-space:pre-wrap; font-size:.75rem; background:#f0f0f0; padding:8px; }}
  details summary {{ cursor:pointer; font-size:.8rem; color:#1f5fa8; }}
  .note {{ font-size:.8rem; color:#666; }}
</style></head>
<body>
<header>
  <h1>フォルダ構成 改善提案レポート</h1>
  <div class="meta">対象: {_h(scan.root)}（mode: {_h(scan.mode)}）｜走査: {_h(scan.scanned_at)}｜{_h(llm_note)}</div>
</header>
<main>
  <section>
    <h2>サマリ</h2>
    {_summary_cards(summary)}
    <p class="note">※ 更新日・作成者は開閉や雛形の影響で実態を反映しないため低信頼として扱い、
       重複判定は内容ハッシュ（SHA-256）を用いています。</p>
  </section>

  <section>
    <h2>改善前後の構造（before / after）</h2>
    <div class="diagrams">
      {_mermaid_block("改善前（現状）", before)}
      {_mermaid_block("改善後（提案）", after)}
    </div>
  </section>

  <section>
    <h2>完全重複（統合候補）</h2>
    <div class="scroll">{_dup_table(analysis)}</div>
  </section>

  <section>
    <h2>旧版系列（アーカイブ候補・要確認）</h2>
    <div class="scroll">{_version_table(analysis)}</div>
  </section>

  <section>
    <h2>資料種別の内訳</h2>
    {_category_table(analysis)}
  </section>

  <section>
    <h2>命名規約・フォルダ体系の提案</h2>
    {_naming_block(summary)}
  </section>

  <section>
    <h2>移動計画（提案）</h2>
    <p class="note">実ファイルは変更していません。以下は提案です。全件は
       <b>move_plan.csv</b> を参照してください。</p>
    <div class="scroll">{_move_plan_table(analysis)}</div>
  </section>
</main>
<script src="{_MERMAID_CDN}"></script>
<script>
  try {{
    if (window.mermaid) {{ mermaid.initialize({{ startOnLoad: true, theme: 'neutral' }}); }}
  }} catch (e) {{ console.warn('mermaid init failed', e); }}
</script>
</body></html>"""


def write_report(scan: ScanResult, analysis: AnalysisResult, out_dir: Path) -> dict:
    """HTML・CSV・グラフ JSON を out_dir に書き出し、パスを返す。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "report.html"
    csv_path = out_dir / "move_plan.csv"
    graph_path = out_dir / "graph.json"

    html_path.write_text(build_html(scan, analysis), encoding="utf-8")
    write_move_plan_csv(analysis, csv_path)

    # mermaid 図を単体ファイルとしても出力（要件: mermaid 図として可視化）。
    (out_dir / "before.mmd").write_text(before_mermaid(scan), encoding="utf-8")
    (out_dir / "after.mmd").write_text(
        after_mermaid(analysis.proposed_tree), encoding="utf-8"
    )
    graph_path.write_text(
        json.dumps(
            {
                "before": before_graph_json(scan),
                "after": after_graph_json(analysis.proposed_tree),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "report": str(html_path),
        "csv": str(csv_path),
        "graph": str(graph_path),
        "before_mmd": str(out_dir / "before.mmd"),
        "after_mmd": str(out_dir / "after.mmd"),
    }
