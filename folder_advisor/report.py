"""HTML レポート生成と移動計画 CSV 出力。

レポートは 1 ファイルで完結する HTML。外部 CDN やスクリプトに依存せず、
折り畳みツリーと SVG の容量ヒートマップ（Treemap）で before/after を可視化する。
"""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path

from .models import AnalysisResult, ScanResult
from .scoring import score_structure
from .visualize import (
    after_graph_json,
    after_tree_data,
    before_graph_json,
    before_tree_data,
)


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
            ["現在パス", "提案パス", "プロジェクト", "分類", "アクション", "重複", "旧版", "根拠"]
        )
        for m in analysis.move_plan:
            writer.writerow(
                [
                    m.current_path,
                    m.proposed_path,
                    m.project,
                    m.category,
                    m.action,
                    "○" if m.dup_flag else "",
                    "○" if m.old_version_flag else "",
                    m.reason,
                ]
            )


# --- 折り畳みツリー（大規模向けの主表現） -----------------------------------
# mermaid と違い縦に伸びるだけなので、数千ノードでも字が潰れずエッジも交差しない。
# JS 不要（<details> のネイティブ開閉）。既定では浅い階層だけ開いて表示する。
_TREE_OPEN_DEPTH = 2  # この深さまでは既定で開いておく


def _html_tree(node: dict, depth: int = 0) -> str:
    name = _h(node.get("name", ""))
    count = node.get("count", 0)
    total = node.get("total", 0)
    children = node.get("children", [])
    # 件数バッジ（直下 / 配下合計）。0 は表示しない。
    badges = ""
    if count:
        badges += f'<span class="b b-file">{_h(count)}</span>'
    if total and total != count:
        badges += f'<span class="b b-total">Σ{_h(total)}</span>'
    label = f'<span class="tname">{name}</span>{badges}'
    if not children:
        return f'<li class="leaf">{label}</li>'
    open_attr = " open" if depth < _TREE_OPEN_DEPTH else ""
    inner = "".join(_html_tree(c, depth + 1) for c in children)
    return (
        f'<li><details{open_attr}><summary>{label}'
        f'<span class="cc">{len(children)}フォルダ</span></summary>'
        f"<ul>{inner}</ul></details></li>"
    )


def _tree_block(title: str, node: dict) -> str:
    return (
        f'<div class="tree"><h3>{_h(title)}</h3>'
        f'<div class="tree-tools">'
        f'<button type="button" onclick="treeToggle(this,true)">すべて展開</button>'
        f'<button type="button" onclick="treeToggle(this,false)">すべて折り畳み</button>'
        f'</div>'
        f'<ul class="filetree">{_html_tree(node)}</ul></div>'
    )


# --- Treemap（面積＝配下ファイル数のヒートマップ） ---------------------------
# squarified treemap（Bruls et al.）。どのフォルダが重いか＝どこに削減余地が
# あるかを一目で把握するための補助表現。純 SVG（CDN・JS 不要）。
def _squarify(sizes: list[float], x: float, y: float, dx: float, dy: float) -> list[dict]:
    """正規化済み面積 sizes（降順）を矩形リストに割り付ける。"""
    if not sizes:
        return []
    if len(sizes) == 1:
        return _layout(sizes, x, y, dx, dy)
    i = 1
    while i < len(sizes) and _worst(sizes[:i], dx, dy) >= _worst(sizes[: i + 1], dx, dy):
        i += 1
    current, remaining = sizes[:i], sizes[i:]
    lx, ly, ldx, ldy = _leftover(current, x, y, dx, dy)
    return _layout(current, x, y, dx, dy) + _squarify(remaining, lx, ly, ldx, ldy)


def _layout(sizes, x, y, dx, dy):
    covered = sum(sizes)
    rects = []
    if dx >= dy:
        width = covered / dy if dy else 0
        for s in sizes:
            h = s / width if width else 0
            rects.append({"x": x, "y": y, "dx": width, "dy": h})
            y += h
    else:
        height = covered / dx if dx else 0
        for s in sizes:
            w = s / height if height else 0
            rects.append({"x": x, "y": y, "dx": w, "dy": height})
            x += w
    return rects


