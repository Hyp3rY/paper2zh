#!/usr/bin/env python3
"""Generate the raster figures used by the synthetic demo paper.

Everything is drawn with Pillow so the PDF embeds real bitmaps: MinerU can
extract them as image blocks, which is the point of this layout test.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
W, H = 900, 520
INK = (28, 32, 38)
MUTED = (95, 103, 112)
GRID = (218, 223, 229)


def font(size: int, bold: bool = False):
    candidates = [
        r"C:\Windows\Fonts\timesbd.ttf" if bold else r"C:\Windows\Fonts\times.ttf",
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (W, H), "white")
    return image, ImageDraw.Draw(image)


def arrow(draw: ImageDraw.ImageDraw, start, end, color=INK, width=2):
    draw.line([start, end], fill=color, width=width)
    x1, y1 = start
    x2, y2 = end
    angle = math.atan2(y2 - y1, x2 - x1)
    size = 10
    left = (x2 - size * math.cos(angle - 0.45), y2 - size * math.sin(angle - 0.45))
    right = (x2 - size * math.cos(angle + 0.45), y2 - size * math.sin(angle + 0.45))
    draw.polygon([end, left, right], fill=color)


def figure_pipeline():
    image, draw = canvas()
    draw.text((30, 22), "(a)  Layout-preserving processing pipeline", fill=INK, font=font(22, True))

    boxes = [
        (45, 95, 205, 165, "PDF\ninput"),
        (250, 95, 430, 165, "Layout\nparser"),
        (475, 95, 650, 165, "Text units\n+ formulas"),
        (695, 95, 855, 165, "Translator\n(model)"),
    ]
    for x1, y1, x2, y2, label in boxes:
        draw.rounded_rectangle([x1, y1, x2, y2], radius=8, outline=INK, width=2, fill=(246, 248, 250))
        lines = label.split("\n")
        for index, line in enumerate(lines):
            draw.text(((x1 + x2) / 2, (y1 + y2) / 2 + (index - 0.5) * 22),
                      line, fill=INK, font=font(18, True), anchor="mm")
    for left, right in zip(boxes, boxes[1:]):
        arrow(draw, (left[2] + 6, (left[1] + left[3]) / 2), (right[0] - 6, (left[1] + left[3]) / 2))

    draw.rounded_rectangle([475, 220, 855, 320], radius=8, outline=INK, width=2, fill=(250, 250, 250))
    draw.text((665, 245), "Renderer + QA gates", fill=INK, font=font(19, True), anchor="mm")
    for label, x in [("formula count", 555), ("image paths", 665), ("table cells", 775)]:
        draw.text((x, 285), label, fill=MUTED, font=font(15), anchor="mm")
    arrow(draw, (775, 170), (775, 214))
    arrow(draw, (665, 320), (665, 380))
    draw.text((665, 400), "Chinese Markdown", fill=INK, font=font(18, True), anchor="mm")

    draw.text((45, 390), "Deterministic", fill=MUTED, font=font(17))
    draw.text((45, 418), "scripts", fill=MUTED, font=font(17))
    draw.text((45, 455), "LLM only sees text", fill=MUTED, font=font(17))
    image.save(OUT / "fig1_pipeline.png")


def figure_lines():
    image, draw = canvas()
    left, top, right, bottom = 90, 80, 840, 430

    for i in range(6):
        y = top + i * (bottom - top) / 5
        draw.line([(left, y), (right, y)], fill=GRID, width=1)
        draw.text((left - 12, y), f"{1.0 - i * 0.2:.1f}", fill=MUTED, font=font(14), anchor="rm")
    for i in range(7):
        x = left + i * (right - left) / 6
        draw.line([(x, top), (x, bottom)], fill=GRID, width=1)
        draw.text((x, bottom + 12), str(20 + i * 10), fill=MUTED, font=font(14), anchor="mt")

    draw.line([(left, top), (left, bottom)], fill=INK, width=2)
    draw.line([(left, bottom), (right, bottom)], fill=INK, width=2)
    draw.text(((left + right) / 2, bottom + 45), "Flight speed (m/s)", fill=INK, font=font(18), anchor="mm")
    draw.text((22, (top + bottom) / 2), "Normalized coefficient", fill=INK, font=font(18), anchor="lm")

    def curve(fn, dash=False, color=(30, 80, 150)):
        points = []
        for i in range(240):
            t = i / 239
            x = left + t * (right - left)
            y = bottom - (0.12 + 0.72 * fn(t)) * (bottom - top)
            points.append((x, y))
        if dash:
            for start in range(0, len(points) - 1, 12):
                draw.line(points[start:start + 7], fill=color, width=3)
        else:
            draw.line(points, fill=color, width=3)

    curve(lambda t: 0.55 + 0.25 * math.sin(t * 6.0) * math.exp(-0.4 * t))
    curve(lambda t: 0.32 + 0.40 * t ** 1.7, dash=True, color=(180, 90, 30))
    draw.line([(600, 110), (640, 110)], fill=(30, 80, 150), width=3)
    draw.text((650, 110), "Rotor–propeller", fill=INK, font=font(16), anchor="lm")
    draw.line([(600, 138), (640, 138)], fill=(180, 90, 30), width=3)
    draw.text((650, 138), "Isolated rotor", fill=INK, font=font(16), anchor="lm")

    draw.text((30, 22), "(b)  Thrust coefficient versus flight speed", fill=INK, font=font(22, True))
    image.save(OUT / "fig2_lines.png")


def figure_bars():
    image, draw = canvas()
    left, top, right, bottom = 95, 90, 835, 420
    draw.line([(left, top), (left, bottom)], fill=INK, width=2)
    draw.line([(left, bottom), (right, bottom)], fill=INK, width=2)
    for i in range(6):
        y = bottom - i * (bottom - top) / 5
        draw.line([(left, y), (right, y)], fill=GRID, width=1)
        draw.text((left - 10, y), f"{i * 0.1:.1f}", fill=MUTED, font=font(14), anchor="rm")

    values_a = [0.31, 0.42, 0.38, 0.51, 0.46]
    values_b = [0.28, 0.36, 0.34, 0.44, 0.40]
    labels = ["30", "60", "90", "110", "130"]
    group_width = (right - left) / len(labels)
    bar_width = group_width * 0.28
    for index, label in enumerate(labels):
        center = left + group_width * (index + 0.5)
        for offset, value, color in [(-bar_width / 1.8, values_a[index], (30, 80, 150)),
                                     (bar_width / 1.8, values_b[index], (190, 120, 50))]:
            x = center + offset
            y = bottom - value * (bottom - top) / 0.55
            draw.rectangle([x - bar_width / 2, y, x + bar_width / 2, bottom], fill=color, outline=color)
        draw.text((center, bottom + 14), label, fill=MUTED, font=font(14), anchor="mt")
    draw.text(((left + right) / 2, bottom + 45), "Flight speed (m/s)", fill=INK, font=font(18), anchor="mm")
    draw.text((24, (top + bottom) / 2), "Maximum C_T", fill=INK, font=font(18), anchor="lm")
    draw.rectangle([600, 110, 622, 126], fill=(30, 80, 150))
    draw.text((632, 118), "Installed", fill=INK, font=font(16), anchor="lm")
    draw.rectangle([600, 142, 622, 158], fill=(190, 120, 50))
    draw.text((632, 150), "Isolated", fill=INK, font=font(16), anchor="lm")
    draw.text((30, 22), "(c)  Maximum thrust coefficient by condition", fill=INK, font=font(22, True))
    image.save(OUT / "fig3_bars.png")


def figure_heatmap():
    image, draw = canvas()
    draw.text((30, 22), "(d)  Lateral asymmetry map", fill=INK, font=font(22, True))
    random.seed(7)
    x0, y0, cell = 100, 85, 62
    cols, rows = 8, 5
    for row in range(rows):
        for col in range(cols):
            value = 0.18 + 0.62 * random.random()
            r = int(250 - 170 * value)
            g = int(245 - 100 * value)
            b = int(240 - 20 * value)
            draw.rectangle([x0 + col * cell, y0 + row * cell,
                           x0 + (col + 1) * cell, y0 + (row + 1) * cell],
                           fill=(r, g, b), outline=(255, 255, 255), width=2)
    for i in range(cols + 1):
        draw.line([(x0 + i * cell, y0), (x0 + i * cell, y0 + rows * cell)], fill=(255, 255, 255), width=2)
    for i in range(rows + 1):
        draw.line([(x0, y0 + i * cell), (x0 + cols * cell, y0 + i * cell)], fill=(255, 255, 255), width=2)
    draw.rectangle([x0, y0, x0 + cols * cell, y0 + rows * cell], outline=INK, width=2)
    for i in range(cols):
        draw.text((x0 + (i + 0.5) * cell, y0 + rows * cell + 12), str(30 + i * 10),
                  fill=MUTED, font=font(13), anchor="mt")
    for j in range(rows):
        draw.text((x0 - 12, y0 + (j + 0.5) * cell), chr(65 + j),
                  fill=MUTED, font=font(14), anchor="rm")
    draw.text((x0 + cols * cell / 2, y0 + rows * cell + 40), "Azimuth sector",
              fill=INK, font=font(17), anchor="mm")
    draw.text((30, y0 + rows * cell / 2), "Panel", fill=INK, font=font(17), anchor="lm")

    # colour bar
    bar_x, bar_y, bar_w, bar_h = 700, 100, 22, 250
    for i in range(bar_h):
        value = 1 - i / (bar_h - 1)
        r = int(250 - 170 * value)
        g = int(245 - 100 * value)
        b = int(240 - 20 * value)
        draw.line([(bar_x, bar_y + i), (bar_x + bar_w, bar_y + i)], fill=(r, g, b))
    draw.rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], outline=INK, width=1)
    draw.text((bar_x + bar_w + 10, bar_y - 4), "high", fill=MUTED, font=font(13), anchor="lb")
    draw.text((bar_x + bar_w + 10, bar_y + bar_h + 4), "low", fill=MUTED, font=font(13), anchor="lt")
    image.save(OUT / "fig4_heatmap.png")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    figure_pipeline()
    figure_lines()
    figure_bars()
    figure_heatmap()
    print("figures written to", OUT)
