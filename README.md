# folder_advisor — フォルダ構成 改善提案アプリ

散在・重複・旧版で無秩序になったフォルダを走査し、**既存の構造をできるだけ壊さずに
整頓する改善案を提案**する CLI ツールです。第一階層を資料種別で作り直すような大改造は
せず、**既定はすべて据置**。散在は**プロジェクト/案件単位**（ファイル名の類似性＋既存
フォルダ文脈を LLM が判断）で最小限だけ集約します。実ファイルは一切変更せず
（提案・可視化のみ）、before/after を **折り畳みツリー**と **Treemap（容量ヒート
マップ）** で可視化した **HTML レポート**（外部 CDN 不要・オフライン表示可）と、
**移動計画 CSV** を出力します。

> 背景・課題整理は [`docs/設計計画.md`](docs/設計計画.md) を参照。

## 特長（課題との対応）

- **探索の困難（症状A）**：完全重複（内容ハッシュ）・近似重複・旧版系列を洗い出す。
  **既存フォルダ構造を土台に据置を既定**とし、**一時/個人置き場に散在した**同一案件の
  ファイルだけを、その案件の既存フォルダ（ホーム）へ引き上げる。共有フォルダの正規
  ファイルやサブ構造は動かさない。**資料種別では束ねない**（異なる案件が種別で混ざる
  のを避けるため）。
- **信頼性の判断不能（症状B）**：更新日・作成者は「開閉で変わる／雛形の残存」で
  実態を反映しないため**低信頼**として扱い、重複判定は当てにできる
  **内容ハッシュ（SHA-256）** を一次情報にします。
- **対象の切り替え**：Windows ローカルと SharePoint（OneDrive 同期フォルダ）を
  `--mode` で切り替え。SharePoint は同期済みローカルパスとして走査します。
- **プロジェクト束ねは LLM が主エンジン（Azure OpenAI / Mistral 切替）**：
  ファイルの**相対パス（現在のフォルダ位置）とファイル名のリストのみ**を送り、
  「どのファイルが同じ案件か」を LLM に判断させる（ルールでは案件判定が難しいため）。
  接続情報が無い場合は集約を行わず、**据置中心（重複統合・旧版隔離のみ）で完全動作**。
- **ノイズ除外**：`.gitignore` / `__init__.py` / `README` 等の**構成ファイル**や
  **0バイトの空ファイル**、`スクリーンショット`等の**自動生成名**は、重複・旧版の
  誤検出を避けるため統合候補・旧版系列から除外し、移動提案でも据え置きます。

## 動作環境

- Python 3.10 以降（標準ライブラリのみで本体は動作。追加依存なし）
- LLM補助を使う場合のみ `pip install -r requirements.txt`（`openai`）

## 使い方

```powershell
# 1) まず動作確認用サンプルを生成（任意）
python scripts/make_sample.py

# 2) 一括実行：走査 → 分析 → レポート生成
python -m folder_advisor run --source sample_data --out out

# 実フォルダを対象にする例（ローカル）
python -m folder_advisor run --source "D:\共有ドライブ\部門フォルダ" --out out

# SharePoint（OneDrive 同期済みフォルダ）を対象にする例
python -m folder_advisor run --mode sharepoint --source "C:\Users\<you>\<会社名>\<サイト名> - Documents" --out out
```

### サブコマンド

| コマンド | 用途 |
|---|---|
| `scan`   | 走査＋ハッシュ＋信頼度付与のみ（`out/scan.json`） |
| `report` | `--scan out/scan.json` か `--source` を入力に分析・提案・レポート生成 |
| `run`    | 走査→分析→レポートを一括実行 |

### 主なオプション

- `--source PATH` 対象フォルダ
- `--mode local|sharepoint` 対象種別（既定 local）
- `--out DIR` 出力先（既定 `out`）
- `--max-files N` 走査上限（大規模フォルダの試走用）
- `--no-hash` 内容ハッシュ計算を省略（高速だが完全重複検出は無効）
- `--llm` LLM補助を有効化（`report` / `run`。接続情報が必要）
- `--llm-provider auto|azure|mistral` LLMプロバイダ選択（既定 `auto` = 認証情報が揃っている方）

## LLM補助（Azure OpenAI / Mistral）の設定

接続情報を受領したら環境変数を設定して `--llm` を付けます。プロバイダは
`--llm-provider` で選べます（既定 `auto`）。未設定・失敗時は自動でルールベースに
フォールバックします。

