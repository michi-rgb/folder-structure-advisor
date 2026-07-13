"""動作確認用の「散らかった」サンプルフォルダを生成する。

python scripts/make_sample.py [出力先=sample_data]
"""
from __future__ import annotations

import os
import sys

FILES = {
    "共有/A社向け提案": [
        "提案書_v1.pptx", "提案書_v2.pptx", "提案書_最終.pptx", "提案書_最終_コピー.pptx",
        "見積書_20240401.xlsx", "見積書_20240415.xlsx", "議事録メモ.docx",
    ],
    "共有/A社向け提案/旧": ["提案書_old.pptx"],
    "共有/B案件": ["要件定義書_draft.docx", "要件定義書_確定.docx", "スケジュール.xlsx"],
    "共有/新しいフォルダ": ["無題.xlsx"],
    "共有/新しいフォルダ (2)": [],
    "共有/temp": ["作業中_集計.xlsx", "コピー ～ 集計.xlsx"],
    "個人/田中": ["A社_提案書_v3_最新.pptx", "経費精算_202403.xlsx"],
    "個人/佐藤/ダウンロード": ["B案件_要件定義書_コピー.docx"] + [f"データ{i:03d}.csv" for i in range(120)],
    "アーカイブ済/2019年度": ["年度報告_2019_final.pptx"],
    "共有/規程類/総務/人事/勤怠/2023/月次/確定": ["勤怠_202301_確定.xlsx"],
}


def main() -> None:
    root = sys.argv[1] if len(sys.argv) > 1 else "sample_data"
    for rel, names in FILES.items():
        d = os.path.join(root, *rel.split("/"))
        os.makedirs(d, exist_ok=True)
        for name in names:
            with open(os.path.join(d, name), "w", encoding="utf-8") as fp:
                fp.write(f"sample: {rel}/{name}\n" * 3)
    print(f"サンプルを生成しました: {root}")
    print(f"次を実行: python -m folder_advisor run --source {root} --out out --no-llm")


if __name__ == "__main__":
    main()
