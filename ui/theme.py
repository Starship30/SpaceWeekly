from __future__ import annotations

from dataclasses import dataclass
from string import Template

from PySide6.QtCore import QObject
from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtGui import QColor
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from resources import resource_path


THEME_SYSTEM = "system"
THEME_LIGHT = "light"
THEME_DARK = "dark"
THEME_MODES = {THEME_SYSTEM, THEME_LIGHT, THEME_DARK}


@dataclass(frozen=True)
class Theme:
    name: str
    colors: dict[str, str]


LIGHT_THEME = Theme(
    name=THEME_LIGHT,
    colors={
        "window_bg": "#f3f5fa",
        "sidebar_bg": "#e7edf8",
        "panel_bg": "#ffffff",
        "panel_alt_bg": "#f8fafc",
        "control_bg": "#ffffff",
        "control_hover_bg": "#f3f4f6",
        "control_pressed_bg": "#eaeef2",
        "border": "#e3e8f2",
        "border_strong": "#d6deeb",
        "text": "#17233c",
        "text_muted": "#65738b",
        "text_subtle": "#6e7781",
        "accent": "#4468e8",
        "accent_hover": "#3656c8",
        "primary": "#4468e8",
        "primary_hover": "#3656c8",
        "primary_border": "#4468e8",
        "on_primary": "#ffffff",
        "selection_text": "#ffffff",
        "input_shadow": "#eef2f6",
        "log_info": "#2563eb",
        "log_success": "#16833a",
        "log_warning": "#b45309",
        "log_error": "#dc2626",
    },
)

DARK_THEME = Theme(
    name=THEME_DARK,
    colors={
        "window_bg": "#121827",
        "sidebar_bg": "#0e1422",
        "panel_bg": "#1a2235",
        "panel_alt_bg": "#161e30",
        "control_bg": "#202a40",
        "control_hover_bg": "#343c46",
        "control_pressed_bg": "#3c4652",
        "border": "#3a434f",
        "border_strong": "#4a5563",
        "text": "#e6edf3",
        "text_muted": "#b6c2cf",
        "text_subtle": "#8d99a6",
        "accent": "#5b9dff",
        "accent_hover": "#75adff",
        "primary": "#7094ff",
        "primary_hover": "#8aabff",
        "primary_border": "#7094ff",
        "on_primary": "#10172a",
        "selection_text": "#0d1117",
        "input_shadow": "#1c2229",
        "log_info": "#79b8ff",
        "log_success": "#72d987",
        "log_warning": "#f4bf75",
        "log_error": "#ff8b8b",
    },
)


class ThemeManager(QObject):
    """Centralized application theme resolver and stylesheet applier."""

    theme_changed = Signal(object)

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self.app = app
        self.app.setStyle("Fusion")
        font = QFont()
        font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"])
        font.setPointSize(10)
        self.app.setFont(font)
        self.mode = THEME_SYSTEM
        self.theme = LIGHT_THEME
        self._connect_system_theme()

    def apply(self, mode: str) -> Theme:
        self.mode = mode if mode in THEME_MODES else THEME_SYSTEM
        self.theme = self._resolve_theme()
        self._apply_palette(self.theme)
        self.app.setStyleSheet(self._render_stylesheet(self.theme))
        self.theme_changed.emit(self.theme)
        return self.theme

    def log_stylesheet(self) -> str:
        colors = self.theme.colors
        return (
            f".info {{ color: {colors['log_info']}; }}"
            f".success {{ color: {colors['log_success']}; font-weight: 600; }}"
            f".warning {{ color: {colors['log_warning']}; font-weight: 600; }}"
            f".error, .critical {{ color: {colors['log_error']}; font-weight: 700; }}"
        )

    def _connect_system_theme(self) -> None:
        hints = self.app.styleHints()
        signal = getattr(hints, "colorSchemeChanged", None)

        if signal is not None:
            signal.connect(self._on_system_theme_changed)

    def _on_system_theme_changed(self, *_args) -> None:
        if self.mode == THEME_SYSTEM:
            self.apply(THEME_SYSTEM)

    def _resolve_theme(self) -> Theme:
        if self.mode == THEME_LIGHT:
            return LIGHT_THEME

        if self.mode == THEME_DARK:
            return DARK_THEME

        return DARK_THEME if self._system_prefers_dark() else LIGHT_THEME

    def _system_prefers_dark(self) -> bool:
        hints = self.app.styleHints()
        color_scheme = getattr(hints, "colorScheme", None)

        if color_scheme is not None:
            try:
                return str(color_scheme()).endswith("Dark")
            except TypeError:
                pass

        return self.app.palette().color(QPalette.ColorRole.Window).lightness() < 128

    def _render_stylesheet(self, theme: Theme) -> str:
        style_path = resource_path("ui", "style.qss")

        if not style_path.exists():
            return ""

        return Template(style_path.read_text(encoding="utf-8")).safe_substitute(
            **theme.colors,
            check_icon=resource_path("assets", "ui-check.svg").as_posix(),
            arrow_down=resource_path("assets", "ui-arrow-down.svg").as_posix(),
            arrow_up=resource_path("assets", "ui-arrow-up.svg").as_posix(),
        )

    def _apply_palette(self, theme: Theme) -> None:
        colors = theme.colors
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(colors["window_bg"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["text"]))
        palette.setColor(QPalette.ColorRole.Base, QColor(colors["control_bg"]))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors["panel_alt_bg"]))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(colors["panel_bg"]))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(colors["text"]))
        palette.setColor(QPalette.ColorRole.Text, QColor(colors["text"]))
        palette.setColor(QPalette.ColorRole.Button, QColor(colors["control_bg"]))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors["text"]))
        palette.setColor(QPalette.ColorRole.BrightText, QColor(colors["log_error"]))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(colors["accent"]))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(colors["selection_text"]))
        self.app.setPalette(palette)
