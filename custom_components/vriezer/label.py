"""Draw a freezer label as a black and white PNG; any label printer can print that.

Layout (landscape): optional QR code on the left, then the package code (big),
a line, the name and the freeze date. Sizes come from the options, so a printer
with another label size only needs other numbers.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
FONT_FILE = HERE / "Roboto.ttf"


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONT_FILE), max(6, size))
    try:
        font.set_variation_by_name("Bold" if bold else "Regular")
    except (OSError, ValueError):
        pass
    return font


def _fit(draw, text: str, max_w: int, start: int, minimum: int, bold: bool = True):
    """Largest font size (from start down to minimum) where text fits max_w."""
    size = start
    while size > minimum:
        font = _font(size, bold)
        if draw.textlength(text, font=font) <= max_w:
            return font
        size -= 1
    return _font(minimum, bold)


def _center(draw, text: str, font, x0: int, x1: int, y_mid: int) -> None:
    """Draw text centered between x0 and x1 with its ink box centered on y_mid."""
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    x = x0 + (x1 - x0 - (right - left)) // 2 - left
    y = y_mid - (bottom - top) // 2 - top
    draw.text((x, y), text, font=font, fill=0)


def _qr(data: str):
    import segno  # imported here: only needed when QR codes are on

    return segno.make(data, error="l", micro=False)


def render(
    width: int,
    height: int,
    margin_long: int,
    margin_short: int,
    code: str,
    name: str,
    date_text: str,
    qr_data: str | None = None,
) -> Image.Image:
    """Return a width x height grayscale label (0 = black, 255 = white)."""
    img = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"  # crisp 1-bit glyphs

    x0, x1 = margin_long, width - margin_long
    top, bottom = margin_short, height - margin_short
    if qr_data:
        qr = _qr(qr_data)
        modules = qr.symbol_size(border=0)[0]
        # one module of quiet zone at top and bottom; may use part of the short margin
        scale = max(1, height // (modules + 2))
        size = modules * scale
        y = (height - size) // 2
        for row_i, row in enumerate(qr.matrix):
            for col_i, dark in enumerate(row):
                if dark:
                    draw.rectangle(
                        (
                            x0 + col_i * scale,
                            y + row_i * scale,
                            x0 + (col_i + 1) * scale - 1,
                            y + (row_i + 1) * scale - 1,
                        ),
                        fill=0,
                    )
        x0 += size + max(4, scale * 2)

    h = bottom - top
    text_w = x1 - x0
    big = int(h * 0.40)
    _center(
        draw, code, _fit(draw, code, text_w, big, big // 2), x0, x1, top + h * 20 // 100
    )
    line_y = top + h * 40 // 100
    draw.line((x0 + text_w // 12, line_y, x1 - text_w // 12, line_y), fill=0, width=2)
    title = name.strip()
    mid = int(h * 0.25)
    _center(
        draw,
        title,
        _fit(draw, title, text_w, mid, mid // 2),
        x0,
        x1,
        top + h * 58 // 100,
    )
    small = int(h * 0.25)
    _center(
        draw,
        date_text,
        _fit(draw, date_text, text_w, small, small // 2, bold=False),
        x0,
        x1,
        top + h * 86 // 100,
    )
    return img


def save(img: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.point(lambda v: 0 if v < 128 else 255).convert("1").save(path, "PNG")
