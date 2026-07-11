"""コアロジックの回帰テスト（標準ライブラリ unittest のみ）。

実行: python -m unittest discover -s tests
一時ディレクトリに散らかりフォルダを作って一連の処理を検証する。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from folder_advisor.classifier import classify_all
from folder_advisor.duplicates import bytes_reclaimable, detect_exact_duplicates
from folder_advisor.enrich import enrich
from folder_advisor.proposer import build_proposal
from folder_advisor.scanner import scan_source
from folder_advisor.versioning import detect_version_series, normalize_name


def _make_tree(root: Path) -> None:
    files = {
        "a/見積書_v1.txt": "AAA",
        "a/見積書_v2.txt": "BBB",
        "b/見積書_v2 - コピー.txt": "BBB",   # 完全重複
        "c/logo.png": "IMG",
        "c/img/logo.png": "IMG",             # 完全重複
        "d/契約書.pdf": "CONTRACT",
        "~$tmp.docx": "TEMP",                # 除外対象
        # 構成ファイル（各所に存在して当然 → 重複/旧版に出してはいけない）
        "a/.gitignore": "IGNORE_A",
        "b/.gitignore": "IGNORE_B",
        "a/__init__.py": "",                 # 空ファイル（0バイト）
        "b/__init__.py": "",                 # 同名・同内容だが構成ファイル
        "c/README.md": "READ_C",
        "d/README.md": "READ_D",
        # 汎用・自動生成名（版ではない）
        "e/スクリーンショット 2024-01-01.png": "S1",
        "e/スクリーンショット 2024-02-02.png": "S2",
    }
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


class CoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_tree(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_scan_excludes_temp(self) -> None:
        scan = scan_source(str(self.root))
        names = {f.name for f in scan.files}
        self.assertNotIn("~$tmp.docx", names)
        self.assertEqual(len(scan.files), 14)

    def test_reliability_marks_mtime_low(self) -> None:
        scan = enrich(scan_source(str(self.root)))
        f = scan.files[0]
        self.assertEqual(f.reliability["modified"], "low")
        self.assertEqual(f.reliability["hash"], "high")

    def test_exact_duplicates(self) -> None:
        scan = enrich(scan_source(str(self.root)))
        dups = detect_exact_duplicates(scan.files)
        # 見積書 BBB × 2、logo IMG × 2 の 2 グループ。
        self.assertEqual(len(dups), 2)
        self.assertGreater(bytes_reclaimable(dups), 0)
        for g in dups:
            self.assertEqual(len(g.redundant), len(g.paths) - 1)

    def test_normalize_name_strips_version(self) -> None:
        self.assertEqual(normalize_name("見積書_v2"), normalize_name("見積書_v1"))
        self.assertEqual(normalize_name("提案書（作業中）"), normalize_name("提案書_ドラフト").replace("ドラフト", "").strip() or "提案書")

    def test_version_series_detected(self) -> None:
        scan = enrich(scan_source(str(self.root)))
        series = detect_version_series(scan.files)
        bases = {s.base_name for s in series}
        self.assertIn("見積書", bases)

    def test_structural_files_not_duplicated(self) -> None:
        # .gitignore / __init__.py（空）は完全重複グループに現れない。
        scan = enrich(scan_source(str(self.root)))
        dups = detect_exact_duplicates(scan.files)
        dup_paths = {p for g in dups for p in g.paths}
        for p in dup_paths:
            self.assertFalse(
                p.endswith(".gitignore") or p.endswith("__init__.py"),
                f"構成ファイルが重複に混入: {p}",
            )

    def test_generic_and_samename_not_versioned(self) -> None:
        scan = enrich(scan_source(str(self.root)))
        series = detect_version_series(scan.files)
        bases = {s.base_name for s in series}
        # スクリーンショット（汎用名）/ README（同名多数・構成）は系列にならない。
        self.assertNotIn("スクリーンショット", bases)
        self.assertNotIn("readme", bases)
        # 見積書は本物の版なので系列に残る。
        self.assertIn("見積書", bases)

    def test_structural_kept_in_place(self) -> None:
        from folder_advisor.classifier import classify_all
        from folder_advisor.proposer import build_proposal

        scan = enrich(scan_source(str(self.root)))
        counts = classify_all(scan.files)
        dups = detect_exact_duplicates(scan.files)
        series = detect_version_series(scan.files)
        analysis = build_proposal(scan, dups, series, counts)
        for m in analysis.move_plan:
            if m.current_path.endswith(".gitignore"):
                self.assertEqual(m.action, "据置")
                self.assertEqual(m.current_path, m.proposed_path)

    def test_proposal_no_file_mutation(self) -> None:
        scan = enrich(scan_source(str(self.root)))
        before = {p.name for p in self.root.rglob("*") if p.is_file()}
        counts = classify_all(scan.files)
        dups = detect_exact_duplicates(scan.files)
        series = detect_version_series(scan.files)
        analysis = build_proposal(scan, dups, series, counts)
        after = {p.name for p in self.root.rglob("*") if p.is_file()}
        # 提案のみ：実ファイルは一切変わらない。
        self.assertEqual(before, after)
        # 全ファイルに移動計画がある。
        self.assertEqual(len(analysis.move_plan), len(scan.files))
        # 見積書は「見積・請求」に分類される。
        self.assertIn("見積・請求", analysis.category_counts)


if __name__ == "__main__":
    unittest.main()
