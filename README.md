# folder_advisor — フォルダ体系を取得し、LLM で改善提案するアプリ

職場の「必要な資料に短時間でアクセスできない・どれが正/最新か分からない」という
課題を解決するため、**フォルダ体系（構造メタデータのみ）を取得**し、
**Azure OpenAI（Azure CLI 認証）** に分析させて、フォルダ体系・命名規則・版管理・
オーナー制・ライフサイクルまで含む**改善提案レポート**を生成する CLI ツールです。

旧版（ファイル単位の重複統合ツール）をゼロから作り直した v2 です。
背景の課題整理と設計は [`docs/設計.md`](docs/設計.md) を参照してください。

## 特長

| 課題（原因） | 本ツールの出力 |
|---|---|
| 格納・命名・版管理のルールがない | 格納先の原則／標準フォルダ体系案／命名規則／版管理ルール（`運用ルール案.md`） |
| 正式版と作業中が混在し正が不明 | 正式版/作業中の分離構成の提案 ＋ 混在フォルダの検出 |
| 「正」のオーナーが未定義 | 体系案の各フォルダにオーナー役割を提案 |
| 廃棄/アーカイブ運用の不在 | ライフサイクルルール ＋ 長期未更新フォルダの棚卸しリスト |
| フォルダ乱立 | 汎用名・同名散在・空フォルダの検出、作成統制ルールの提案 |

- **実ファイルには一切触れません**。読み取り（メタデータのみ）→ 提案レポート出力だけです。
- LLM が使えない状況でも、ルールベースの所見＋テンプレート提案で**完全動作**します。

## 通信量の削減（設計上の柱）

1. **ファイル内容を読まない・送らない。** 収集するのはフォルダ構造の統計
   （件数・容量・拡張子・代表ファイル名few件）だけ。
2. **OneDrive 同期フォルダはローカルと同じ経路で走査。** stat 情報しか見ないため、
   「ファイルオンデマンド」のクラウド専用ファイルを**ダウンロードさせません**
   （通信ゼロ）。レポートにクラウド専用ファイル数を表示して確認できます。
3. **Graph API 直結モードは delta クエリ。** 初回はメタデータのみ
   （1 ファイルあたり数百バイト）、**2 回目以降は変更差分だけ**を取得します
   （`out/onedrive_cache.json` に deltaLink とメタデータを保存）。
4. **LLM へは圧縮ダイジェストを 1 回だけ送信。** フォルダ単位 1 行サマリに圧縮し、
   既定で最大 400 フォルダ（`--max-digest-folders` で調整）。ファイル一覧は送りません。

## 動作環境

- Python 3.10 以降。スキャン・分析・レポートは**標準ライブラリのみ**で動作
- LLM 提案を使う場合: `pip install -r requirements.txt`（openai / azure-identity）と Azure CLI（`az`）

## 使い方

```powershell
# 0) 動作確認用サンプル（任意）
python scripts/make_sample.py
python -m folder_advisor run --source sample_data --out out --no-llm

# 1) ローカルフォルダ
python -m folder_advisor run --source "D:\共有ドライブ\部門フォルダ" --out out

# 2) OneDrive 同期フォルダ（推奨。通信ゼロ）
python -m folder_advisor run --source "C:\Users\<you>\OneDrive - <会社名>" --out out

# 3) OneDrive を Graph API 直結でスキャン（同期していない場合。az login が必要）
python -m folder_advisor run --source "onedrive:/ドキュメント/仕事" --out out
#    SharePoint ドキュメントライブラリは --drive-id <driveId> で指定
```

出力（`out/`）:

| ファイル | 内容 |
|---|---|
| `report.html` | サマリ・課題所見・改善後体系（before/after 折り畳みツリー）・移行計画・各種ルール。オフライン閲覧可 |
| `運用ルール案.md` | そのまま職場に展開できるルール文書ドラフト |
| `move_plan.csv` | フォルダ移行計画（Excel 対応 BOM 付き） |
| `scan.json` | 走査結果（フォルダ単位の統計のみ。再利用可） |

### サブコマンドと主なオプション

```
scan     --source PATH|onedrive:[/subpath] [--drive-id ID] [--exclude PAT]... [--max-folders N] --out DIR
propose  --scan out/scan.json [--no-llm] [--goal "追加要望"] [--max-digest-folders N] --out DIR
run      scan + propose を一括実行
```

`--goal` で LLM への追加要望を自由文で渡せます（例: `--goal "第一階層は部署別を維持したい"`）。

## Azure OpenAI の設定（Azure CLI 認証・API キー不要）

```powershell
az login                                    # 必要なら --tenant <tenant-id>
$env:AZURE_OPENAI_ENDPOINT   = "https://<resource>.openai.azure.com/"
$env:AZURE_OPENAI_DEPLOYMENT = "<deployment-name>"   # モデル名ではなくデプロイ名
python -m folder_advisor run --source "..." --out out
```

- 対象リソースで自分のアカウントに **Cognitive Services OpenAI User** ロールが必要です。
- トークンは azure-identity（`AzureCliCredential`）が自動取得・自動更新します。
- 認証やロールが未整備の間は `--no-llm` でルールベース提案のみ利用できます。
- 詳細は [`.env.example`](.env.example) を参照。

## LLM に送る情報（社外秘への配慮）

フォルダパス・件数・容量・拡張子内訳と、各フォルダの**代表ファイル名最大 4 件**のみです。
ファイル内容・全ファイル一覧・作成者名は送信しません。代表ファイル名も出したくない
場合は `digest.py` の `例[...]` 出力を無効化してください。

## モジュール構成

```
folder_advisor/
  models.py         データモデル・ファイル名シグナル（版/作業中/確定の判定）
  scan_local.py     ローカル/OneDrive同期フォルダ走査（メタデータのみ・非ハイドレート）
  scan_onedrive.py  Microsoft Graph delta 走査（差分キャッシュ・$select 最小化）
  analyzer.py       ルールベース課題所見（版乱立・混在・散在・平置き・深層・未更新）
  digest.py         LLM 向け圧縮ダイジェスト（フォルダ数上限・省略注記）
  prompts.py        システム/ユーザープロンプト（課題定義を内蔵）
  llm.py            Azure OpenAI クライアント（AzureCliCredential）
  propose.py        提案生成（LLM 主・失敗時ルールベースにフォールバック）
  report.py         HTML レポート・運用ルール案.md・move_plan.csv
  cli.py            scan / propose / run
```

## テスト

```bash
python -m unittest discover -s tests -v
```
