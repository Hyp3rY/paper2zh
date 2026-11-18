#!/usr/bin/env python3
"""Generate `samples/one-page.pdf` -- a tiny self-owned English page used by doctor's warm-up.

It is generated locally (reportlab is already a MinerU dependency) instead of
shipping a real paper, so the bundle carries no third-party PDF content.

Usage:
    python build/make_sample.py [output]
"""

from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

TEXT = [
    ("Helvetica-Bold", 16, "Attention Improves Small Translation Models"),
    ("Helvetica-Oblique", 10, "A one-page warm-up sample generated for the paper2zh self-check"),
    ("Helvetica", 10, ""),
    ("Helvetica", 10,
     "Recurrent networks process tokens one step at a time. Given a hidden state h_t, the model "
     "computes the next state from the previous one. This sequential dependency limits parallelism "
     "during training and becomes painful for long sequences."),
    ("Helvetica", 10, ""),
    ("Helvetica", 10,
     "An attention layer instead compares every query with every key and returns a weighted sum of "
     "the values. The cost is quadratic in sequence length, but the computation is fully parallel."),
    ("Helvetica", 10, ""),
    ("Helvetica-Bold", 11, "1 Method"),
    ("Helvetica", 10, ""),
    ("Helvetica", 10,
     "We stack N = 6 identical layers and normalise each sub-layer. The output of a sub-layer is:"),
    ("Helvetica", 10, ""),
    ("Helvetica-Oblique", 11, "    LayerNorm(x + Sublayer(x))"),
    ("Helvetica", 10, ""),
    ("Helvetica", 10,
     "Table 1 reports the average translation quality on a small held-out set. All numbers are "
     "percentages and are rounded to one decimal place."),
    ("Helvetica", 10, ""),
]

ROWS = [
    ("Model", "BLEU", "Training hours"),
    ("Recurrent baseline", "26.1", "18.5"),
    ("Attention (this work)", "28.4", "3.5"),
]


def draw(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    y = height - 72
    for font, size, line in TEXT:
        pdf.setFont(font, size)
        if line:
            pdf.drawString(72, y, line)
        y -= size + 8

    table_y = y - 6
    for row_index, row in enumerate(ROWS):
        pdf.setFont("Helvetica-Bold" if row_index == 0 else "Helvetica", 9)
        for column_index, cell in enumerate(row):
            pdf.drawString(90 + column_index * 140, table_y - row_index * 16, cell)
    pdf.setFont("Helvetica", 8)
    pdf.drawString(90, table_y - len(ROWS) * 16 - 4, "Table 1: Quality and cost on the held-out set.")
    pdf.showPage()
    pdf.save()


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    target = Path(argv[1]).expanduser() if len(argv) > 1 else root / "samples" / "one-page.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    draw(target)
    print(f"[sample] wrote {target} ({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
