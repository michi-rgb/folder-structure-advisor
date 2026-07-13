"""コアロジックのテスト（標準ライブラリ unittest のみ・ネットワーク不要）。

python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from folder_advisor.analyzer import analyze
from folder_advisor.digest import build_digest, select_folders
from folder_advisor.models import (
    FolderStat,
    Proposal,
    ScanResult,
    is_generic_dir_name,
    name_signals,
    series_key,
)
from folder_advisor.propose import make_proposal, _from_llm_json
from folder_advisor.report import write_report
from folder_advisor.scan_local import scan_local
from folder_advisor.scan_onedrive import _build_result, _item_dir_path


def _make_tree(root: str, spec: dict[str, list[str]]) -> None:
    for rel, names in spec.items():
        d = os.path.join(root, *rel.split("/")) if rel else root
        os.makedirs(d, exist_ok=True)
        for name in names:
            with open(os.path.join(d, name), "w") as fp:
                fp.write("x")


class TestNameSignals(unittest.TestCase):
    def test_series_key_strips_versions(self):
        self.assertEqual(series_key("提案書_v1.pptx"), series_key("提案書_v2.pptx"))
        self.assertEqual(series_key("提案書_最終.pptx"), series_key("提案書_v1.pptx"))
        self.assertEqual(series_key("見積書_20240401.xlsx"), series_key("見積書_20240415.xlsx"))
        self.assertNotEqual(series_key("提案書.pptx"), series_key("見積書.pptx"))

    def test_name_signals(self):
        self.assertEqual(name_signals("報告_v2.docx"), (True, False, False))
        self.assertEqual(name_signals("報告_作業中.docx"), (False, True, False))
        self.assertEqual(name_signals("報告_確定.docx"), (False, False, True))

    def test_generic_dir(self):
        self.assertTrue(is_generic_dir_name("新しいフォルダ (2)"))
        self.assertTrue(is_generic_dir_name("temp"))
        self.assertFalse(is_generic_dir_name("A社向け提案"))


class TestLocalScan(unittest.TestCase):
    def test_scan_metadata_only(self):
        with tempfile.TemporaryDirectory() as root:
            _make_tree(root, {
                "": [],
                "共有/A案件": ["提案書_v1.pptx", "提案書_v2.pptx", "提案書_v3.pptx"],
                "共有/.git": ["config"],  # 除外対象
                "個人": ["メモ.txt"],
            })
            scan = scan_local(root)
            paths = {f.path for f in scan.folders}
            self.assertIn("共有/A案件", paths)
            self.assertNotIn("共有/.git", paths)
            a = next(f for f in scan.folders if f.path == "共有/A案件")
            self.assertEqual(a.n_files, 3)
            self.assertEqual(a.max_series, 3)
            self.assertEqual(a.exts, {"pptx": 3})
            self.assertTrue(a.last_modified)

    def test_scan_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as root:
            _make_tree(root, {"a/b": ["x.txt"]})
            scan = scan_local(root)
            p = os.path.join(root, "scan.json")
            scan.save(p)
            loaded = ScanResult.load(p)
            self.assertEqual(loaded.total_files, scan.total_files)
            self.assertEqual([f.path for f in loaded.folders], [f.path for f in scan.folders])

    def test_max_folders_truncates(self):
        with tempfile.TemporaryDirectory() as root:
            _make_tree(root, {f"d{i}": [] for i in range(10)})
            scan = scan_local(root, max_folders=5)
            self.assertTrue(scan.truncated)
            self.assertEqual(len(scan.folders), 5)


class TestAnalyzer(unittest.TestCase):
    def _scan(self, **kw) -> ScanResult:
        return ScanResult(folders=[FolderStat(**kw)])

    def test_version_chaos(self):
        s = self._scan(path="a", depth=1, n_files=4, max_series=4)
        kinds = {f.kind for f in analyze(s)}
        self.assertIn("version_chaos", kinds)

    def test_deep_and_flat(self):
        s = ScanResult(folders=[
            FolderStat(path="a/b/c/d/e/f", depth=6),
            FolderStat(path="dl", depth=1, n_files=150),
        ])
        kinds = {f.kind for f in analyze(s)}
        self.assertIn("deep_nesting", kinds)
        self.assertIn("flat_overload", kinds)

    def test_duplicate_folder_name(self):
        s = ScanResult(folders=[
            FolderStat(path="x/議事録", depth=2, n_files=1),
            FolderStat(path="y/議事録", depth=2, n_files=1),
        ])
        kinds = {f.kind for f in analyze(s)}
        self.assertIn("duplicate_folder_name", kinds)


class TestDigest(unittest.TestCase):
    def test_budget_keeps_ancestors(self):
        folders = [FolderStat(path="", depth=0)]
        for i in range(50):
            folders.append(FolderStat(path=f"top{i}", depth=1, n_files=1))
            folders.append(FolderStat(path=f"top{i}/deep/leaf", depth=3, n_files=100 - i))
            folders.append(FolderStat(path=f"top{i}/deep", depth=2))
        scan = ScanResult(folders=sorted(folders, key=lambda f: f.path))
        selected, omitted = select_folders(scan, max_folders=60)
        paths = {f.path for f in selected}
        for f in selected:
            if "/" in f.path:
                self.assertIn(f.path.rsplit("/", 1)[0], paths)  # 祖先が含まれる
        self.assertTrue(sum(omitted.values()) > 0)

    def test_digest_text(self):
        scan = ScanResult(folders=[
            FolderStat(path="共有/A案件", depth=2, n_files=3, size=1500,
                       exts={"pptx": 3}, samples=["提案書_v1.pptx"], max_series=3),
        ])
        text = build_digest(scan)
        self.assertIn("共有/A案件", text)
        self.assertIn("版乱立x3", text)
        self.assertIn("提案書_v1.pptx", text)


class TestPropose(unittest.TestCase):
    def test_fallback_without_llm(self):
        scan = ScanResult(source="/x", folders=[
            FolderStat(path="共有", depth=1, n_files=2, max_series=3),
        ])
        proposal, findings = make_proposal(scan, use_llm=False)
        self.assertEqual(proposal.generated_by, "rules")
        self.assertTrue(proposal.principles)
        self.assertTrue(proposal.target_tree)
        self.assertTrue(any(f.kind == "version_chaos" for f in findings))

    def test_llm_json_parsing_is_lenient(self):
        raw = {
            "principles": ["p1", None],
            "target_tree": [{"path": "10_x", "purpose": "y"}, "junk"],
            "folder_mapping": "not-a-list",
            "naming_rules": ["n1"],
        }
        p = _from_llm_json(raw)
        self.assertEqual(p.principles, ["p1"])
        self.assertEqual(p.target_tree[0]["path"], "10_x")
        self.assertEqual(p.target_tree[0]["owner_role"], "")
        self.assertEqual(p.folder_mapping, [])
        self.assertEqual(p.generated_by, "llm")

    def test_llm_failure_falls_back(self):
        scan = ScanResult(folders=[FolderStat(path="a", depth=1, n_files=1)])
        with mock.patch("folder_advisor.llm.chat_json", side_effect=RuntimeError("boom")):
            proposal, _ = make_proposal(scan, use_llm=True)
        self.assertEqual(proposal.generated_by, "rules")


class TestOneDriveBuild(unittest.TestCase):
    def test_item_dir_path(self):
        self.assertEqual(_item_dir_path({"parentReference": {"path": "/drive/root:"}}), "")
        self.assertEqual(_item_dir_path({"parentReference": {"path": "/drives/x/root:/A/B"}}), "A/B")
        self.assertIsNone(_item_dir_path({"parentReference": {}}))

    def test_build_result_with_subpath(self):
        items = {
            "1": {"name": "仕事", "dir": "", "is_folder": True, "size": 0, "mtime": ""},
            "2": {"name": "A案件", "dir": "仕事", "is_folder": True, "size": 0, "mtime": ""},
            "3": {"name": "提案_v1.pptx", "dir": "仕事/A案件", "is_folder": False, "size": 10, "mtime": "2026-01"},
            "4": {"name": "提案_v2.pptx", "dir": "仕事/A案件", "is_folder": False, "size": 12, "mtime": "2026-02"},
            "5": {"name": "無関係.txt", "dir": "私物", "is_folder": False, "size": 5, "mtime": "2026-01"},
        }
        result = _build_result(items, "仕事", max_folders=100)
        paths = {f.path for f in result.folders}
        self.assertEqual(paths, {"", "A案件"})
        a = next(f for f in result.folders if f.path == "A案件")
        self.assertEqual(a.n_files, 2)
        self.assertEqual(a.max_series, 2)
        self.assertEqual(a.last_modified, "2026-02")


class TestReport(unittest.TestCase):
    def test_write_report_outputs(self):
        scan = ScanResult(source="/x", scanned_at="2026-07-13",
                          folders=[FolderStat(path="a", depth=1, n_files=1, samples=["f.txt"])])
        proposal = Proposal(
            principles=["p"], naming_rules=["n"],
            target_tree=[{"path": "10_プロジェクト", "purpose": "案件", "owner_role": "責任者"}],
            folder_mapping=[{"from": "a", "to": "10_プロジェクト/a", "action": "移動", "reason": "r"}],
        )
        with tempfile.TemporaryDirectory() as out:
            paths = write_report(out, scan, analyze(scan), proposal)
            html_text = Path(paths["html"]).read_text(encoding="utf-8")
            self.assertIn("10_プロジェクト", html_text)
            self.assertNotIn("http://", html_text)  # 外部リソース非依存
            self.assertNotIn("https://", html_text)
            csv_text = Path(paths["csv"]).read_text(encoding="utf-8-sig")
            self.assertIn("10_プロジェクト/a", csv_text)
            md_text = Path(paths["rules"]).read_text(encoding="utf-8")
            self.assertIn("命名規則", md_text)


if __name__ == "__main__":
    unittest.main()
