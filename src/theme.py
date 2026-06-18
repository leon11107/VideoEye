"""Centralized UI theming: dark (default) and light (Elecard-style) palettes.

Standard widgets are themed via a QPalette + a global stylesheet built from the
active Theme. Custom-painted widgets read colours from `current_theme()` in their
paintEvent, and widgets that set their own stylesheet expose `apply_theme()` so
the toggle can re-apply them. Overlay colours drawn on the decoded *video* frame
are intentionally NOT themed (they sit on image pixels, not UI chrome).
"""

from dataclasses import dataclass

from PyQt6.QtGui import QColor, QPalette

from .resources import resource_path

_CHECK_SVG = resource_path("src/assets/check.svg").replace("\\", "/")


@dataclass(frozen=True)
class Theme:
    name: str
    is_dark: bool

    # Palette roles
    window: QColor
    base: QColor
    text: QColor
    button: QColor
    highlight: QColor
    highlight_text: QColor
    disabled_text: QColor
    border: QColor

    # Chrome (QSS + custom paint)
    tooltip_bg: QColor
    menu_sel: QColor
    statusbar_bg: QColor
    statusbar_text: QColor
    scrollbar_bg: QColor
    scrollbar_handle: QColor
    scrollbar_handle_hover: QColor

    # Custom-painted UI chrome
    canvas_bg: QColor       # decoded image background (letterbox)
    chart_bg: QColor        # frame-size bar background
    chart_text: QColor      # legend / axis labels
    chart_text_dim: QColor  # secondary axis labels
    panel_bg: QColor        # info-label strips
    panel_fg: QColor
    ruler_bg: QColor
    ruler_line: QColor
    ruler_text: QColor
    ruler_corner: QColor    # origin marker (orange both)
    cursor_core: QColor     # frame-bar position cursor line
    cursor_halo: QColor     # halo keeping the line crisp on any background
    section_bg: QColor      # block-info section header
    section_fg: QColor
    muted: QColor           # placeholder / disabled text
    tree_hover: QColor

    def hx(self, c: QColor) -> str:
        return c.name()


DARK = Theme(
    name="dark", is_dark=True,
    window=QColor(45, 45, 45), base=QColor(30, 30, 30), text=QColor(212, 212, 212),
    button=QColor(45, 45, 45), highlight=QColor(42, 130, 218),
    highlight_text=QColor(255, 255, 255), disabled_text=QColor(127, 127, 127),
    border=QColor(60, 60, 60),
    tooltip_bg=QColor(45, 45, 45), menu_sel=QColor(9, 71, 113),
    statusbar_bg=QColor(0, 122, 204), statusbar_text=QColor(255, 255, 255),
    scrollbar_bg=QColor(30, 30, 30), scrollbar_handle=QColor(90, 90, 90),
    scrollbar_handle_hover=QColor(120, 120, 120),
    canvas_bg=QColor(26, 26, 26), chart_bg=QColor(30, 30, 30),
    chart_text=QColor(200, 200, 200), chart_text_dim=QColor(150, 150, 150),
    panel_bg=QColor(51, 51, 51), panel_fg=QColor(204, 204, 204),
    ruler_bg=QColor(46, 46, 46), ruler_line=QColor(120, 120, 120),
    ruler_text=QColor(210, 210, 210), ruler_corner=QColor(200, 90, 30),
    cursor_core=QColor(0, 0, 0), cursor_halo=QColor(235, 235, 235, 220),
    section_bg=QColor(45, 90, 136), section_fg=QColor(255, 255, 255),
    muted=QColor(136, 136, 136), tree_hover=QColor(42, 45, 46),
)

LIGHT = Theme(
    name="light", is_dark=False,
    window=QColor(240, 240, 240), base=QColor(255, 255, 255), text=QColor(30, 30, 30),
    button=QColor(232, 232, 232), highlight=QColor(42, 130, 218),
    highlight_text=QColor(255, 255, 255), disabled_text=QColor(160, 160, 160),
    border=QColor(192, 192, 192),
    tooltip_bg=QColor(255, 255, 240), menu_sel=QColor(205, 230, 255),
    statusbar_bg=QColor(0, 122, 204), statusbar_text=QColor(255, 255, 255),
    scrollbar_bg=QColor(224, 224, 224), scrollbar_handle=QColor(176, 176, 176),
    scrollbar_handle_hover=QColor(144, 144, 144),
    canvas_bg=QColor(224, 224, 224), chart_bg=QColor(255, 255, 255),
    chart_text=QColor(60, 60, 60), chart_text_dim=QColor(130, 130, 130),
    panel_bg=QColor(224, 224, 224), panel_fg=QColor(32, 32, 32),
    ruler_bg=QColor(228, 228, 228), ruler_line=QColor(128, 128, 128),
    ruler_text=QColor(48, 48, 48), ruler_corner=QColor(200, 90, 30),
    cursor_core=QColor(0, 0, 0), cursor_halo=QColor(255, 255, 255, 180),
    section_bg=QColor(45, 90, 136), section_fg=QColor(255, 255, 255),
    muted=QColor(136, 136, 136), tree_hover=QColor(225, 235, 245),
)

_THEMES = {"dark": DARK, "light": LIGHT}
_current = DARK


def current_theme() -> Theme:
    return _current


