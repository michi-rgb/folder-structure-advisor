"""動作確認用のサンプル散らかりフォルダを生成する。

重複コピー・旧版残存・無秩序な階層・雛形由来ファイルに加え、本ツールの主眼である
**同一プロジェクト（案件）が資料種別ごとに別フォルダへ散在している**状況を
重点的に模す。実務データは使わない。

散在の例（A社案件）:
  営業/見積/A社_見積書 … 見積は営業フォルダ
  契約/2024/A社_契約書 … 契約は契約フォルダ
  提案/A社_提案書       … 提案は提案フォルダ
  会議/A社_キックオフ議事録 … 議事録は会議フォルダ
  個人フォルダ/…/A社_… … 個人が手元にコピー
→ 種別で分けると A 社案件がバラバラになる。プロジェクト単位で束ねると 1 案件に集まる。
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "sample_data"

# (相対パス, 内容) のリスト。内容が同じものは完全重複になる。
# 内容末尾の "# dup" 等はコメントで、実際の内容は文字列全体。
FILES = [
    # === A社案件：資料種別ごとに別フォルダへ散在（プロジェクト集約の主役）===
    ("営業/見積/A社_見積書_v1.txt", "A-corp estimate v1"),
    ("営業/見積/A社_見積書_v2.txt", "A-corp estimate v2"),
    ("メール添付/A社_見積書_v2.txt", "A-corp estimate v2"),            # v2 の完全コピー
    ("契約/2024/A社_基本契約書.pdf", "A-corp contract"),
    ("提案/A社_提案書_確定.txt", "A-corp proposal final"),
    ("個人フォルダ/田中/A社_提案書_確定 - コピー.txt", "A-corp proposal final"),  # 完全コピー
    ("会議/A社_キックオフ議事録_20240310.txt", "A-corp kickoff minutes"),
    ("デスクトップ整理/A社_提案書_ドラフト.txt", "A-corp proposal draft"),  # 旧版（作業中）

    # === B社案件：同じく種別バラバラ・別フォルダ ===
    ("営業/見積/B社_見積書.txt", "B-corp estimate"),
    ("契約/2024/B社_基本契約書.pdf", "B-corp contract"),
    ("契約/B社_基本契約書_旧.pdf", "B-corp contract old"),            # 旧版
    ("提案/B社_提案書_確定.txt", "B-corp proposal final"),
    ("メール添付/B社_議事録_20240520.txt", "B-corp minutes 0520"),

    # === 新製品X 開発PJ：仕様・設計・スケジュールが散在 ===
    ("開発/仕様書_v1.txt", "product-X spec v1"),
    ("開発/仕様書_v2.txt", "product-X spec v2"),
    ("ダウンロード/仕様書_v1.txt", "product-X spec v1"),               # 完全コピー（別階層）
    ("開発/設計/基本設計書.txt", "product-X basic design"),
    ("開発/設計/詳細設計書.txt", "product-X detail design"),
    ("個人フォルダ/佐藤/新製品Xスケジュール.txt", "product-X schedule"),
    ("メール添付/新製品X_仕様書_v2 - コピー.txt", "product-X spec v2"),  # v2 の完全コピー

    # === 定例会議 議事録シリーズ（日付版の乱立＋別階層の重複）===
    ("会議/議事録/20240401_定例議事録.txt", "weekly minutes 0401"),
    ("会議/議事録/20240415_定例議事録.txt", "weekly minutes 0415"),
    ("会議/議事録/20240422_定例議事録.txt", "weekly minutes 0422"),
    ("会議/20240422_定例議事録.txt", "weekly minutes 0422"),            # 完全重複（別階層）
    ("会議/議事録/定例議事録_旧.txt", "weekly minutes old"),           # 旧版

    # === 画像・素材：完全重複＋自動生成名（版扱いしない）===
    ("画像/logo.png", "PNGDATA-LOGO"),
    ("画像/素材/logo.png", "PNGDATA-LOGO"),                            # 完全重複
    ("画像/素材/logo - コピー.png", "PNGDATA-LOGO"),                   # 完全重複
    ("画像/スクリーンショット 2024-05-01.png", "SHOT-1"),             # 汎用名（系列にしない）
    ("画像/スクリーンショット 2024-05-02.png", "SHOT-2"),

    # === 構成ファイル（各フォルダに存在して当然・据置になるべき）===
    ("開発/.gitignore", "node_modules/\n__pycache__/\n"),
    ("開発/README.md", "product-X readme"),
    ("提案/README.md", "proposal readme"),
    ("画像/Thumbs.db", "THUMBS-CACHE"),
    ("画像/素材/desktop.ini", "[.ShellClassInfo]\n"),
    ("開発/__init__.py", ""),                                          # 0 バイト空ファイル

    # === その他ノイズ：無秩序フォルダ・空ファイル ===
    ("その他/メモ.txt", "memo"),
    ("その他/一時/メモ - コピー.txt", "memo"),                        # 完全重複
    ("デスクトップ整理/新しいフォルダ/無題.txt", ""),                # 0 バイト・汎用名
    ("デスクトップ整理/新しいフォルダ (2)/無題.txt", ""),            # 0 バイト・汎用名

    # === 整然としている（据置想定）===
    ("契約/2024/雛形_契約書テンプレート.pdf", "contract template"),
]


def main() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    for rel, content in FILES:
        p = ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    print(f"サンプル生成: {ROOT}  ({len(FILES)} ファイル)")


if __name__ == "__main__":
    main()