**Azure OpenAI:**
```powershell
$env:AZURE_OPENAI_ENDPOINT    = "https://<resource>.openai.azure.com/"
$env:AZURE_OPENAI_API_KEY     = "<api-key>"
$env:AZURE_OPENAI_DEPLOYMENT  = "<deployment-name>"   # モデル名ではなくデプロイ名
$env:AZURE_OPENAI_API_VERSION = "2024-10-21"
python -m folder_advisor run --source sample_data --out out --llm --llm-provider azure
```

**Mistral:**
```powershell
$env:MISTRAL_API_KEY = "<api-key>"
$env:MISTRAL_MODEL   = "mistral-large-latest"   # 省略可
python -m folder_advisor run --source sample_data --out out --llm --llm-provider mistral
```

依存パッケージ（使う方のみ）: `pip install openai`（Azure）/ `pip install mistralai`（Mistral）。
詳細は [`.env.example`](.env.example) を参照。

## 出力物（`out/`）

| ファイル | 内容 |
|---|---|
| `report.html`   | 整理健全度スコア・before/after の折り畳みツリー / Treemap・重複表・旧版表・分類・移動計画 |
| `move_plan.csv` | 全ファイルの `現在パス→提案パス→分類→アクション→根拠`（Excel 対応 BOM 付き） |
| `graph.json`    | before/after の nodes/edges グラフデータ |
| `scan.json` / `analysis.json` | 走査生データ・分析結果（再利用・監査用） |

> **before/after の構造可視化**は、大規模フォルダで巨大化して判読できなくなる
> mermaid 図を廃止し、レポート内蔵の JS（**外部 CDN 不要**）で描画する 2 種類に
> 置き換えました。**折り畳みツリー**でフォルダを開閉しながら構造を辿れ、
> **Treemap（容量ヒートマップ）** では面積＝容量・色が濃いほど大容量で、どこが
> 容量を食っているか／整理でどう変わるかを俯瞰できます（フォルダをクリックで
> 掘り下げ可能）。社内ネットワークでもオフラインで表示できます。

## 移動計画のアクション区分

- **据置**：現状維持（構造変更なし）。**既定はこれ**で、大半のファイルが該当する
- **移動**：**一時/個人置き場**（メール添付・個人フォルダ・デスクトップ・ダウンロード・
  一時 等）に散在した案件ファイルだけを、その案件の既存の集約先（ホーム）へ引き上げる
  提案（LLM 補助時のみ発生）。共有フォルダの正規ファイルやサブフォルダは動かさず、
  一時/個人置き場そのものは集約先にしない（＝最小変更を徹底）
- **統合**：内容が完全一致する冗長コピー。「正」1 本へ集約
- **要確認**：旧版の可能性（系列の最新以外）。プロジェクトのホーム（無ければ現在地）
  配下の `_アーカイブ(旧版)` へローカル隔離を提案

## 設計上の重要な前提

- **実ファイルは変更しません**（第一版は提案・可視化のみ）。移動は CSV の提案に留めています。
- 更新日・作成者は信頼できないため、版の新旧は**ファイル名中の版番号・日付**を優先します。
- SharePoint は Graph API 直結ではなく**同期済みローカルパス**を前提とします
  （`scanner.SourceBackend` を実装すれば将来 Graph API を追加可能）。

## モジュール構成

```
folder_advisor/
  scanner.py     走査（LocalBackend / SourceBackend 抽象）
  enrich.py      内容ハッシュ・メタデータ信頼度
  duplicates.py  完全重複・近似重複
  versioning.py  旧版系列・名前正規化
  classifier.py  資料種別のルールベース分類（レポートの補助メタ情報）
  filters.py     構成ファイル/汎用名の判定（重複・旧版の誤検出を除外）
  llm.py         LLM補助（プロジェクト束ねが主・Azure OpenAI / Mistral・フォールバック付き）
  clustering.py  プロジェクト束ね＋集約先（ホーム）決定＋一時/個人置き場の限定集約
  proposer.py    据置既定・散在集約の移動計画・改善後ツリー
  scoring.py     整理健全度スコア（改善前後・100点満点）の算出
  visualize.py   before/after の折り畳みツリー・Treemap 用データ・グラフ JSON
  report.py      HTML レポート・CSV 出力
  cli.py         コマンドライン
```
```
