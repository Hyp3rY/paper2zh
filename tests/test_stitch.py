#!/usr/bin/env python3
"""Regression: stitch_continuations must not merge text across an equation."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import merge  # noqa: E402
import middle  # noqa: E402


def text_block(text: str):
    return SimpleNamespace(
        type="text",
        content=[SimpleNamespace(type="text", content=text)],
    )


class StitchEquationTest(unittest.TestCase):
    def test_text_equation_text_is_not_merged(self):
        first = text_block("First part, ")
        equation = SimpleNamespace(type="equation", content=[])
        last = text_block("second part.")
        model = SimpleNamespace(
            pages=[SimpleNamespace(page_idx=0, blocks=[first, equation, last])]
        )

        merged = merge.stitch_continuations(
            model,
            {"p0b0": "第一部分，", "p0b2": "第二部分。"},
            bilingual=False,
        )

        self.assertEqual(merged, 0)
        self.assertEqual(first.content[0].content, "First part, ")
        self.assertEqual(equation.content, [])
        self.assertEqual(last.content[0].content, "second part.")

    def test_text_text_without_equation_still_merges(self):
        first = text_block("A sentence that is cut ")
        last = text_block("in two.")
        model = SimpleNamespace(
            pages=[SimpleNamespace(page_idx=0, blocks=[first, last])]
        )

        merged = merge.stitch_continuations(
            model,
            {"p0b0": "一句话被切", "p0b1": "成两半。"},
            bilingual=False,
        )

        self.assertEqual(merged, 1)
        self.assertIn("一句话被切成两半", first.content[0].content)
        self.assertEqual(last.content, [])


if __name__ == "__main__":
    unittest.main()
