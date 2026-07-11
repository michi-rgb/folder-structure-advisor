"""動作確認用のサンプル散らかりフォルダを生成する。

重複コピー・旧版残存・無秩序な階層・雛形由来ファイルなど、課題で挙がった
状況を模したツリーを sample_data/ に作る。実務データは使わない。
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "sample_data"

# (相対パス, 内容) のリスト。内容が同じものは完全重複になる。
FILES = [
    # 見積書：同一内容が複数箇所に散在（完全重複）＋版違い
    ("営業/見積/見積書_v1.txt", "estimate body A"),
    ("営業/見積/見積書_v2.txt", "estimate body B"),
    ("営業/見積/見積書_最新版.txt", "estimate body C"),
    ("個人フォルダ/田中/見積書_v2 - コピー.txt", "estimate body B"),   # v2 の完全コピー
    ("メール添付/見積書_v2.txt", "estimate body B"),                    # v2 の完全コピー
    # 議事録：日付版が乱立
    ("会議/議事録/20240401_定例議事録.txt", "minutes 0401"),
    ("会議/議事録/20240415_定例議事録.txt", "minutes 0415"),
    ("会議/20240415_定例議事録.txt", "minutes 0415"),                    # 完全重複（別階層）
    ("会議/議事録/定例議事録_旧.txt", "minutes old"),
    # 提案書：雛形由来・作業中
    ("提案/提案書_ドラフト.txt", "proposal draft"),
    ("提案/提案書（作業中）.txt", "proposal draft"),                     # 近似重複
    ("提案/提案書_確定.txt", "proposal final"),
    # 無秩序に置かれた個人保管の重複
    ("デスクトップ整理/新しいフォルダ/提案書_確定.txt", "proposal final"),  # 完全重複
    # 画像・その他
    ("画像/logo.png", "PNGDATA"),
    ("画像/素材/logo.png", "PNGDATA"),                                   # 完全重複
    ("その他/メモ.txt", "memo"),
    ("その他/一時/メモ - コピー.txt", "memo"),                          # 完全重複
    # 契約：整然としている（据置になる想定）
    ("契約/2024/A社_契約書.pdf", "contract A"),
    ("契約/2024/B社_契約書.pdf", "contract B"),
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