def set_current_theme(name: str) -> Theme:
    global _current
    _current = _THEMES.get(name, DARK)
    return _current


def build_palette(t: Theme) -> QPalette:
    p = QPalette()
    R = QPalette.ColorRole
    p.setColor(R.Window, t.window)
    p.setColor(R.WindowText, t.text)
    p.setColor(R.Base, t.base)
    p.setColor(R.AlternateBase, t.window)
    p.setColor(R.ToolTipBase, t.tooltip_bg)
    p.setColor(R.ToolTipText, t.text)
    p.setColor(R.Text, t.text)
    p.setColor(R.Button, t.button)
    p.setColor(R.ButtonText, t.text)
    p.setColor(R.BrightText, QColor(255, 255, 255) if t.is_dark else QColor(0, 0, 0))
    p.setColor(R.Link, t.highlight)
    p.setColor(R.Highlight, t.highlight)
    p.setColor(R.HighlightedText, t.highlight_text)
    G = QPalette.ColorGroup.Disabled
    for role in (R.WindowText, R.Text, R.ButtonText, R.HighlightedText):
        p.setColor(G, role, t.disabled_text)
    return p


def build_stylesheet(t: Theme) -> str:
    return f"""
        QToolTip {{
            background-color: {t.hx(t.tooltip_bg)};
            color: {t.hx(t.text)};
            border: 1px solid {t.hx(t.border)};
            padding: 4px;
        }}
        QMenuBar {{ background-color: {t.hx(t.window)};
                    border-bottom: 1px solid {t.hx(t.border)}; }}
        QMenuBar::item:selected {{ background-color: {t.hx(t.border)}; }}
        QMenu {{ background-color: {t.hx(t.window)};
                 border: 1px solid {t.hx(t.border)}; }}
        QMenu::item:selected {{ background-color: {t.hx(t.menu_sel)}; }}
        QToolBar {{ background-color: {t.hx(t.window)}; border: none;
                    spacing: 4px; padding: 4px; }}
        QToolBar::separator {{ background-color: {t.hx(t.border)};
                               width: 1px; margin: 4px 8px; }}
        QStatusBar {{ background-color: {t.hx(t.statusbar_bg)};
                      color: {t.hx(t.statusbar_text)}; }}
        QDockWidget::title {{ background-color: {t.hx(t.window)}; padding: 6px;
                              border-bottom: 1px solid {t.hx(t.border)}; }}
        QScrollBar:vertical {{ background-color: {t.hx(t.scrollbar_bg)};
                               width: 12px; margin: 0; }}
        QScrollBar::handle:vertical {{ background-color: {t.hx(t.scrollbar_handle)};
            min-height: 20px; border-radius: 4px; margin: 2px; }}
        QScrollBar::handle:vertical:hover {{
            background-color: {t.hx(t.scrollbar_handle_hover)}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar:horizontal {{ background-color: {t.hx(t.scrollbar_bg)};
                                 height: 12px; margin: 0; }}
        QScrollBar::handle:horizontal {{ background-color: {t.hx(t.scrollbar_handle)};
            min-width: 20px; border-radius: 4px; margin: 2px; }}
        QScrollBar::handle:horizontal:hover {{
            background-color: {t.hx(t.scrollbar_handle_hover)}; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
        QGroupBox {{ border: 1px solid {t.hx(t.border)}; border-radius: 4px;
                     margin-top: 8px; padding-top: 8px; }}
        QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left;
                            padding: 0 4px; color: {t.hx(t.text)}; }}
        QCheckBox::indicator, QGroupBox::indicator {{
            width: 14px; height: 14px;
            border: 1px solid {t.hx(t.border)};
            border-radius: 3px;
            background-color: {t.hx(t.base)};
        }}
        QCheckBox::indicator:hover, QGroupBox::indicator:hover {{
            border-color: {t.hx(t.highlight)};
        }}
        QCheckBox::indicator:checked, QGroupBox::indicator:checked {{
            background-color: {t.hx(t.highlight)};
            border-color: {t.hx(t.highlight)};
            image: url("{_CHECK_SVG}");
        }}
        QCheckBox::indicator:disabled {{ border-color: {t.hx(t.disabled_text)}; }}
        QToolButton#overlayChip {{
            border: none; background: transparent; color: {t.hx(t.text)};
            padding: 5px 10px 5px 13px; border-radius: 12px; font-size: 13px; }}
        QToolButton#overlayChip:hover {{ background: {t.hx(t.button)}; }}
        QToolButton#overlayChip:checked {{
            background: {t.hx(t.highlight)}; color: {t.hx(t.highlight_text)}; }}
        QToolButton#overlayChip::menu-button {{
            border: none; background: transparent; width: 16px; }}
        QMenu#overlayMenu {{
            background-color: {t.hx(t.base)}; border: none; padding: 5px 0; }}
        QMenu#overlayMenu::item {{ padding: 5px 18px 5px 8px; }}
        QMenu#overlayMenu::item:selected {{ background-color: {t.hx(t.menu_sel)}; }}
    """


def apply_theme_to_app(app, t: Theme = None) -> None:
    """Apply a theme (or the current one) to the whole application."""
    if t is None:
        t = _current
    app.setStyle("Fusion")
    app.setPalette(build_palette(t))
    app.setStyleSheet(build_stylesheet(t))
