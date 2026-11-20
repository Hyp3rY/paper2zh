#!/usr/bin/env python3
"""Regression: Markdown figure width and Word figure layout."""
from __future__ import annotations

import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import merge  # noqa: E402
import middle  # noqa: E402


def _image_block(left: float, right: float):
    return SimpleNamespace(
        bbox=[left, 0.2, right, 0.4],
        content=[SimpleNamespace(type="image_body", image_path="images/fig.jpg")],
    )


class MarkdownFigureWidthTest(unittest.TestCase):
    def test_half_column_expands_with_cap(self):
        html = middle.make_image_renderer()(_image_block(0.1, 0.5))
        self.assertIn('width="720"', html)
        self.assertIn("max-width:100%", html)

    def test_full_width_stays_proportional(self):
        html = middle.make_image_renderer()(_image_block(0.1, 0.9))
        self.assertIn('width="635"', html)
        self.assertNotIn("max-width:100%", html)


class WordFigureLayoutTest(unittest.TestCase):
    def test_consecutive_images_become_table(self):
        from docx import Document
        from docx.shared import Inches
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "x.png"
            Image.new("RGB", (120, 80), "white").save(image_path)
            doc = Document()
            for _ in range(2):
                doc.add_picture(str(image_path), width=Inches(1))
            target = Path(tmp) / "x.docx"
            doc.save(str(target))

            stats = merge.postprocess_docx_layout(target)

            self.assertEqual(stats["tables"], 1)
            result = Document(str(target))
            self.assertEqual(len(result.tables), 1)
            self.assertEqual((len(result.tables[0].rows), len(result.tables[0].columns)), (1, 2))


if __name__ == "__main__":
    unittest.main()
