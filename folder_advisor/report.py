"""レポート出力。

- report.html      自己完結 HTML（外部 CDN 不要・オフライン閲覧可）
- 運用ルール案.md   命名・版管理・ガバナンス・ライフサイクルのルール文書
- move_plan.csv    フォルダ移行計画（Excel 用 BOM 付き UTF-8）
"""
from __future__ import annotations

import csv
import html
import json
import os
import re
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


def _natural_key(name: str):
    """フォルダ名の昇順ソート用キー。数字部分は数値として比較する（例: 2_ < 10_）。

    先頭は必ず非数字チャンク・数字チャンクが交互に並ぶため、同じ位置は常に同じ型
    （偶数位置=文字列, 奇数位置=int）となり、int と str の比較エラーは起きない。
    """
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


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
        for name in sorted(node, key=_natural_key):
            full = f"{prefix}/{name}" if prefix else name
            note = notes.get(full, "")
            label = f"<span class='dir' data-path=\"{_esc(full)}\">📁 {_esc(name)}</span>"
            if note:
                label += f" <span class='note'>{_esc(note)}</span>"
            if node[name]:
                op = " open" if depth < open_depth else ""
                kids = f"<div class='kids'>{render(node[name], full, depth + 1)}</div>"
                out.append(f"<details{op}><summary>{label}</summary>{kids}</details>")
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
.tree .kids{margin-left:6px;border-left:1px solid #d0d5dd;padding-left:9px}
.tree summary{cursor:pointer;list-style:none;display:flex;align-items:baseline;gap:4px}
.tree summary::-webkit-details-marker{display:none}
.tree summary::before{content:'\\25b6';display:inline-block;width:12px;flex:none;font-size:10px;color:#889}
.tree details[open]>summary::before{content:'\\25bc'}
.tree .leaf{display:flex;align-items:baseline;gap:4px;padding-left:16px}
.note{color:#667;font-size:12px} .muted{color:#889}
.gen{font-size:12px;color:#667;margin-top:4px}
.treebar{margin:8px 0}
.treebar button{font:inherit;font-size:12px;cursor:pointer;background:#fff;border:1px solid #cbd3e1;border-radius:6px;padding:4px 12px;margin-right:8px;color:#2b4f9e}
.treebar button:hover{background:#eef1f7}
.arrowtoggle{font-size:12px;color:#2b4f9e;cursor:pointer;user-select:none;margin-left:4px}
.arrowtoggle input{vertical-align:middle;margin-right:4px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.migwrap{position:relative}
.migwrap.arrows-on .cols{gap:56px}
.migwrap .cols{position:relative;z-index:1}
.miglines{position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none;overflow:visible;z-index:2;display:none}
.migwrap.arrows-on .miglines{display:block}
.miglines path:hover{stroke-width:3;opacity:1}
.miglegend{display:flex;gap:18px;flex-wrap:wrap;font-size:12px;color:#556;margin:6px 0 12px}
.miglegend span{display:inline-flex;align-items:center;gap:6px}
.miglegend i{width:22px;border-top:2px solid;display:inline-block}
@media(max-width:800px){.miglines{display:none}.migwrap.arrows-on .miglines{display:none}.migwrap.arrows-on .cols{gap:16px}.cols{grid-template-columns:1fr}}
"""


# 一括開閉ボタン + 構造比較ツリー上の移行矢印描画（window.MIG に移行マッピングが入る）。
# f-string ではないため波括弧のエスケープ不要。
_JS = r"""
function treeToggle(id, open){
  var root = document.getElementById(id);
  if(!root) return;
  root.querySelectorAll('details').forEach(function(d){ d.open = open; });
}

// details の入れ子深さ（祖先 details 数）を返す。
function treeDepthOf(root, d){
  var depth = 0;
  for(var p = d.parentElement; p && p !== root; p = p.parentElement){
    if(p.tagName === 'DETAILS') depth++;
  }
  return depth;
}

// level 段目までの details を開き、それより深い階層は閉じる。
function treeToggleDepth(id, level){
  var root = document.getElementById(id);
  if(!root) return;
  root.querySelectorAll('details').forEach(function(d){
    d.open = treeDepthOf(root, d) < level;
  });
}

// 押すたびに現在の展開段数を 1 つ深くする（1階層→2階層→3階層…）。
function treeExpandMore(id){
  var root = document.getElementById(id);
  if(!root) return;
  var minClosed = Infinity, maxDepth = 0;
  root.querySelectorAll('details').forEach(function(d){
    var depth = treeDepthOf(root, d);
    if(depth + 1 > maxDepth) maxDepth = depth + 1;
    if(!d.open && depth < minClosed) minClosed = depth;  // 最も浅い「閉じた」階層 = 現在の展開段数
  });
  var current = (minClosed === Infinity) ? maxDepth : minClosed;
  treeToggleDepth(id, Math.min(current + 1, maxDepth));
}

function toggleArrows(){
  var wrap = document.getElementById('cmp');
  var cb = document.getElementById('arrowsOn');
  var legend = document.getElementById('arrowlegend');
  if(!wrap || !cb) return;
  var on = cb.checked;
  wrap.classList.toggle('arrows-on', on);
  if(legend) legend.hidden = !on;
  if(on) window.drawArrows();
}

(function(){
  var raf = null;
  function schedule(){ if(raf) return; raf = requestAnimationFrame(function(){ raf = null; window.drawArrows(); }); }

  function esc(s){
    return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];
    });
  }
  function buildMap(root){
    var m = {};
    root.querySelectorAll('.dir[data-path]').forEach(function(el){ m[el.getAttribute('data-path')] = el; });
    return m;
  }
  // path に一致する可視ノードを探す。無ければ祖先パスへ遡る（折りたたみ/非存在に対応）。
  function resolve(map, path){
    var parts = (path || '').split('/');
    while(parts.length){
      var el = map[parts.join('/')];
      if(el && el.offsetParent !== null) return el;
      parts.pop();
    }
    return null;
  }
  function colorFor(action){
    action = action || '';
    if(/アーカイブ|廃棄|削除|除外/.test(action)) return '#c26a1e';
    if(/統合|集約|マージ|merge/i.test(action)) return '#7a4fbf';
    return '#3b6fd4';
  }

  window.drawArrows = function(){
    var wrap = document.getElementById('cmp');
    if(!wrap) return;
    var svg = wrap.querySelector('.miglines');
    if(!svg) return;
    // 矢印が非表示のときは描画しない（クリアのみ）。
    if(!wrap.classList.contains('arrows-on') || !window.MIG){ svg.innerHTML = ''; return; }
    var cols = wrap.querySelectorAll('.cols > div');
    if(cols.length < 2) return;
    var leftMap = buildMap(cols[0]);
    var rightMap = buildMap(cols[1]);
    var wr = wrap.getBoundingClientRect();
    var out = [];
    (window.MIG || []).forEach(function(m){
      var a = resolve(leftMap, m.from);
      var b = resolve(rightMap, m.to);
      if(!a || !b) return;
      var ar = a.getBoundingClientRect(), br = b.getBoundingClientRect();
      var x1 = ar.right - wr.left + 2, y1 = ar.top + ar.height / 2 - wr.top;
      var x2 = br.left - wr.left - 8, y2 = br.top + br.height / 2 - wr.top;
      var col = colorFor(m.action);
      var mx = (x1 + x2) / 2;
      var d = 'M' + x1 + ',' + y1 + ' C' + mx + ',' + y1 + ' ' + mx + ',' + y2 + ' ' + x2 + ',' + y2;
      var tip = (m.from || '') + '  →  ' + (m.to || '') +
        (m.action ? '  [' + m.action + ']' : '') + (m.reason ? '\n' + m.reason : '');
      out.push('<path d="' + d + '" fill="none" stroke="' + col + '" stroke-width="1.6" opacity="0.85"' +
        ' style="pointer-events:stroke;cursor:pointer"><title>' + esc(tip) + '</title></path>');
      out.push('<polygon points="' + (x2 + 6) + ',' + y2 + ' ' + x2 + ',' + (y2 - 4) + ' ' + x2 + ',' + (y2 + 4) +
        '" fill="' + col + '" opacity="0.9"></polygon>');
    });
    svg.innerHTML = out.join('');
  };

  window.addEventListener('resize', schedule);
  document.addEventListener('DOMContentLoaded', function(){
    var cmp = document.getElementById('cmp');
    if(cmp) cmp.addEventListener('toggle', schedule, true);
  });
})();
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
    # After ツリーには target_tree（枠組み）に加え、移動してくる実フォルダも子ノードとして載せる。
    target_paths = {t.get("path", "") for t in p.target_tree}
    after_pairs = [(t.get("path", ""), t.get("purpose", "")) for t in p.target_tree]
    for m in p.folder_mapping:
        to = m.get("to", "")
        if to and to not in target_paths:
            after_pairs.append((to, f"← 「{m.get('from', '')}」から移動"))
    # After はフォルダ名の昇順（自然順）で表示。関与者の多寡はプレフィックス番号で表現する。
    after = _tree_html(after_pairs, open_depth=2)

    # 構造比較ツリー上に描く移行矢印のデータ（表示 ON 時に JS が使う）。
    mig_json = json.dumps([
        {"from": m.get("from", ""), "to": m.get("to", ""),
         "action": m.get("action", ""), "reason": m.get("reason", "")}
        for m in p.folder_mapping
    ], ensure_ascii=False).replace("<", "\\u003c")  # </script> 混入を防ぐ

    findings_rows = "".join(
        f"<tr><td><span class='badge {f.severity}'>{_esc(_KIND_LABELS.get(f.kind, f.kind))}</span></td>"
        f"<td>{_esc(f.path or '(ルート)')}</td><td>{_esc(f.detail)}</td></tr>"
        for f in findings
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
<p class="muted">「移行の矢印を表示」をオンにすると、左（現状）の各フォルダから右（提案）の移行先へ矢印を描きます。線にマウスを重ねると移行の理由が表示されます。詳細は move_plan.csv も参照してください。</p>
<div class="treebar">
<button type="button" onclick="treeToggle('cmp',true)">すべて展開</button>
<button type="button" onclick="treeExpandMore('cmp')">1階層ずつ展開</button>
<button type="button" onclick="treeToggle('cmp',false)">すべて折りたたむ</button>
<label class="arrowtoggle"><input type="checkbox" id="arrowsOn" onchange="toggleArrows()">移行の矢印を表示</label>
</div>
<div class="miglegend" id="arrowlegend" hidden>
<span><i style="border-color:#3b6fd4"></i>移動</span>
<span><i style="border-color:#7a4fbf"></i>統合・集約</span>
<span><i style="border-color:#c26a1e"></i>アーカイブ・廃棄</span>
</div>
<div class="migwrap" id="cmp">
<svg class="miglines"></svg>
<div class="cols">
<div><h3>Before（現状）</h3>{before}</div>
<div><h3>After（提案）</h3>{after}</div>
</div>
</div>

<h2>命名規則</h2>{_ul(p.naming_rules)}
<h2>版管理（正式版／作業中の分離）</h2>{_ul(p.versioning_rules)}
<h2>オーナー制・作成統制</h2>{_ul(p.governance)}
<h2>ライフサイクル（保持・アーカイブ・廃棄）</h2>{_ul(p.lifecycle)}
<h2>すぐ着手できる改善</h2>{_ul(p.quick_wins)}
</div>
<script>window.MIG = {mig_json};</script>
<script>{_JS}</script>
</body></html>"""
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(doc)