def _leftover(sizes, x, y, dx, dy):
    covered = sum(sizes)
    if dx >= dy:
        width = covered / dy if dy else 0
        return x + width, y, dx - width, dy
    height = covered / dx if dx else 0
    return x, y + height, dx, dy - height


def _worst(sizes, dx, dy):
    layout = _layout(sizes, 0, 0, dx, dy)
    ratios = []
    for r in layout:
        w, h = r["dx"], r["dy"]
        if w <= 0 or h <= 0:
            return float("inf")
        ratios.append(max(w / h, h / w))
    return max(ratios) if ratios else float("inf")


# 深さごとの塗り色（薄→濃）。ラベル高さと最小描画サイズの閾値。
_TM_COLORS = ["#dbe4f0", "#c3d4ea", "#a9c1e0", "#8faed6"]
_TM_LABEL_H = 16.0
_TM_MIN = 26.0  # これより小さい矩形は子を描かない（潰れ防止）


def _treemap_cells(node: dict, x, y, w, h, depth, max_depth, out: list) -> None:
    children = node.get("children", [])
    if not children or depth >= max_depth or w < _TM_MIN or h < _TM_MIN:
        return
    weighted = [(max(c.get("total", 0), 1), c) for c in children]
    total = sum(wt for wt, _ in weighted)
    if total <= 0:
        return
    area = w * h
    norm = [(wt * area / total, c) for wt, c in weighted]
    norm.sort(key=lambda t: t[0], reverse=True)
    rects = _squarify([n for n, _ in norm], x, y, w, h)
    for r, (_, child) in zip(rects, norm):
        out.append((r["x"], r["y"], r["dx"], r["dy"], child, depth))
        # ラベル分を上に空けて内側を再帰。
        _treemap_cells(
            child,
            r["x"] + 1,
            r["y"] + _TM_LABEL_H,
            r["dx"] - 2,
            r["dy"] - _TM_LABEL_H - 1,
            depth + 1,
            max_depth,
            out,
        )


