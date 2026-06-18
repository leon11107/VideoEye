"""Custom overlay-category icons for the toolbar chips.

Each glyph is painted programmatically (no asset files) so it can be tinted to
match the theme and recolored white when the chip is checked. Designed to be
distinct from Elecard's icons while staying self-explanatory:

  boundary  - four detached tiles (the gaps are the slice/tile boundaries)
  partition - a connected quad-tree (the top-left cell splits again: PU/TU)
  mode      - a diagonal arrow (motion vector / direction)
  type      - a 2x2 checkerboard (distinct block categories)
  bits      - little bars of differing height (per-block bit amount)
  qp        - a light-to-dark band scale (the quantization heat map)
"""
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import (QPixmap, QPainter, QPen, QColor, QBrush, QIcon,
                         QPolygonF)


def _draw_glyph(p: QPainter, key: str, s: float, col: QColor) -> None:
    pen = QPen(col, max(1.4, s * 0.09))
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    m = s * 0.12
    a, b = m, s - m
    w = b - a

    if key == "boundary":
        gap = w * 0.16
        tw = (w - gap) / 2
        for cx in (a, a + tw + gap):
            for cy in (a, a + tw + gap):
                p.drawRect(QRectF(cx, cy, tw, tw))

    elif key == "partition":
        p.drawRect(QRectF(a, a, w, w))
        p.drawLine(QPointF(a + w / 2, a), QPointF(a + w / 2, b))
        p.drawLine(QPointF(a, a + w / 2), QPointF(b, a + w / 2))
        q = w / 2
        p.drawLine(QPointF(a + q / 2, a), QPointF(a + q / 2, a + q))
        p.drawLine(QPointF(a, a + q / 2), QPointF(a + q, a + q / 2))

    elif key == "mode":
        p.drawLine(QPointF(a, b), QPointF(b, a))
        head = s * 0.30
        tip = QPointF(b, a)
        p.drawLine(tip, QPointF(b - head, a))
        p.drawLine(tip, QPointF(b, a + head))

    elif key == "types":
        q = w / 2
        p.drawRect(QRectF(a, a, w, w))
        p.drawLine(QPointF(a + q, a), QPointF(a + q, b))
        p.drawLine(QPointF(a, a + q), QPointF(b, a + q))
        p.fillRect(QRectF(a, a, q, q), col)
        p.fillRect(QRectF(a + q, a + q, q, q), col)

    elif key == "bits":
        p.setBrush(QBrush(col))
        bw = w / 5
        for i, hf in enumerate((0.45, 0.85, 0.6)):
            p.drawRect(QRectF(a + i * (bw * 1.6), b - w * hf, bw, w * hf))
        p.setBrush(Qt.BrushStyle.NoBrush)

    elif key == "qp":
        bands = 4
        bw = w / bands
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(bands):
            c = QColor(col)
            c.setAlpha(int(60 + 195 * i / (bands - 1)))
            p.fillRect(QRectF(a + i * bw, a, bw + 0.5, w), c)
        p.setPen(pen)
        p.drawRect(QRectF(a, a, w, w))


def _glyph_pixmap(key: str, px: int, col: QColor, dpr: float) -> QPixmap:
    pm = QPixmap(int(px * dpr), int(px * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    _draw_glyph(p, key, px, col)
    p.end()
    return pm


def overlay_icon(key: str, off_color, on_color, px: int = 18,
                 dpr: float = 2.0) -> QIcon:
    """QIcon for an overlay category: off_color when unchecked, on_color (e.g.
    white) when the chip is checked (drawn over the highlight pill)."""
    icon = QIcon()
    icon.addPixmap(_glyph_pixmap(key, px, QColor(off_color), dpr),
                   QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(_glyph_pixmap(key, px, QColor(on_color), dpr),
                   QIcon.Mode.Normal, QIcon.State.On)
    return icon
