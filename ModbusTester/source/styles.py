"""
Light-mode stylesheet — soft, modern, professional.
"""

COLORS = {
    "bg":           "#f4f5f7",
    "surface":      "#ffffff",
    "surface_alt":  "#f0f1f4",
    "border":       "#d8dbe2",
    "border_focus": "#7b8fce",
    "text":         "#2c2e3a",
    "text_dim":     "#7a7e8f",
    "accent":       "#4f6bcc",
    "accent_hover": "#6580e0",
    "accent_press": "#3d55a8",
    "accent_light": "#e8ecf8",
    "green":        "#2ba060",
    "green_bg":     "#e6f5ed",
    "green_dim":    "#228a52",
    "red":          "#d04255",
    "red_bg":       "#fce8eb",
    "red_dim":      "#b53545",
    "orange":       "#d08a20",
    "yellow":       "#c5a820",
    "input_bg":     "#f8f9fb",
    "row_alt":      "#fafbfd",
    "header_bg":    "#f0f1f5",
    "log_bg":       "#fafbfd",
    "shadow":       "rgba(0,0,0,0.04)",
}

STYLESHEET = f"""

/* ── Global ─────────────────────────────────────────── */
QMainWindow, QWidget {{
    background-color: {COLORS['bg']};
    color: {COLORS['text']};
    font-family: "Segoe UI", "SF Pro Text", "Helvetica Neue", sans-serif;
    font-size: 13px;
}}

/* ── Group Boxes / Frames ───────────────────────────── */
QGroupBox {{
    background-color: {COLORS['surface']};
    border: 1px solid {COLORS['border']};
    border-radius: 10px;
    margin-top: 8px;
    padding: 14px 12px 10px 12px;
    font-weight: 600;
    font-size: 11px;
    color: {COLORS['text_dim']};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
    color: {COLORS['text_dim']};
}}

/* ── Inputs ─────────────────────────────────────────── */
QLineEdit, QSpinBox, QDoubleSpinBox {{
    background-color: {COLORS['input_bg']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    padding: 4px 8px;
    color: {COLORS['text']};
    selection-background-color: {COLORS['accent_light']};
    selection-color: {COLORS['accent']};
}}
QLineEdit:focus, QSpinBox:focus {{
    border-color: {COLORS['border_focus']};
    background-color: #ffffff;
}}
QLineEdit:disabled, QSpinBox:disabled {{
    color: {COLORS['text_dim']};
    background-color: {COLORS['surface_alt']};
}}

/* ── Combo Boxes ────────────────────────────────────── */
QComboBox {{
    background-color: {COLORS['input_bg']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    padding: 2px 8px;
    color: {COLORS['text']};
    min-width: 60px;
}}
QComboBox:focus {{
    border-color: {COLORS['border_focus']};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {COLORS['text_dim']};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {COLORS['surface']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    color: {COLORS['text']};
    selection-background-color: {COLORS['accent_light']};
    selection-color: {COLORS['accent']};
    outline: 0;
    padding: 4px;
}}

/* ── Buttons ────────────────────────────────────────── */
QPushButton {{
    background-color: {COLORS['surface']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    padding: 6px 14px;
    color: {COLORS['text']};
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {COLORS['surface_alt']};
    border-color: {COLORS['border_focus']};
}}
QPushButton:pressed {{
    background-color: {COLORS['accent_light']};
}}
QPushButton:disabled {{
    color: {COLORS['text_dim']};
    background-color: {COLORS['surface_alt']};
    border-color: {COLORS['border']};
}}

QPushButton[cssClass="accent"] {{
    background-color: {COLORS['accent']};
    border-color: {COLORS['accent']};
    color: #ffffff;
    font-weight: 600;
}}
QPushButton[cssClass="accent"]:hover {{
    background-color: {COLORS['accent_hover']};
    border-color: {COLORS['accent_hover']};
}}
QPushButton[cssClass="accent"]:pressed {{
    background-color: {COLORS['accent_press']};
}}

QPushButton[cssClass="connect"] {{
    background-color: {COLORS['green_bg']};
    border-color: {COLORS['green']};
    color: {COLORS['green_dim']};
    font-weight: 600;
}}
QPushButton[cssClass="connect"]:hover {{
    background-color: {COLORS['green']};
    color: #ffffff;
}}

QPushButton[cssClass="disconnect"] {{
    background-color: {COLORS['red_bg']};
    border-color: {COLORS['red']};
    color: {COLORS['red_dim']};
    font-weight: 600;
}}
QPushButton[cssClass="disconnect"]:hover {{
    background-color: {COLORS['red']};
    color: #ffffff;
}}

QPushButton[cssClass="row_write"] {{
    background-color: transparent;
    border: 1px solid {COLORS['border']};
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    color: {COLORS['accent']};
    font-weight: 600;
}}
QPushButton[cssClass="row_write"]:hover {{
    background-color: {COLORS['accent']};
    border-color: {COLORS['accent']};
    color: #ffffff;
}}

/* ── Table ──────────────────────────────────────────── */
QTableWidget {{
    background-color: {COLORS['surface']};
    alternate-background-color: {COLORS['row_alt']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    gridline-color: {COLORS['border']};
    selection-background-color: {COLORS['accent_light']};
    selection-color: {COLORS['text']};
    outline: 0;
}}
QTableWidget::item {{
    padding: 4px 6px;
    border: none;
}}
QHeaderView::section {{
    background-color: {COLORS['header_bg']};
    color: {COLORS['text_dim']};
    border: none;
    border-bottom: 2px solid {COLORS['accent']};
    padding: 7px 8px;
    font-weight: 600;
    font-size: 11px;
}}
QHeaderView::section:first {{
    border-top-left-radius: 8px;
}}
QHeaderView::section:last {{
    border-top-right-radius: 8px;
}}

/* Scrollbars */
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    border-radius: 4px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {COLORS['border']};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {COLORS['text_dim']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
    border-radius: 4px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {COLORS['border']};
    border-radius: 4px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {COLORS['text_dim']};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ── Labels ─────────────────────────────────────────── */
QLabel {{
    color: {COLORS['text']};
    background: transparent;
}}
QLabel[cssClass="dim"] {{
    color: {COLORS['text_dim']};
    font-size: 11px;
}}
QLabel[cssClass="status_connected"] {{
    color: {COLORS['green']};
    font-weight: 600;
}}
QLabel[cssClass="status_disconnected"] {{
    color: {COLORS['red']};
    font-weight: 600;
}}

/* ── Check Boxes ────────────────────────────────────── */
QCheckBox {{
    spacing: 6px;
    color: {COLORS['text']};
    background: transparent;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1.5px solid {COLORS['border']};
    background-color: {COLORS['surface']};
}}
QCheckBox::indicator:checked {{
    background-color: {COLORS['accent']};
    border-color: {COLORS['accent']};
}}
QCheckBox::indicator:hover {{
    border-color: {COLORS['border_focus']};
}}

/* ── Status Bar ─────────────────────────────────────── */
QStatusBar {{
    background-color: {COLORS['surface']};
    border-top: 1px solid {COLORS['border']};
    color: {COLORS['text_dim']};
    font-size: 12px;
    padding: 2px 8px;
}}

/* ── Tooltips ───────────────────────────────────────── */
QToolTip {{
    background-color: {COLORS['surface']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    padding: 5px 8px;
}}

/* ── Splitter ───────────────────────────────────────── */
QSplitter::handle {{
    background-color: {COLORS['border']};
    height: 2px;
    margin: 4px 40px;
    border-radius: 1px;
}}
QSplitter::handle:hover {{
    background-color: {COLORS['accent']};
}}

/* ── Dialog ─────────────────────────────────────────── */
QDialog {{
    background-color: {COLORS['bg']};
}}
"""