def _treemap_svg(node: dict, width: int = 1040, height: int = 460, max_depth: int = 2) -> str:
    cells: list = []
    _treemap_cells(node, 0, 0, float(width), float(height), 0, max_depth, cells)
    if not cells:
        return "<p>表示できるフォルダがありません。</p>"
    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'preserveAspectRatio="xMidYMid meet" class="treemap" '
        f'font-family="Segoe UI, Meiryo, sans-serif">'
    ]
    for rx, ry, rw, rh, child, depth in cells:
        color = _TM_COLORS[min(depth, len(_TM_COLORS) - 1)]
        name = _h(child.get("name", ""))
        total = child.get("total", 0)
        parts.append(
            f'<rect x="{rx:.1f}" y="{ry:.1f}" width="{rw:.1f}" height="{rh:.1f}" '
            f'fill="{color}" stroke="#fff" stroke-width="1.5" rx="2">'
            f"<title>{name}｜配下{_h(total)}ファイル</title></rect>"
        )
        # 幅・高さに余裕がある時だけラベルを描く。
        if rw > 46 and rh > 14:
            parts.append(
                f'<text x="{rx + 4:.1f}" y="{ry + 12:.1f}" font-size="11" '
                f'fill="#1a2a44" clip-path="inset(0)">{name} ({_h(total)})</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


def _summary_cards(summary: dict) -> str:
    cards = [
        ("総ファイル数", summary.get("total_files", 0)),
        ("総フォルダ数", summary.get("total_dirs", 0)),
        ("完全重複グループ", summary.get("duplicate_groups", 0)),
        ("冗長コピー", summary.get("redundant_copies", 0)),
        ("削減見込み", _fmt_bytes(summary.get("reclaimable_bytes", 0))),
        ("旧版の可能性", summary.get("old_versions", 0)),
        ("プロジェクト", summary.get("projects", 0)),
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
            f"<td>{_h(m.project)}</td><td>{_h(m.category)}</td>"
            f"<td class='{cls}'>{_h(m.action)}</td>"
            f"<td>{_h(m.reason)}</td></tr>"
        )
    note = ""
    if len(analysis.move_plan) > limit:
        note = f"<p>※ 表示は先頭 {limit} 件。全件は move_plan.csv を参照。</p>"
    return (
        "<table><thead><tr><th>現在パス</th><th>提案パス</th><th>プロジェクト</th>"
        "<th>分類</th><th>アクション</th><th>根拠</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>{note}"
    )


def _score_color(score: int) -> str:
    if score >= 75:
        return "#0a7d33"
    if score >= 60:
        return "#b5651d"
    return "#b02a37"


def _score_section(scan: ScanResult, analysis: AnalysisResult) -> str:
    """整理健全度スコア（改善前→改善後・100点満点）のブロックを組み立てる。"""
    sc = score_structure(scan, analysis)
    b, a = sc["before_total"], sc["after_total"]
    delta = sc["delta"]
    bcol, acol = _score_color(b), _score_color(a)

    # 大きな before → after 表示。
    big = f"""
      <div class="scorewrap">
        <div class="scorebox">
          <div class="scorecap">改善前</div>
          <div class="scoreval" style="color:{bcol}">{b}<span class="max">/100</span></div>
          <div class="scoregrade">{_h(sc['grade_before'])}</div>
        </div>
        <div class="scorearrow">→<div class="scoredelta">+{delta}</div></div>
        <div class="scorebox">
          <div class="scorecap">改善後</div>
          <div class="scoreval" style="color:{acol}">{a}<span class="max">/100</span></div>
          <div class="scoregrade">{_h(sc['grade_after'])}</div>
        </div>
      </div>"""

    # 観点別の内訳（重み・before・after・バー）。
    rows = []
    for d in sc["dimensions"]:
        rows.append(
            f"<tr><td>{_h(d['label'])}<br><span class='dimdetail'>"
            f"{_h(d['before_detail'])} → {_h(d['after_detail'])}</span></td>"
            f"<td class='num'>{_h(d['weight'])}%</td>"
            f"<td class='num'>{_h(d['before'])}</td>"
            f"<td class='num'><b style='color:{_score_color(d['after'])}'>{_h(d['after'])}</b></td>"
            f"<td class='barcell'><div class='bar'>"
            f"<div class='barfill barbefore' style='width:{d['before']}%'></div>"
            f"<div class='barfill barafter' style='width:{d['after']}%'></div>"
            f"</div></td></tr>"
        )
    table = (
        "<table class='scoretable'><thead><tr><th>観点</th><th>重み</th>"
        "<th>改善前</th><th>改善後</th><th>スコア（薄=前 / 濃=後）</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )
    note = (
        "<p class='note'>※ 整理健全度＝重複の排除・旧版の隔離・散在の解消・案件のまとまり"
        "を重み付けした目安値（100点満点）。案件のまとまりは"
        + ("LLM が束ねた案件単位" if sc["has_projects"] else "同名資料の単位")
        + "で分散を評価。対象ファイル "
        + str(sc["total_files"]) + " 件（構成ファイルを除く）。</p>"
    )
    return big + table + note


def build_html(scan: ScanResult, analysis: AnalysisResult) -> str:
    before_tree = before_tree_data(scan)
    after_tree = after_tree_data(analysis.proposed_tree)
    # 前後の Treemap は横並び比較のため、やや小さめの viewBox で描く。
    before_treemap = _treemap_svg(before_tree, width=560, height=440)
    after_treemap = _treemap_svg(after_tree, width=560, height=440)
    summary = analysis.summary
    llm_note = (
        "LLM補助（プロジェクト束ね）：有効"
        if analysis.llm_used
        else "LLM補助：無効（据置中心・重複統合/旧版隔離のみ）"
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
  .tree {{ flex:1 1 460px; min-width:320px; background:#fafbfc;
          border:1px solid #e2e5ea; border-radius:8px; padding:10px; }}
  .tree h3 {{ margin:.2rem 0 .4rem; font-size:.95rem; }}
  .tree-tools {{ margin-bottom:6px; }}
  .tree-tools button {{ font-size:.72rem; padding:2px 8px; margin-right:6px;
          border:1px solid #c4ccd6; border-radius:5px; background:#eef2f8;
          color:#1f3a5f; cursor:pointer; }}
  .filetree, .filetree ul {{ list-style:none; margin:0; padding:0; }}
  .filetree ul {{ margin-left:14px; border-left:1px dotted #c4ccd6; padding-left:10px; }}
  .filetree li {{ font-size:.82rem; line-height:1.9; white-space:nowrap; }}
  .filetree summary {{ cursor:pointer; }}
  .filetree summary::marker {{ color:#1f5fa8; }}
  .filetree .leaf {{ padding-left:14px; color:#333; }}
  .filetree .tname {{ font-weight:600; color:#1f3a5f; }}
  .filetree .b {{ display:inline-block; margin-left:6px; padding:0 6px; border-radius:9px;
          font-size:.7rem; font-weight:600; }}
  .filetree .b-file {{ background:#e3edf9; color:#1f5fa8; }}
  .filetree .b-total {{ background:#eef1e6; color:#5c6b2f; }}
  .filetree .cc {{ margin-left:6px; font-size:.7rem; color:#8a93a0; }}
  .treemap-wrap {{ width:100%; overflow-x:auto; }}
  .treemap {{ display:block; }}
  .treemap text {{ pointer-events:none; }}
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
  details summary {{ cursor:pointer; font-size:.8rem; color:#1f5fa8; }}
  .note {{ font-size:.8rem; color:#666; }}
  .scorewrap {{ display:flex; align-items:center; justify-content:center; gap:24px;
               flex-wrap:wrap; margin:6px 0 18px; }}
  .scorebox {{ text-align:center; background:#f4f7fb; border:1px solid #e2e5ea;
              border-radius:12px; padding:14px 26px; min-width:150px; }}
  .scorecap {{ font-size:.8rem; color:#555; }}
  .scoreval {{ font-size:3rem; font-weight:800; line-height:1.1; }}
  .scoreval .max {{ font-size:1rem; font-weight:600; color:#888; }}
  .scoregrade {{ font-size:.9rem; color:#444; letter-spacing:.1em; }}
  .scorearrow {{ font-size:2rem; color:#1f3a5f; text-align:center; }}
  .scoredelta {{ font-size:1rem; font-weight:700; color:#0a7d33; }}
  table.scoretable td.num {{ text-align:right; white-space:nowrap; }}
  .dimdetail {{ font-size:.72rem; color:#777; }}
  .barcell {{ min-width:160px; width:34%; }}
  .bar {{ position:relative; height:16px; background:#eceff3; border-radius:8px; }}
  .barfill {{ position:absolute; top:0; left:0; height:100%; border-radius:8px; }}
  .barbefore {{ background:#c7d4e6; }}
  .barafter {{ background:#1f5fa8; height:8px; top:4px; }}
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
    <h2>整理健全度スコア（改善前 → 改善後）</h2>
    {_score_section(scan, analysis)}
  </section>

  <section>
    <h2>改善前後の構造（before / after）</h2>
    <p class="note">大規模でも字が潰れないよう、折り畳みツリーで表示しています。
       各行の <span class="b b-file">n</span> は直下ファイル数、
       <span class="b b-total">Σn</span> は配下合計です。</p>
    <div class="diagrams">
      {_tree_block("改善前（現状）", before_tree)}
      {_tree_block("改善後（提案）", after_tree)}
    </div>
  </section>

  <section>
    <h2>容量ヒートマップ（配下ファイル数・上位2階層）</h2>
    <p class="note">面積が大きいフォルダ＝ファイルが集中している場所です。
       改善前後を並べると、集約・アーカイブ隔離で分散がどう解消されるかが分かります。</p>
    <div class="diagrams">
      <div class="tree"><h3>改善前（現状）</h3>
        <div class="treemap-wrap">{before_treemap}</div></div>
      <div class="tree"><h3>改善後（提案）</h3>
        <div class="treemap-wrap">{after_treemap}</div></div>
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
    <h2>移動計画（提案）</h2>
    <p class="note">実ファイルは変更していません。以下は提案です。全件は
       <b>move_plan.csv</b> を参照してください。</p>
    <div class="scroll">{_move_plan_table(analysis)}</div>
  </section>
</main>
<script>
  // 折り畳みツリーの一括開閉。押されたボタンが属する .tree 内だけを対象にする。
  function treeToggle(btn, open) {{
    var root = btn.closest('.tree');
    if (!root) return;
    root.querySelectorAll('details').forEach(function (d) {{ d.open = open; }});
  }}
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
    }
