#!/usr/bin/env python3
"""Regression: finish/export rename the two markdown outputs."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import merge  # noqa: E402


class RenameOutputsTest(unittest.TestCase):
    def test_renames_both_files_and_readme(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            (workdir / "zh.md").write_text("中文正文", encoding="utf-8")
            (workdir / "bilingual.md").write_text("English\n\n中文", encoding="utf-8")
            (workdir / "先读我.txt").write_text(
                "打开 zh.md；对照看 bilingual.md。", encoding="utf-8"
            )
            (workdir / "zh.docx").write_bytes(b"docx")
            (workdir / "bilingual.docx").write_bytes(b"docx")

            renamed = merge.rename_outputs(workdir)

            self.assertEqual(renamed, ["中文版.md", "中英对照.md", "中文版.docx", "中英对照.docx"])
            self.assertTrue((workdir / "中文版.md").is_file())
            self.assertTrue((workdir / "中英对照.md").is_file())
            self.assertFalse((workdir / "zh.md").exists())
            self.assertFalse((workdir / "bilingual.md").exists())
            self.assertTrue((workdir / "中文版.docx").is_file())
            self.assertTrue((workdir / "中英对照.docx").is_file())
            self.assertFalse((workdir / "zh.docx").exists())
            self.assertFalse((workdir / "bilingual.docx").exists())
            text = (workdir / "先读我.txt").read_text(encoding="utf-8")
            self.assertIn("中文版.md", text)
            self.assertIn("中英对照.md", text)
            self.assertNotIn("zh.md", text)
            self.assertNotIn("bilingual.md", text)

    def test_rerun_overwrites_existing_chinese_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            (workdir / "中文版.md").write_text("旧", encoding="utf-8")
            (workdir / "zh.md").write_text("新", encoding="utf-8")
            merge.rename_outputs(workdir)
            self.assertEqual((workdir / "中文版.md").read_text(encoding="utf-8"), "新")


if __name__ == "__main__":
    unittest.main()
