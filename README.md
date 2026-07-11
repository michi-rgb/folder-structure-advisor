# folder_advisor — フォルダ構成 改善提案アプリ

散在・重複・旧版で無秩序になったフォルダを走査し、**内容の重複を最小化した
改善後フォルダ構成を提案**する CLI ツールです。実ファイルは一切変更せず
（提案・可視化のみ）、before/after を **mermaid 図**とグラフ JSON で可視化した
**HTML レポート**と、**移動計画 CSV** を出力します。

> 背景・課題整理は [`docs/設計計画.md`](docs/設計計画.md) を参照。

## 特長（課題との対応）

- **探索の困難（症状A）**：完全重複（内容ハッシュ）・近似重複・旧版系列を洗い出し、
  資料種別に再分類した提案ツリーを生成。
- **信頼性の判断不能（症状B）**：更新日・作成者は「開閉で変わる／雛形の残存」で
  実態を反映しないため**低信頼**として扱い、重複判定は当てにできる
  **内容ハッシュ（SHA-256）** を一次情報にします。
- **対象の切り替え**：Windows ローカルと SharePoint（OneDrive 同期フォルダ）を
  `--mode` で切り替え。SharePoint は同期済みローカルパスとして走査します。
- **LLM補助（任意・Azure OpenAI）**：ファイル名・フォルダ名の**リストのみ**を送って
  日本語の意味分類・命名規約案を補助。接続情報が無ければ**ルールベース単体で完全動作**。

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
- `--llm` Azure OpenAI 補助を有効化（`report` / `run`。接続情報が必要）

## LLM補助（Azure OpenAI）の設定

接続情報（別部署が管理）を受領したら環境変数を設定して `--llm` を付けます。
未設定・失敗時は自動でルールベースにフォールバックします。

```powershell
$env:AZURE_OPENAI_ENDPOINT    = "https://<resource>.openai.azure.com/"
$env:AZURE_OPENAI_API_KEY     = "<api-key>"
$env:AZURE_OPENAI_DEPLOYMENT  = "<deployment-name>"   # モデル名ではなくデプロイ名
$env:AZURE_OPENAI_API_VERSION = "2024-10-21"
python -m folder_advisor run --source sample_data --out out --llm
```

詳細は [`.env.example`](.env.example) を参照。

## 出力物（`out/`）

| ファイル | 内容 |
|---|---|
| `report.html`   | サマリ・before/after 図・重複表・旧版表・分類・命名規約案・移動計画 |
| `move_plan.csv` | 全ファイルの `現在パス→提案パス→分類→アクション→根拠`（Excel 対応 BOM 付き） |
| `before.mmd` / `after.mmd` | 改善前後の mermaid 図ソース（[mermaid.live](https://mermaid.live) 等に貼付可） |
| `graph.json`    | before/after の nodes/edges グラフデータ |
| `scan.json` / `analysis.json` | 走査生データ・分析結果（再利用・監査用） |

> HTML の図はブラウザで mermaid.js（CDN）を読み込んで描画します。社内ネットワークで
> CDN が使えない場合は、各図の「mermaid ソース」折り畳み、または `*.mmd` を
> [mermaid.live](https://mermaid.live) に貼り付けて確認してください。

## 移動計画のアクション区分

- **移動**：資料種別に沿った位置へ移す提案
- **統合**：内容が完全一致する冗長コピー。「正」1 本へ集約
- **要確認**：旧版の可能性（系列の最新以外）。アーカイブへ隔離を提案
- **据置**：既に適切な位置

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
  classifier.py  資料種別のルールベース分類
  llm.py         Azure OpenAI 補助（任意・フォールバック付き）
  proposer.py    改善後ツリー・移動計画・命名規約案
  visualize.py   before/after mermaid・グラフ JSON
  report.py      HTML レポート・CSV 出力
  cli.py         コマンドライン
```
```
