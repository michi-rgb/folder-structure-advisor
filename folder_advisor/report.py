"""HTML レポート生成と移動計画 CSV 出力。

レポートは 1 ファイルで完結する HTML。before/after のフォルダ構成は、巨大化して
判読困難になる mermaid 図を廃止し、外部 CDN に依存しない自前の描画に置き換えた
（社内ネットワークでもオフラインで表示できる）。改善前後それぞれについて、

- **折り畳みツリー**：フォルダを開閉しながら構造を辿れるツリー
- **Treemap（容量ヒートマップ）**：面積＝容量・色の濃淡＝容量の大小で、どこが
  容量を食っているか／整理でどう変わるかを俯瞰

をブラウザ内蔵の JS（外部依存なし）で描画する。
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

# 外部 CDN に依存しない、before/after 用の折り畳みツリー＋Treemap 描画スクリプト。
# f-string ではなく素の文字列として保持し、波括弧のエスケープを避ける。
_VIZ_JS = r"""
(function () {
  var DATA = window.__VIZ__ || {};

  function fmtBytes(n) {
    n = n || 0;
    var u = ["B", "KB", "MB", "GB", "TB"], i = 0, v = n;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return (i === 0 ? n : v.toFixed(1)) + u[i];
  }

  // 配下の総ファイル数を前計算してノードに付与する。
  function decorate(node) {
    if (node.type !== "dir") { node._tf = 1; return 1; }
    var t = 0, ch = node.children || [];
    for (var i = 0; i < ch.length; i++) t += decorate(ch[i]);
    node._tf = t;
    return t;
  }

  // --- 折り畳みツリー --------------------------------------------------------
  function buildNode(node, depth) {
    var el = document.createElement("div");
    el.className = "tnode";
    var isDir = node.type === "dir";
    var hasKids = isDir && node.children && node.children.length;
    var row = document.createElement("div");
    row.className = "trow " + (isDir ? "dir" : "file");

    var caret = document.createElement("span");
    caret.className = "tcaret";
    caret.textContent = hasKids ? "▾" : "";
    var icon = document.createElement("span");
    icon.className = "ticon";
    icon.textContent = isDir ? "📁" : "📄";
    var name = document.createElement("span");
    name.className = "tname";
    name.textContent = node.name;
    var meta = document.createElement("span");
    meta.className = "tmeta";
    meta.textContent = isDir
      ? "  " + node._tf + " ファイル・" + fmtBytes(node.size)
      : "  " + fmtBytes(node.size);

    row.appendChild(caret);
    row.appendChild(icon);
    row.appendChild(name);
    row.appendChild(meta);
    el.appendChild(row);

    if (hasKids) {
      var kids = document.createElement("div");
      kids.className = "tchildren";
      node.children.forEach(function (c) { kids.appendChild(buildNode(c, depth + 1)); });
      el.appendChild(kids);
      row.addEventListener("click", function () { el.classList.toggle("collapsed"); });
      if (depth >= 1) el.classList.add("collapsed"); // 既定はルート直下のみ展開
    }
    return el;
  }

  function renderTree(pane, root) {
    var ctl = document.createElement("div");
    ctl.className = "tctl";
    var bAll = document.createElement("button");
    bAll.textContent = "すべて展開";
    var bNone = document.createElement("button");
    bNone.textContent = "すべて折りたたむ";
    ctl.appendChild(bAll);
    ctl.appendChild(bNone);
    pane.appendChild(ctl);

    var treeEl = buildNode(root, 0);
    pane.appendChild(treeEl);

    bAll.addEventListener("click", function () {
      pane.querySelectorAll(".tnode.collapsed").forEach(function (n) { n.classList.remove("collapsed"); });
    });
    bNone.addEventListener("click", function () {
      pane.querySelectorAll(".tnode").forEach(function (n, i) {
        if (n !== treeEl && n.querySelector(".tchildren")) n.classList.add("collapsed");
      });
    });
  }

  // --- Treemap（容量ヒートマップ） ------------------------------------------
  // 面積を二分割していく素朴な squarified 近似。外部ライブラリ不要で見やすい。
  function layout(items, rect, out) {
    if (items.length === 0) return;
    if (items.length === 1) { out.push({ node: items[0].node, rect: rect }); return; }
    var total = 0, i;
    for (i = 0; i < items.length; i++) total += items[i].area;
    var acc = 0, split = 0;
    for (i = 0; i < items.length; i++) {
      if (i > 0 && acc + items[i].area > total / 2) break;
      acc += items[i].area; split = i + 1;
    }
    var g1 = items.slice(0, split), g2 = items.slice(split);
    var f = acc / total;
    if (rect.w >= rect.h) {
      var wl = rect.w * f;
      layout(g1, { x: rect.x, y: rect.y, w: wl, h: rect.h }, out);
      layout(g2, { x: rect.x + wl, y: rect.y, w: rect.w - wl, h: rect.h }, out);
    } else {
      var ht = rect.h * f;
      layout(g1, { x: rect.x, y: rect.y, w: rect.w, h: ht }, out);
      layout(g2, { x: rect.x, y: rect.y + ht, w: rect.w, h: rect.h - ht }, out);
    }
  }

  function heat(t) { // t:0..1 -> 薄い青→濃い紺
    t = Math.max(0, Math.min(1, t));
    var a = [230, 238, 247], b = [31, 58, 95];
    var r = Math.round(a[0] + (b[0] - a[0]) * t);
    var g = Math.round(a[1] + (b[1] - a[1]) * t);
    var bl = Math.round(a[2] + (b[2] - a[2]) * t);
    return "rgb(" + r + "," + g + "," + bl + ")";
  }

  function renderTreemap(pane, rootRoot) {
    pane.innerHTML = "";
    var crumb = document.createElement("div");
    crumb.className = "tmcrumb";
    var wrap = document.createElement("div");
    wrap.className = "tmwrap";
    pane.appendChild(crumb);
    pane.appendChild(wrap);

    var stack = [rootRoot]; // ドリルダウン用スタック

    function draw() {
      var current = stack[stack.length - 1];
      // パンくず
      crumb.innerHTML = "";
      stack.forEach(function (n, idx) {
        if (idx > 0) crumb.appendChild(document.createTextNode(" / "));
        if (idx < stack.length - 1) {
          var a = document.createElement("a");
          a.textContent = n.name;
          a.addEventListener("click", function () { stack = stack.slice(0, idx + 1); draw(); });
          crumb.appendChild(a);
        } else {
          crumb.appendChild(document.createTextNode(n.name));
        }
      });

      wrap.innerHTML = "";
      var W = wrap.clientWidth || 400, H = wrap.clientHeight || 420;
      var kids = (current.children || []).filter(function (c) { return (c.size || 0) > 0; });
      if (kids.length === 0) {
        var e = document.createElement("div");
        e.className = "tmempty";
        e.textContent = "容量情報のある項目がありません。";
        wrap.appendChild(e);
        return;
      }
      var maxSize = 0;
      kids.forEach(function (c) { if (c.size > maxSize) maxSize = c.size; });
      var totalSize = 0;
      kids.forEach(function (c) { totalSize += c.size; });
      var area = W * H;
      var items = kids.slice().sort(function (a, b) { return b.size - a.size; })
        .map(function (c) { return { node: c, area: c.size / totalSize * area }; });

      var cells = [];
      layout(items, { x: 0, y: 0, w: W, h: H }, cells);

      cells.forEach(function (c) {
        var node = c.node, r = c.rect;
        var div = document.createElement("div");
        var isDir = node.type === "dir";
        div.className = "tmcell" + (isDir ? " dir" : "");
        div.style.left = r.x + "px";
        div.style.top = r.y + "px";
        div.style.width = Math.max(0, r.w) + "px";
        div.style.height = Math.max(0, r.h) + "px";
        div.style.background = heat(maxSize ? node.size / maxSize : 0);
        var deep = isDir ? node._tf + " ファイル" : "ファイル";
        div.title = node.name + "\n" + fmtBytes(node.size) + "（" + deep + "）";
        if (r.w > 42 && r.h > 18) {
          var lab = document.createElement("div");
          lab.className = "tmlabel";
          lab.style.color = (maxSize && node.size / maxSize > 0.55) ? "#f5f8fc" : "#12233b";
          lab.textContent = (isDir ? "📁 " : "") + node.name + " · " + fmtBytes(node.size);
          div.appendChild(lab);
        }
        if (isDir && (node.children || []).some(function (x) { return (x.size || 0) > 0; })) {
          div.addEventListener("click", function () { stack.push(node); draw(); });
        }
        wrap.appendChild(div);
      });
    }

    pane._tmDraw = draw; // タブ表示時／リサイズ時に呼ぶ
    draw();
  }

  // --- 組み立て --------------------------------------------------------------
  ["before", "after"].forEach(function (side) {
    var root = DATA[side];
    if (!root) return;
    decorate(root);
    var treePane = document.querySelector('.vizpane.tree[data-side="' + side + '"]');
    var tmPane = document.querySelector('.vizpane.treemap[data-side="' + side + '"]');
    if (treePane) renderTree(treePane, root);
    if (tmPane) renderTreemap(tmPane, root);
  });

  // タブ切替。Treemap は表示された瞬間にサイズが確定するため再描画する。
  document.querySelectorAll(".viztabs").forEach(function (tabs) {
    var side = tabs.getAttribute("data-side");
    tabs.querySelectorAll(".vtab").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var view = btn.getAttribute("data-view");
        tabs.querySelectorAll(".vtab").forEach(function (b) { b.classList.remove("active"); });
        btn.classList.add("active");
        document.querySelectorAll('.vizpane[data-side="' + side + '"]').forEach(function (p) {
          var show = p.getAttribute("data-view") === view;
          p.hidden = !show;
          if (show && p._tmDraw) p._tmDraw();
        });
      });
    });
  });

  var rzTimer = null;
  window.addEventListener("resize", function () {
    clearTimeout(rzTimer);
    rzTimer = setTimeout(function () {
      document.querySelectorAll(".vizpane.treemap").forEach(function (p) {
        if (!p.hidden && p._tmDraw) p._tmDraw();
      });
    }, 200);
  });
})();
"""


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


def _viz_block(title: str, side: str) -> str:
    """1 側（before / after）分のツリー＋Treemap の器。中身は JS が描画する。"""
    return f"""
      <div class="diagram">
        <h3>{_h(title)}</h3>
        <div class="viztabs" data-side="{side}">
          <button class="vtab active" data-view="tree">折り畳みツリー</button>
          <button class="vtab" data-view="treemap">Treemap（容量ヒートマップ）</button>
        </div>
        <div class="vizpane tree" data-side="{side}" data-view="tree"></div>
        <div class="vizpane treemap" data-side="{side}" data-view="treemap" hidden></div>
      </div>"""


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
    viz_data = json.dumps(
        {
            "before": before_tree_data(scan),
            "after": after_tree_data(scan, analysis),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    # </script> がデータ中に現れると script 要素が閉じてしまうため無害化する。
    viz_data = viz_data.replace("</", "<\\/")
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
  .diagram {{ flex:1 1 460px; min-width:320px; background:#fafbfc;
             border:1px solid #e2e5ea; border-radius:8px; padding:10px; }}
  .diagram h3 {{ margin:.2rem 0 .6rem; font-size:1rem; }}
  .viztabs {{ display:flex; gap:6px; margin-bottom:8px; }}
  .vtab {{ font:inherit; font-size:.78rem; padding:5px 10px; cursor:pointer;
          border:1px solid #cdd4de; background:#eef2f8; color:#33415c;
          border-radius:6px; }}
  .vtab.active {{ background:#1f3a5f; color:#fff; border-color:#1f3a5f; }}
  .vizpane {{ overflow:auto; }}
  .vizpane.tree {{ max-height:520px; font-size:.82rem; }}
  /* 折り畳みツリー */
  .tnode {{ user-select:none; }}
  .trow {{ display:flex; align-items:center; gap:6px; padding:2px 4px;
          border-radius:5px; white-space:nowrap; }}
  .trow:hover {{ background:#eef2f8; }}
  .trow.dir {{ cursor:pointer; }}
  .tcaret {{ display:inline-block; width:1em; text-align:center; color:#7a869a;
            transition:transform .12s; }}
  .tnode.collapsed > .trow .tcaret {{ transform:rotate(-90deg); }}
  .tnode.collapsed > .tchildren {{ display:none; }}
  .ticon {{ width:1.1em; text-align:center; }}
  .tname {{ font-weight:600; color:#1f3a5f; }}
  .trow.file .tname {{ font-weight:400; color:#33415c; }}
  .tmeta {{ color:#7a869a; font-size:.74rem; }}
  .tchildren {{ margin-left:1.05em; border-left:1px dotted #d3d9e2; padding-left:.5em; }}
  .tctl {{ display:flex; gap:10px; margin-bottom:6px; }}
  .tctl button {{ font:inherit; font-size:.72rem; color:#1f5fa8; background:none;
                 border:none; cursor:pointer; padding:0; text-decoration:underline; }}
  /* Treemap */
  .tmwrap {{ position:relative; width:100%; height:420px; }}
  .tmcrumb {{ font-size:.75rem; margin-bottom:6px; color:#33415c; min-height:1.2em; }}
  .tmcrumb a {{ color:#1f5fa8; cursor:pointer; text-decoration:underline; }}
  .tmcell {{ position:absolute; box-sizing:border-box; border:1px solid rgba(255,255,255,.7);
            overflow:hidden; cursor:default; }}
  .tmcell.dir {{ cursor:pointer; }}
  .tmcell .tmlabel {{ font-size:.7rem; line-height:1.15; padding:2px 4px;
                     color:#12233b; pointer-events:none; }}
  .tmempty {{ color:#7a869a; font-size:.8rem; padding:12px; }}
  .tmlegend {{ display:flex; align-items:center; gap:8px; margin-top:12px;
              font-size:.75rem; color:#666; }}
  .tmbar {{ display:inline-block; width:160px; height:12px; border-radius:6px;
           background:linear-gradient(90deg,#e6eef7,#7fa8d6,#1f3a5f); }}
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
    <p class="note">各図は「折り畳みツリー」と「Treemap（容量ヒートマップ）」を切替できます。
       Treemap は面積＝容量・色が濃いほど大容量。フォルダをクリックすると
       その配下だけを掘り下げられます。</p>
    <div class="diagrams">
      {_viz_block("改善前（現状）", "before")}
      {_viz_block("改善後（提案）", "after")}
    </div>
    <div class="tmlegend">
      <span>容量：小</span>
      <span class="tmbar"></span>
      <span>大</span>
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
<script>window.__VIZ__ = {viz_data};</script>
<script>{_VIZ_JS}</script>
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
