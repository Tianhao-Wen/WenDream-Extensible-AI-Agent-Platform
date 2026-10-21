"""生成 Benchmark 的确定性多模态 fixtures。

要求：重复执行生成完全一致的内容（固定尺寸、固定坐标、固定字号，不使用随机数）。

运行：
    python benchmark/fixtures/generate_multimodal_fixtures.py
产出：
    benchmark/fixtures/images/vision_001_shapes.png
    benchmark/fixtures/images/vision_002_flow.png
    benchmark/fixtures/images/vision_003_table.png
    benchmark/fixtures/pdf/vision_004_project.pdf
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent
IMAGES_DIR = BASE / "images"
PDF_DIR = BASE / "pdf"

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
BLUE = (40, 90, 200)
GREY = (120, 120, 120)

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _font(size: int):
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def _center_text(draw: ImageDraw.ImageDraw, box, text: str, font) -> None:
    left, top, right, bottom = box
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    draw.text(
        (left + (right - left - w) / 2 - bbox[0], top + (bottom - top - h) / 2 - bbox[1]),
        text,
        fill=BLACK,
        font=font,
    )


def make_shapes() -> Path:
    """3 个圆 + 2 个正方形，固定坐标，不重叠，无任何文字。"""
    img = Image.new("RGB", (640, 400), WHITE)
    draw = ImageDraw.Draw(img)

    # 三个圆（半径 50，固定位置）
    for cx, cy in [(100, 120), (300, 120), (500, 120)]:
        draw.ellipse([cx - 50, cy - 50, cx + 50, cy + 50], fill=BLUE, outline=BLACK, width=3)

    # 两个正方形（边长 100，固定位置）
    for x, y in [(150, 250), (390, 250)]:
        draw.rectangle([x, y, x + 100, y + 100], fill=GREY, outline=BLACK, width=3)

    out = IMAGES_DIR / "vision_001_shapes.png"
    img.save(out, format="PNG")
    return out


def make_flow() -> Path:
    """单行 Input -> Agent -> Tool -> Result，带清晰箭头。"""
    img = Image.new("RGB", (1000, 260), WHITE)
    draw = ImageDraw.Draw(img)
    font = _font(34)

    labels = ["Input", "Agent", "Tool", "Result"]
    box_w, box_h, gap = 180, 90, 70
    y = 85
    boxes = []
    x = 40
    for label in labels:
        box = (x, y, x + box_w, y + box_h)
        boxes.append(box)
        draw.rectangle(box, fill=WHITE, outline=BLACK, width=3)
        _center_text(draw, box, label, font)
        x += box_w + gap

    for i in range(len(boxes) - 1):
        start = (boxes[i][2] + 8, y + box_h // 2)
        end = (boxes[i + 1][0] - 8, y + box_h // 2)
        draw.line([start, end], fill=BLACK, width=4)
        arrow = [(end[0], end[1]), (end[0] - 16, end[1] - 9), (end[0] - 16, end[1] + 9)]
        draw.polygon(arrow, fill=BLACK)

    out = IMAGES_DIR / "vision_002_flow.png"
    img.save(out, format="PNG")
    return out


def make_table() -> Path:
    """固定表格：Model | Score / A 82 / B 91 / C 87。"""
    img = Image.new("RGB", (560, 380), WHITE)
    draw = ImageDraw.Draw(img)
    font = _font(32)

    rows = [("Model", "Score"), ("A", "82"), ("B", "91"), ("C", "87")]
    col_x = [60, 340]
    col_w = [260, 160]
    row_h = 70
    top = 45

    # 竖线
    draw.line([(col_x[0], top), (col_x[0], top + row_h * len(rows))], fill=BLACK, width=3)
    draw.line([(col_x[1], top), (col_x[1], top + row_h * len(rows))], fill=BLACK, width=3)
    right = col_x[1] + col_w[1]
    draw.line([(right, top), (right, top + row_h * len(rows))], fill=BLACK, width=3)

    for i, (a, b) in enumerate(rows):
        y0 = top + i * row_h
        draw.line([(col_x[0], y0), (right, y0)], fill=BLACK, width=3)
        weight = 5 if i == 0 else 2
        _center_text(draw, (col_x[0], y0, col_x[1], y0 + row_h), a, font)
        _center_text(draw, (col_x[1], y0, right, y0 + row_h), b, font)
        if i == 0:
            draw.line([(col_x[0], y0), (right, y0)], fill=BLACK, width=weight)
    draw.line([(col_x[0], top + row_h * len(rows)), (right, top + row_h * len(rows))], fill=BLACK, width=3)

    out = IMAGES_DIR / "vision_003_table.png"
    img.save(out, format="PNG")
    return out


def make_pdf() -> Path:
    """单页 PDF，包含三行可读文本。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as pdf_canvas

    out = PDF_DIR / "vision_004_project.pdf"
    c = pdf_canvas.Canvas(str(out), pagesize=A4)
    c.setFont("Helvetica", 22)
    c.drawString(72, 720, "Project: WenDream")
    c.drawString(72, 680, "Benchmark Tasks: 30")
    c.drawString(72, 640, "Primary Model: Qwen")
    c.showPage()
    c.save()
    return out


def main() -> None:
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    created = [make_shapes(), make_flow(), make_table(), make_pdf()]
    for path in created:
        print(f"{path.relative_to(BASE.parent.parent)}  ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
