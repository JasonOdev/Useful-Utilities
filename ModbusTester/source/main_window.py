"""
Main window for the Modbus Tester application.
Includes a collapsible transaction log panel for debugging.
"""
import logging
import time
from datetime import datetime
from functools import partial
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QLineEdit, QSpinBox, QPushButton, QComboBox,
    QTableWidget, QTableWidgetItem, QCheckBox, QHeaderView,
    QFileDialog, QDialog, QMessageBox, QStatusBar, QSizePolicy,
    QAbstractItemView, QTextEdit, QSplitter, QApplication, QTabWidget,
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal, QObject
from PySide6.QtGui import QFont, QColor, QTextCursor

from modbus_client import (
    ModbusTcpBackend, compute_address_str, is_writable,
    REG_TYPES, DATA_TYPES, REG_COUNTS, BYTE_ORDER_LABELS,
    _decode_registers,
)
from app_config import (
    AppConfig, ConnectionConfig, RegisterEntry,
    save_config, load_config, AUTO_SAVE_PATH,
)
from styles import COLORS
from discovery_tab import DiscoveryTab

log = logging.getLogger("modbus_tester")


# ── Column indices ──────────────────────────────────────────────
COL_ENABLE   = 0
COL_LABEL    = 1
COL_REGTYPE  = 2
COL_OFFSET   = 3
COL_DATATYPE = 4
COL_ADDRESS  = 5
COL_VALUE    = 6
COL_STATUS   = 7
COL_WRITE    = 8
COL_WRITEBTN = 9
NUM_COLS     = 10

COLUMN_HEADERS = [
    "", "Label", "Register Type", "Offset", "Data Type",
    "Address", "Value", "", "Write Value", "",
]


# ── Qt log handler — bridges Python logging → GUI ──────────────
class _QtLogHandler(logging.Handler, QObject):
    """Logging handler that emits a Qt signal for each record
    so the log panel can update safely from any thread."""
    log_signal = Signal(str, str)   # (formatted_message, level_name)

    def __init__(self):
        logging.Handler.__init__(self)
        QObject.__init__(self)
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        try:
            msg = self.format(record)
            self.log_signal.emit(msg, record.levelname)
        except Exception:
            pass


# ── Worker for threaded read/write ──────────────────────────────
class _ReadWorker(QObject):
    """Reads registers in a background thread.
    Automatically coalesces contiguous registers of the same type
    into a single Modbus request for efficiency."""
    row_result = Signal(int, object, object, str)  # row, value, raw_regs, error
    finished = Signal(float)

    def __init__(self, backend, rows_data, byte_order):
        super().__init__()
        self.backend = backend
        self.rows_data = rows_data     # [(row_idx, reg_type, offset, data_type), ...]
        self.byte_order = byte_order

    def run(self):
        import logging
        _log = logging.getLogger("modbus_tester")
        t0 = time.perf_counter()

        # Group by register type
        from collections import defaultdict
        groups = defaultdict(list)
        for row_idx, reg_type, offset, data_type in self.rows_data:
            groups[reg_type].append((row_idx, offset, data_type))

        for reg_type, items in groups.items():
            items.sort(key=lambda x: x[1])  # sort by offset

            # Build contiguous ranges
            ranges = []
            r_start = items[0][1]
            r_end = r_start + REG_COUNTS.get(items[0][2], 1)
            r_items = [items[0]]

            for item in items[1:]:
                _, offset, data_type = item
                reg_count = REG_COUNTS.get(data_type, 1)
                if offset == r_end:
                    r_end = offset + reg_count
                    r_items.append(item)
                else:
                    ranges.append((r_start, r_end - r_start, list(r_items)))
                    r_start = offset
                    r_end = offset + reg_count
                    r_items = [item]
            ranges.append((r_start, r_end - r_start, list(r_items)))

            n_coalesced = sum(1 for _, _, ri in ranges if len(ri) > 1)
            if n_coalesced:
                total_items = sum(len(ri) for _, _, ri in ranges)
                _log.info(
                    f"COALESCE  {total_items} {reg_type} rows → "
                    f"{len(ranges)} request(s)"
                )

            # Execute each range
            for start, count, items_in_range in ranges:
                try:
                    raw = self.backend.read_bulk(reg_type, start, count)
                    for row_idx, offset, data_type in items_in_range:
                        rc = REG_COUNTS.get(data_type, 1)
                        pos = offset - start
                        if reg_type in ("coil", "discrete"):
                            val = bool(raw[pos])
                            raw_regs = [int(raw[pos])]
                        else:
                            raw_regs = list(raw[pos:pos + rc])
                            val = _decode_registers(raw_regs, data_type,
                                                    self.byte_order)
                        self.row_result.emit(row_idx, val, raw_regs, "")
                except Exception as exc:
                    for row_idx, _, _ in items_in_range:
                        self.row_result.emit(row_idx, None, None, str(exc))

        elapsed = (time.perf_counter() - t0) * 1000
        self.finished.emit(elapsed)


class _WriteWorker(QObject):
    row_result = Signal(int, bool, str)
    finished = Signal()

    def __init__(self, backend, rows_data, byte_order):
        super().__init__()
        self.backend = backend
        self.rows_data = rows_data
        self.byte_order = byte_order

    def run(self):
        for row_idx, reg_type, offset, data_type, value in self.rows_data:
            try:
                self.backend.write_item(reg_type, offset, data_type,
                                        self.byte_order, value)
                self.row_result.emit(row_idx, True, "")
            except Exception as exc:
                self.row_result.emit(row_idx, False, str(exc))
        self.finished.emit()


# ════════════════════════════════════════════════════════════════
class MainWindow(QMainWindow):
    MAX_ROWS = 50

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Modbus Tester")
        self.setMinimumSize(1080, 640)
        self.resize(1200, 780)

        self.backend = ModbusTcpBackend()
        self._cyclic_running = False
        self._read_thread: QThread | None = None
        self._write_thread: QThread | None = None
        self._error_count = 0
        self._last_scan_ms = 0.0
        self._log_max_lines = 5000
        self._show_binary = False
        self._raw_values = {}       # row → list[int] raw register values
        self._decoded_values = {}   # row → decoded Python value

        # Central widget — tabbed layout
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(
            f"QTabWidget::pane {{ border: none; }}"
            f"QTabBar::tab {{"
            f"  background: {COLORS['surface_alt']};"
            f"  border: 1px solid {COLORS['border']};"
            f"  border-bottom: none;"
            f"  border-radius: 6px 6px 0 0;"
            f"  padding: 8px 24px;"
            f"  margin-right: 2px;"
            f"  font-weight: 500;"
            f"  color: {COLORS['text_dim']};"
            f"}}"
            f"QTabBar::tab:selected {{"
            f"  background: {COLORS['bg']};"
            f"  color: {COLORS['accent']};"
            f"  font-weight: 600;"
            f"  border-bottom: 2px solid {COLORS['accent']};"
            f"}}"
            f"QTabBar::tab:hover {{"
            f"  color: {COLORS['text']};"
            f"}}"
        )
        main_layout.addWidget(self._tabs)

        # ── Tab 1: Modbus Poller ────────────────────────────────
        modbus_tab = QWidget()
        self._root = QVBoxLayout(modbus_tab)
        self._root.setContentsMargins(12, 12, 12, 6)
        self._root.setSpacing(10)

        self._build_connection_bar()
        self._build_toolbar()

        self._splitter = QSplitter(Qt.Vertical)
        self._build_table()
        self._build_log_panel()
        self._splitter.setSizes([500, 200])
        self._root.addWidget(self._splitter, stretch=1)

        self._tabs.addTab(modbus_tab, "Modbus Poller")

        # ── Tab 2: Discovery ────────────────────────────────────
        self._discovery_tab = DiscoveryTab()
        self._discovery_tab.connect_device.connect(self._on_discovery_connect)
        self._tabs.addTab(self._discovery_tab, "Network Discovery")

        self._build_status_bar()
        self._setup_logging()

        # Cyclic timer
        self._cyclic_timer = QTimer(self)
        self._cyclic_timer.timeout.connect(self._do_read_selected)

        # Auto-load last session
        cfg = load_config()
        self._apply_config(cfg)
        log.info(f"Session loaded from {AUTO_SAVE_PATH}")

    # ── Logging setup ───────────────────────────────────────────
    def _setup_logging(self):
        self._log_handler = _QtLogHandler()
        self._log_handler.log_signal.connect(self._append_log)
        self._log_handler.setLevel(logging.DEBUG)

        logger = logging.getLogger("modbus_tester")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(self._log_handler)

        log.info("═" * 60)
        log.info("  Modbus Tester started")
        log.info("═" * 60)

    def _append_log(self, message: str, level: str):
        """Append a log line to the log panel with color coding."""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        color_map = {
            "DEBUG":    COLORS["text_dim"],
            "INFO":     COLORS["text"],
            "WARNING":  COLORS["orange"],
            "ERROR":    COLORS["red"],
            "CRITICAL": COLORS["red"],
        }
        color = color_map.get(level, COLORS["text"])

        # Level badge
        badge_bg = {
            "ERROR":   COLORS["red_bg"],
            "WARNING": "#fff3cd",
        }.get(level, COLORS["surface_alt"])

        html = (
            f'<div style="margin:1px 0; font-family: Consolas, monospace; font-size:12px;">'
            f'<span style="color:{COLORS["text_dim"]}">{timestamp}</span> '
            f'<span style="background:{badge_bg}; color:{color}; '
            f'padding:1px 4px; border-radius:2px; font-size:10px;">{level:>5}</span> '
            f'<span style="color:{color}">{message}</span>'
            f'</div>'
        )
        self._log_text.append(html)

        # Auto-scroll to bottom
        cursor = self._log_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        self._log_text.setTextCursor(cursor)

        # Trim old lines if too long
        if self._log_text.document().blockCount() > self._log_max_lines:
            cursor = self._log_text.textCursor()
            cursor.movePosition(QTextCursor.Start)
            cursor.movePosition(QTextCursor.Down, QTextCursor.KeepAnchor, 500)
            cursor.removeSelectedText()

    # ── Connection Bar ──────────────────────────────────────────
    def _build_connection_bar(self):
        grp = QGroupBox("CONNECTION")
        lay = QHBoxLayout(grp)
        lay.setSpacing(10)

        def _add_field(label_text, widget, tooltip=""):
            lbl = QLabel(label_text)
            lbl.setProperty("cssClass", "dim")
            lay.addWidget(lbl)
            if tooltip:
                widget.setToolTip(tooltip)
            lay.addWidget(widget)

        self._host_edit = QLineEdit("192.168.1.1")
        self._host_edit.setFixedWidth(140)
        self._host_edit.setPlaceholderText("IP address")
        _add_field("Host", self._host_edit)

        self._port_spin = QSpinBox()
        self._port_spin.setButtonSymbols(QSpinBox.NoButtons)
        self._port_spin.setRange(1, 65535)
        self._port_spin.setValue(502)
        self._port_spin.setFixedWidth(60)
        _add_field("Port", self._port_spin)

        self._unit_spin = QSpinBox()
        self._unit_spin.setButtonSymbols(QSpinBox.NoButtons)
        self._unit_spin.setRange(0, 255)
        self._unit_spin.setValue(1)
        self._unit_spin.setFixedWidth(60)
        _add_field("Unit ID", self._unit_spin)

        self._byte_order_combo = QComboBox()
        for key, label in BYTE_ORDER_LABELS.items():
            self._byte_order_combo.addItem(label, key)
        self._byte_order_combo.setFixedWidth(200)
        _add_field("Byte Order", self._byte_order_combo)

        lay.addStretch()

        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(f"color: {COLORS['red']}; font-size: 18px;")
        lay.addWidget(self._status_dot)

        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setProperty("cssClass", "connect")
        self._connect_btn.setFixedWidth(110)
        self._connect_btn.clicked.connect(self._toggle_connection)
        lay.addWidget(self._connect_btn)

        # Auto-reconnect when host or port changes while connected
        self._host_edit.editingFinished.connect(self._on_connection_params_changed)
        self._port_spin.valueChanged.connect(self._on_connection_params_changed)

        self._root.addWidget(grp)

    # ── Toolbar ─────────────────────────────────────────────────
    def _build_toolbar(self):
        bar = QWidget()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        btn_read = QPushButton("⟳  Read Selected")
        btn_read.setProperty("cssClass", "accent")
        btn_read.setToolTip("Single read of all enabled rows")
        btn_read.clicked.connect(self._do_read_selected)
        lay.addWidget(btn_read)

        self._cyclic_btn = QPushButton("▶  Start Cyclic")
        self._cyclic_btn.setToolTip("Continuous polling at the set interval")
        self._cyclic_btn.clicked.connect(self._toggle_cyclic)
        lay.addWidget(self._cyclic_btn)

        self._interval_spin = QSpinBox()
        self._interval_spin.setButtonSymbols(QSpinBox.NoButtons)
        self._interval_spin.setRange(50, 10000)
        self._interval_spin.setValue(500)
        self._interval_spin.setSuffix(" ms")
        self._interval_spin.setFixedWidth(80)
        self._interval_spin.setToolTip("Cyclic polling interval")
        lay.addWidget(self._interval_spin)

        lay.addWidget(self._vsep())

        btn_write_all = QPushButton("⬆  Write All")
        btn_write_all.setToolTip("Write every row that has a write value entered")
        btn_write_all.clicked.connect(self._do_write_all)
        lay.addWidget(btn_write_all)

        lay.addWidget(self._vsep())

        btn_add = QPushButton("+  Add Row")
        btn_add.clicked.connect(lambda: self._add_row())
        lay.addWidget(btn_add)

        btn_bulk = QPushButton("++  Add Bulk")
        btn_bulk.setToolTip("Add multiple consecutive registers at once")
        btn_bulk.clicked.connect(self._add_bulk_dialog)
        lay.addWidget(btn_bulk)

        btn_del = QPushButton("−  Delete Selected")
        btn_del.clicked.connect(self._delete_selected)
        lay.addWidget(btn_del)

        lay.addWidget(self._vsep())

        btn_save = QPushButton("💾  Save")
        btn_save.setToolTip("Save configuration to file")
        btn_save.clicked.connect(self._save_config_dialog)
        lay.addWidget(btn_save)

        btn_load = QPushButton("📂  Load")
        btn_load.setToolTip("Load configuration from file")
        btn_load.clicked.connect(self._load_config_dialog)
        lay.addWidget(btn_load)

        lay.addStretch()

        # View controls (right side of toolbar)
        self._binary_toggle_btn = QPushButton("Binary")
        self._binary_toggle_btn.setToolTip("Toggle binary / decimal display")
        self._binary_toggle_btn.setCheckable(True)
        # self._binary_toggle_btn.setFixedWidth(50)
        self._binary_toggle_btn.clicked.connect(self._toggle_binary_view)
        lay.addWidget(self._binary_toggle_btn)

        # Log panel controls
        self._log_toggle_btn = QPushButton("📋  Log")
        self._log_toggle_btn.setToolTip("Show / hide transaction log")
        self._log_toggle_btn.setCheckable(True)
        self._log_toggle_btn.setChecked(True)
        self._log_toggle_btn.clicked.connect(self._toggle_log_panel)
        lay.addWidget(self._log_toggle_btn)

        self._root.addWidget(bar)

    def _vsep(self):
        sep = QWidget()
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background-color: {COLORS['border']};")
        sep.setFixedHeight(28)
        return sep

    # ── Register Table ──────────────────────────────────────────
    def _build_table(self):
        self._table = QTableWidget(0, NUM_COLS)
        self._table.setHorizontalHeaderLabels(COLUMN_HEADERS)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.verticalHeader().setDefaultSectionSize(34)
        self._table.verticalHeader().hide()

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(COL_ENABLE,   QHeaderView.Fixed)
        header.setSectionResizeMode(COL_LABEL,    QHeaderView.Stretch)
        header.setSectionResizeMode(COL_REGTYPE,  QHeaderView.Fixed)
        header.setSectionResizeMode(COL_OFFSET,   QHeaderView.Fixed)
        header.setSectionResizeMode(COL_DATATYPE, QHeaderView.Fixed)
        header.setSectionResizeMode(COL_ADDRESS,  QHeaderView.Fixed)
        header.setSectionResizeMode(COL_VALUE,    QHeaderView.Fixed)
        header.setSectionResizeMode(COL_STATUS,   QHeaderView.Fixed)
        header.setSectionResizeMode(COL_WRITE,    QHeaderView.Fixed)
        header.setSectionResizeMode(COL_WRITEBTN, QHeaderView.Fixed)

        self._table.setColumnWidth(COL_ENABLE,   36)
        self._table.setColumnWidth(COL_REGTYPE,  170)
        self._table.setColumnWidth(COL_OFFSET,   70)
        self._table.setColumnWidth(COL_DATATYPE, 110)
        self._table.setColumnWidth(COL_ADDRESS,  130)
        self._table.setColumnWidth(COL_VALUE,    260)
        self._table.setColumnWidth(COL_STATUS,   30)
        self._table.setColumnWidth(COL_WRITE,    120)
        self._table.setColumnWidth(COL_WRITEBTN, 50)

        self._table.cellChanged.connect(self._on_cell_changed)
        self._table.cellDoubleClicked.connect(self._on_value_double_click)
        self._splitter.addWidget(self._table)

    # ── Log Panel ───────────────────────────────────────────────
    def _build_log_panel(self):
        log_container = QWidget()
        log_layout = QVBoxLayout(log_container)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(4)

        # Header bar
        header = QWidget()
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(4, 2, 4, 2)
        header_lay.setSpacing(6)

        title = QLabel("TRANSACTION LOG")
        title.setProperty("cssClass", "dim")
        title.setStyleSheet(f"color: {COLORS['text_dim']}; font-weight: 600; font-size: 11px;")
        header_lay.addWidget(title)
        header_lay.addStretch()

        btn_copy = QPushButton("Copy")
        btn_copy.setFixedSize(60, 32)
        btn_copy.setToolTip("Copy log to clipboard")
        btn_copy.clicked.connect(self._copy_log)
        header_lay.addWidget(btn_copy)

        btn_clear = QPushButton("Clear")
        btn_clear.setFixedSize(60, 32)
        btn_clear.setToolTip("Clear the log")
        btn_clear.clicked.connect(self._clear_log)
        header_lay.addWidget(btn_clear)

        log_layout.addWidget(header)

        # Log text area
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setFont(QFont("Consolas", 10))
        self._log_text.setStyleSheet(
            f"QTextEdit {{"
            f"  background-color: {COLORS['input_bg']};"
            f"  border: 1px solid {COLORS['border']};"
            f"  border-radius: 4px;"
            f"  color: {COLORS['text']};"
            f"  padding: 4px;"
            f"}}"
        )
        log_layout.addWidget(self._log_text, stretch=1)

        self._log_container = log_container
        self._splitter.addWidget(log_container)

    def _toggle_log_panel(self):
        visible = self._log_toggle_btn.isChecked()
        self._log_container.setVisible(visible)

    def _clear_log(self):
        self._log_text.clear()
        log.info("Log cleared")

    def _copy_log(self):
        text = self._log_text.toPlainText()
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self._statusbar.showMessage("Log copied to clipboard", 2000)

    # ── Status Bar ──────────────────────────────────────────────
    def _build_status_bar(self):
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)

        self._status_conn_label = QLabel("Disconnected")
        self._status_conn_label.setProperty("cssClass", "status_disconnected")
        self._statusbar.addWidget(self._status_conn_label)

        self._status_scan_label = QLabel("")
        self._statusbar.addPermanentWidget(self._status_scan_label)

        self._status_error_label = QLabel("Errors: 0")
        self._statusbar.addPermanentWidget(self._status_error_label)

    # ═══════════════════════════════════════════════════════════
    #  TABLE ROW MANAGEMENT
    # ═══════════════════════════════════════════════════════════

    def _add_row(self, entry: RegisterEntry | None = None):
        if self._table.rowCount() >= self.MAX_ROWS:
            QMessageBox.warning(self, "Limit", f"Maximum {self.MAX_ROWS} rows.")
            return
        e = entry or RegisterEntry()
        row = self._table.rowCount()
        self._table.insertRow(row)

        # Enable checkbox
        cb = QCheckBox()
        cb.setChecked(e.enabled)
        cb_widget = QWidget()
        cb_lay = QHBoxLayout(cb_widget)
        cb_lay.addWidget(cb)
        cb_lay.setAlignment(Qt.AlignCenter)
        cb_lay.setContentsMargins(0, 0, 0, 0)
        self._table.setCellWidget(row, COL_ENABLE, cb_widget)

        # Label
        self._table.setItem(row, COL_LABEL, QTableWidgetItem(e.label))

        # Register Type combo
        rt_combo = QComboBox()
        for key, info in REG_TYPES.items():
            rt_combo.addItem(info["label"], key)
        idx = list(REG_TYPES.keys()).index(e.reg_type) if e.reg_type in REG_TYPES else 3
        rt_combo.setCurrentIndex(idx)
        rt_combo.currentIndexChanged.connect(lambda _, r=row: self._on_row_type_changed(r))
        self._table.setCellWidget(row, COL_REGTYPE, rt_combo)

        # Offset
        offset_item = QTableWidgetItem(str(e.offset))
        offset_item.setTextAlignment(Qt.AlignCenter)
        self._table.setItem(row, COL_OFFSET, offset_item)

        # Data Type combo
        dt_combo = QComboBox()
        dt_combo.addItems(DATA_TYPES)
        dt_idx = DATA_TYPES.index(e.data_type) if e.data_type in DATA_TYPES else 0
        dt_combo.setCurrentIndex(dt_idx)
        dt_combo.currentIndexChanged.connect(lambda _, r=row: self._update_address(r))
        self._table.setCellWidget(row, COL_DATATYPE, dt_combo)

        # Address (read-only)
        addr_item = QTableWidgetItem("")
        addr_item.setFlags(addr_item.flags() & ~Qt.ItemIsEditable)
        addr_item.setTextAlignment(Qt.AlignCenter)
        addr_item.setForeground(QColor(COLORS["accent"]))
        self._table.setItem(row, COL_ADDRESS, addr_item)

        # Value (read-only)
        val_item = QTableWidgetItem("")
        val_item.setFlags(val_item.flags() & ~Qt.ItemIsEditable)
        val_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # font = QFont("Consolas", 10)
        # val_item.setFont(font)
        self._table.setItem(row, COL_VALUE, val_item)

        # Status dot
        status_item = QTableWidgetItem("")
        status_item.setFlags(status_item.flags() & ~Qt.ItemIsEditable)
        status_item.setTextAlignment(Qt.AlignCenter)
        self._table.setItem(row, COL_STATUS, status_item)

        # Write value
        write_item = QTableWidgetItem("")
        write_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._table.setItem(row, COL_WRITE, write_item)

        # Write button
        wbtn = QPushButton("W")
        wbtn.setProperty("cssClass", "row_write")
        wbtn.setToolTip("Write this row")
        wbtn.clicked.connect(partial(self._do_write_single, row))
        w_widget = QWidget()
        w_lay = QHBoxLayout(w_widget)
        w_lay.addWidget(wbtn)
        w_lay.setAlignment(Qt.AlignCenter)
        w_lay.setContentsMargins(2, 2, 2, 2)
        self._table.setCellWidget(row, COL_WRITEBTN, w_widget)

        self._update_address(row)

    def _on_row_type_changed(self, row):
        self._update_address(row)
        self._update_write_button_state(row)

    def _on_cell_changed(self, row, col):
        """Recalculate address when the user edits the offset field."""
        if col == COL_OFFSET:
            self._update_address(row)

    def _update_address(self, row):
        rt_combo = self._table.cellWidget(row, COL_REGTYPE)
        if not rt_combo:
            return
        addr_item = self._table.item(row, COL_ADDRESS)
        if not addr_item:
            return  # row still being built
        reg_type = rt_combo.currentData()
        try:
            offset = int(self._table.item(row, COL_OFFSET).text())
        except (ValueError, AttributeError):
            offset = 0

        dt_combo = self._table.cellWidget(row, COL_DATATYPE)
        data_type = dt_combo.currentData() or dt_combo.currentText() if dt_combo else "UINT16"
        count = REG_COUNTS.get(data_type, 1)

        addr_str = compute_address_str(reg_type, offset)
        if count > 1:
            end_addr = compute_address_str(reg_type, offset + count - 1)
            addr_str = f"{addr_str}–{end_addr}"
        self._table.item(row, COL_ADDRESS).setText(addr_str)

    def _update_write_button_state(self, row):
        rt_combo = self._table.cellWidget(row, COL_REGTYPE)
        if not rt_combo:
            return
        writable = is_writable(rt_combo.currentData())
        w_widget = self._table.cellWidget(row, COL_WRITEBTN)
        if w_widget:
            btn = w_widget.findChild(QPushButton)
            if btn:
                btn.setEnabled(writable)

    def _delete_selected(self):
        rows = sorted(set(idx.row() for idx in self._table.selectedIndexes()),
                      reverse=True)
        for r in rows:
            self._table.removeRow(r)
            self._raw_values.pop(r, None)
            self._decoded_values.pop(r, None)

    def _get_row_checkbox(self, row) -> QCheckBox | None:
        w = self._table.cellWidget(row, COL_ENABLE)
        return w.findChild(QCheckBox) if w else None

    def _get_row_entry(self, row) -> dict:
        rt_combo = self._table.cellWidget(row, COL_REGTYPE)
        dt_combo = self._table.cellWidget(row, COL_DATATYPE)
        try:
            offset = int(self._table.item(row, COL_OFFSET).text())
        except (ValueError, AttributeError):
            offset = 0
        return {
            "reg_type":  rt_combo.currentData() if rt_combo else "holding",
            "offset":    offset,
            "data_type": (dt_combo.currentData() or dt_combo.currentText())
                         if dt_combo else "UINT16",
        }

    # ═══════════════════════════════════════════════════════════
    #  ADD BULK DIALOG
    # ═══════════════════════════════════════════════════════════

    def _add_bulk_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Add Bulk Registers")
        dlg.setFixedSize(360, 260)
        layout = QGridLayout(dlg)

        layout.addWidget(QLabel("Register Type:"), 0, 0)
        rt_combo = QComboBox()
        for key, info in REG_TYPES.items():
            rt_combo.addItem(info["label"], key)
        rt_combo.setCurrentIndex(3)
        layout.addWidget(rt_combo, 0, 1)

        layout.addWidget(QLabel("Start Offset:"), 1, 0)
        start_spin = QSpinBox()
        start_spin.setButtonSymbols(QSpinBox.NoButtons)
        start_spin.setRange(0, 65535)
        layout.addWidget(start_spin, 1, 1)

        layout.addWidget(QLabel("Count:"), 2, 0)
        count_spin = QSpinBox()
        count_spin.setButtonSymbols(QSpinBox.NoButtons)
        count_spin.setRange(1, 50)
        count_spin.setValue(10)
        layout.addWidget(count_spin, 2, 1)

        layout.addWidget(QLabel("Data Type:"), 3, 0)
        dt_combo = QComboBox()
        dt_combo.addItems(DATA_TYPES)
        layout.addWidget(dt_combo, 3, 1)

        layout.addWidget(QLabel("Label Prefix:"), 4, 0)
        prefix_edit = QLineEdit()
        prefix_edit.setPlaceholderText("e.g. 'Reg' → Reg_0, Reg_1, ...")
        layout.addWidget(prefix_edit, 4, 1)

        btn_box = QHBoxLayout()
        btn_ok = QPushButton("Add")
        btn_ok.setProperty("cssClass", "accent")
        btn_cancel = QPushButton("Cancel")
        btn_box.addStretch()
        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(btn_ok)
        layout.addLayout(btn_box, 5, 0, 1, 2)

        def accept():
            reg_type = rt_combo.currentData()
            start = start_spin.value()
            count = count_spin.value()
            data_type = dt_combo.currentText()
            prefix = prefix_edit.text().strip()
            reg_size = REG_COUNTS.get(data_type, 1)

            available = self.MAX_ROWS - self._table.rowCount()
            actual = min(count, available)
            if actual < count:
                QMessageBox.information(
                    dlg, "Note",
                    f"Adding {actual} of {count} (table limit {self.MAX_ROWS})."
                )

            for i in range(actual):
                offset = start + i * reg_size
                label = f"{prefix}_{offset}" if prefix else ""
                self._add_row(RegisterEntry(
                    label=label, reg_type=reg_type,
                    offset=offset, data_type=data_type,
                ))
            log.info(f"Added {actual} bulk rows: {reg_type} start={start} "
                     f"type={data_type} stride={reg_size}")
            dlg.accept()

        btn_ok.clicked.connect(accept)
        btn_cancel.clicked.connect(dlg.reject)
        dlg.exec()

    # ═══════════════════════════════════════════════════════════
    #  CONNECTION
    # ═══════════════════════════════════════════════════════════

    def _toggle_connection(self):
        if self.backend.is_connected():
            self._stop_cyclic()
            self.backend.disconnect()
            self._set_connection_ui(False)
        else:
            host = self._host_edit.text().strip()
            port = self._port_spin.value()
            uid = self._unit_spin.value()
            try:
                ok = self.backend.connect(host=host, port=port, unit_id=uid)
                self._set_connection_ui(ok)
                if not ok:
                    QMessageBox.warning(self, "Connection Failed",
                                        f"Could not connect to {host}:{port}")
            except Exception as exc:
                log.error(f"Connection exception: {exc}")
                self._set_connection_ui(False)
                QMessageBox.critical(self, "Error", str(exc))

    def _on_connection_params_changed(self):
        """Auto-reconnect when host or port is changed while connected."""
        if not self.backend.is_connected():
            return
        host = self._host_edit.text().strip()
        port = self._port_spin.value()
        uid = self._unit_spin.value()
        log.info(f"Connection parameters changed → reconnecting to {host}:{port}")
        self._stop_cyclic()
        self.backend.disconnect()
        try:
            ok = self.backend.connect(host=host, port=port, unit_id=uid)
            self._set_connection_ui(ok)
            if not ok:
                self._statusbar.showMessage(
                    f"Reconnect failed: {host}:{port}", 4000)
        except Exception as exc:
            log.error(f"Reconnect failed: {exc}")
            self._set_connection_ui(False)

    def _on_discovery_connect(self, host: str, port: int):
        """Called when the user clicks 'Use in Modbus Tab' in Discovery."""
        self._stop_cyclic()
        self.backend.disconnect()
        self._host_edit.setText(host)
        self._port_spin.setValue(port)
        # Switch to the Modbus tab
        self._tabs.setCurrentIndex(0)
        # Auto-connect
        uid = self._unit_spin.value()
        try:
            ok = self.backend.connect(host=host, port=port, unit_id=uid)
            self._set_connection_ui(ok)
            if ok:
                log.info(f"Connected to {host}:{port} from Discovery")
            else:
                QMessageBox.warning(self, "Connection Failed",
                                    f"Could not connect to {host}:{port}")
        except Exception as exc:
            log.error(f"Connection from Discovery failed: {exc}")
            self._set_connection_ui(False)

    def _set_connection_ui(self, connected: bool):
        if connected:
            self._connect_btn.setText("Disconnect")
            self._connect_btn.setProperty("cssClass", "disconnect")
            self._status_dot.setStyleSheet(f"color: {COLORS['green']}; font-size: 18px;")
            host = self._host_edit.text().strip()
            port = self._port_spin.value()
            self._status_conn_label.setText(f"Connected to {host}:{port}")
            self._status_conn_label.setProperty("cssClass", "status_connected")
        else:
            self._connect_btn.setText("Connect")
            self._connect_btn.setProperty("cssClass", "connect")
            self._status_dot.setStyleSheet(f"color: {COLORS['red']}; font-size: 18px;")
            self._status_conn_label.setText("Disconnected")
            self._status_conn_label.setProperty("cssClass", "status_disconnected")
        self._connect_btn.style().unpolish(self._connect_btn)
        self._connect_btn.style().polish(self._connect_btn)
        self._status_conn_label.style().unpolish(self._status_conn_label)
        self._status_conn_label.style().polish(self._status_conn_label)

    # ═══════════════════════════════════════════════════════════
    #  READING
    # ═══════════════════════════════════════════════════════════

    def _enabled_rows(self) -> list[int]:
        rows = []
        for r in range(self._table.rowCount()):
            cb = self._get_row_checkbox(r)
            if cb and cb.isChecked():
                rows.append(r)
        return rows

    def _do_read_selected(self):
        if not self.backend.is_connected():
            self._statusbar.showMessage("Not connected", 3000)
            return
        if self._read_thread and self._read_thread.isRunning():
            return

        rows = self._enabled_rows()
        if not rows:
            self._statusbar.showMessage("No rows enabled", 2000)
            return

        byte_order = self._byte_order_combo.currentData()
        rows_data = []
        for r in rows:
            info = self._get_row_entry(r)
            rows_data.append((r, info["reg_type"], info["offset"], info["data_type"]))
            self._table.item(r, COL_STATUS).setText("…")
            self._table.item(r, COL_STATUS).setForeground(QColor(COLORS["yellow"]))

        log.info(f"─── Read sweep: {len(rows)} row(s), byte_order={byte_order} ───")

        worker = _ReadWorker(self.backend, rows_data, byte_order)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.row_result.connect(self._on_read_result)
        worker.finished.connect(lambda ms: self._on_read_finished(ms, thread))
        worker.finished.connect(thread.quit)
        self._read_thread = thread
        self._read_worker = worker
        thread.start()

    def _on_read_result(self, row: int, value, raw_regs, error: str):
        if error:
            self._table.item(row, COL_VALUE).setText("ERR")
            self._table.item(row, COL_VALUE).setForeground(QColor(COLORS["red"]))
            self._table.item(row, COL_STATUS).setText("●")
            self._table.item(row, COL_STATUS).setForeground(QColor(COLORS["red"]))
            self._table.item(row, COL_STATUS).setToolTip(error)
            self._error_count += 1
        else:
            self._decoded_values[row] = value
            self._raw_values[row] = raw_regs if raw_regs else []
            display = self._format_value(row, value, raw_regs)
            self._table.item(row, COL_VALUE).setText(display)
            self._table.item(row, COL_VALUE).setForeground(QColor(COLORS["text"]))
            self._table.item(row, COL_STATUS).setText("●")
            self._table.item(row, COL_STATUS).setForeground(QColor(COLORS["green"]))
            self._table.item(row, COL_STATUS).setToolTip("OK")

        self._status_error_label.setText(f"Errors: {self._error_count}")

    def _format_value(self, row, value, raw_regs):
        """Format a value for the Value column based on the display mode."""
        if self._show_binary:
            return self._format_binary(raw_regs)
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        elif isinstance(value, float):
            return f"{value:.4f}"
        return str(value)

    @staticmethod
    def _format_binary(raw_regs):
        """Format raw 16-bit register values as a binary string."""
        if not raw_regs:
            return ""
        if len(raw_regs) == 1:
            v = raw_regs[0]
            if v in (0, 1):
                return str(v)
            return f"{v:016b}"
        # Multi-register: concatenate
        return " ".join(f"{r:016b}" for r in raw_regs)

    def _toggle_binary_view(self):
        """Toggle all Value cells between decimal and binary display."""
        self._show_binary = self._binary_toggle_btn.isChecked()
        for row in range(self._table.rowCount()):
            if row in self._decoded_values:
                display = self._format_value(
                    row, self._decoded_values[row], self._raw_values.get(row, [])
                )
                self._table.item(row, COL_VALUE).setText(display)

    def _on_value_double_click(self, row, col):
        """Show a popup with both decimal and binary representations."""
        if col != COL_VALUE:
            return
        if row not in self._raw_values:
            return

        raw = self._raw_values[row]
        val = self._decoded_values.get(row)

        lines = []
        if val is not None:
            if isinstance(val, bool):
                lines.append(f"Decimal:  {'1' if val else '0'}  ({val})")
            elif isinstance(val, float):
                lines.append(f"Decimal:  {val}")
            else:
                lines.append(f"Decimal:  {val}")

        if raw:
            lines.append(f"Hex:      {' '.join(f'0x{r:04X}' for r in raw)}")
            lines.append(f"Binary:   {' '.join(f'{r:016b}' for r in raw)}")
            if len(raw) == 1:
                lines.append(f"Unsigned: {raw[0]}")
                import struct
                signed = struct.unpack('>h', struct.pack('>H', raw[0]))[0]
                lines.append(f"Signed:   {signed}")

        info = self._get_row_entry(row)
        label_item = self._table.item(row, COL_LABEL)
        label = label_item.text() if label_item else ""
        addr = compute_address_str(info["reg_type"], info["offset"])
        title = f"{label} ({addr})" if label else addr

        QMessageBox.information(
            self, f"Register Detail — {title}",
            "\n".join(lines),
        )

        self._status_error_label.setText(f"Errors: {self._error_count}")

    def _on_read_finished(self, elapsed_ms: float, thread: QThread):
        self._last_scan_ms = elapsed_ms
        self._status_scan_label.setText(f"Last scan: {elapsed_ms:.1f} ms")

    # ═══════════════════════════════════════════════════════════
    #  CYCLIC
    # ═══════════════════════════════════════════════════════════

    def _toggle_cyclic(self):
        if self._cyclic_running:
            self._stop_cyclic()
        else:
            self._start_cyclic()

    def _start_cyclic(self):
        if not self.backend.is_connected():
            self._statusbar.showMessage("Not connected", 3000)
            return
        interval = self._interval_spin.value()
        log.info(f"Cyclic polling STARTED — interval {interval} ms")
        self._cyclic_running = True
        self._cyclic_btn.setText("■  Stop Cyclic")
        self._cyclic_btn.setProperty("cssClass", "disconnect")
        self._cyclic_btn.style().unpolish(self._cyclic_btn)
        self._cyclic_btn.style().polish(self._cyclic_btn)
        self._cyclic_timer.start(interval)

    def _stop_cyclic(self):
        self._cyclic_running = False
        self._cyclic_timer.stop()
        log.info("Cyclic polling STOPPED")
        self._cyclic_btn.setText("▶  Start Cyclic")
        self._cyclic_btn.setProperty("cssClass", "")
        self._cyclic_btn.style().unpolish(self._cyclic_btn)
        self._cyclic_btn.style().polish(self._cyclic_btn)

    # ═══════════════════════════════════════════════════════════
    #  WRITING
    # ═══════════════════════════════════════════════════════════

    def _do_write_single(self, row: int):
        if not self.backend.is_connected():
            self._statusbar.showMessage("Not connected", 3000)
            return
        info = self._get_row_entry(row)
        if not is_writable(info["reg_type"]):
            QMessageBox.warning(self, "Read-Only",
                                f"{info['reg_type']} is not writable.")
            return
        write_val = self._table.item(row, COL_WRITE).text().strip()
        if not write_val:
            self._statusbar.showMessage("No write value entered", 2000)
            return

        byte_order = self._byte_order_combo.currentData()
        rows_data = [(row, info["reg_type"], info["offset"],
                      info["data_type"], write_val)]
        self._run_write(rows_data, byte_order)

    def _do_write_all(self):
        if not self.backend.is_connected():
            self._statusbar.showMessage("Not connected", 3000)
            return

        byte_order = self._byte_order_combo.currentData()
        rows_data = []
        for r in range(self._table.rowCount()):
            info = self._get_row_entry(r)
            if not is_writable(info["reg_type"]):
                continue
            write_val = self._table.item(r, COL_WRITE).text().strip()
            if not write_val:
                continue
            rows_data.append((r, info["reg_type"], info["offset"],
                              info["data_type"], write_val))

        if not rows_data:
            self._statusbar.showMessage("No writable values entered", 2000)
            return

        count = len(rows_data)
        reply = QMessageBox.question(
            self, "Confirm Write",
            f"Write {count} value{'s' if count > 1 else ''} to device?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._run_write(rows_data, byte_order)

    def _run_write(self, rows_data, byte_order):
        self._write_success_rows = []
        self._write_byte_order = byte_order
        worker = _WriteWorker(self.backend, rows_data, byte_order)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.row_result.connect(self._on_write_result)
        worker.finished.connect(self._on_write_finished)
        worker.finished.connect(thread.quit)
        self._write_thread = thread
        self._write_worker = worker
        thread.start()

    def _on_write_result(self, row: int, success: bool, error: str):
        if success:
            self._table.item(row, COL_STATUS).setText("✓")
            self._table.item(row, COL_STATUS).setForeground(QColor(COLORS["green"]))
            self._table.item(row, COL_STATUS).setToolTip("Write OK — verifying…")
            self._write_success_rows.append(row)
        else:
            self._table.item(row, COL_STATUS).setText("✗")
            self._table.item(row, COL_STATUS).setForeground(QColor(COLORS["red"]))
            self._table.item(row, COL_STATUS).setToolTip(error)
            self._statusbar.showMessage(f"Write error: {error}", 4000)

    def _on_write_finished(self):
        """After all writes complete, read back the written rows to verify."""
        rows = self._write_success_rows
        if not rows or not self.backend.is_connected():
            return

        byte_order = self._write_byte_order
        rows_data = []
        for r in rows:
            info = self._get_row_entry(r)
            rows_data.append((r, info["reg_type"], info["offset"], info["data_type"]))

        log.info(f"─── Verify read-back: {len(rows)} row(s) after write ───")

        worker = _ReadWorker(self.backend, rows_data, byte_order)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.row_result.connect(self._on_read_result)
        worker.finished.connect(lambda ms: self._on_read_finished(ms, thread))
        worker.finished.connect(thread.quit)
        self._read_thread = thread
        self._read_worker = worker
        thread.start()

    # ═══════════════════════════════════════════════════════════
    #  CONFIGURATION
    # ═══════════════════════════════════════════════════════════

    def _gather_config(self) -> AppConfig:
        conn = ConnectionConfig(
            host=self._host_edit.text().strip(),
            port=self._port_spin.value(),
            unit_id=self._unit_spin.value(),
            byte_order=self._byte_order_combo.currentData(),
            scan_interval_ms=self._interval_spin.value(),
        )
        entries = []
        for r in range(self._table.rowCount()):
            cb = self._get_row_checkbox(r)
            rt_combo = self._table.cellWidget(r, COL_REGTYPE)
            dt_combo = self._table.cellWidget(r, COL_DATATYPE)
            try:
                offset = int(self._table.item(r, COL_OFFSET).text())
            except (ValueError, AttributeError):
                offset = 0
            entries.append(RegisterEntry(
                label=self._table.item(r, COL_LABEL).text() if self._table.item(r, COL_LABEL) else "",
                reg_type=rt_combo.currentData() if rt_combo else "holding",
                offset=offset,
                data_type=(dt_combo.currentData() or dt_combo.currentText()) if dt_combo else "UINT16",
                enabled=cb.isChecked() if cb else True,
            ))
        return AppConfig(connection=conn, entries=entries)

    def _apply_config(self, cfg: AppConfig):
        self._host_edit.setText(cfg.connection.host)
        self._port_spin.setValue(cfg.connection.port)
        self._unit_spin.setValue(cfg.connection.unit_id)
        idx = self._byte_order_combo.findData(cfg.connection.byte_order)
        if idx >= 0:
            self._byte_order_combo.setCurrentIndex(idx)
        self._interval_spin.setValue(cfg.connection.scan_interval_ms)

        self._table.setRowCount(0)
        self._raw_values.clear()
        self._decoded_values.clear()
        for entry in cfg.entries:
            self._add_row(entry)

    def _save_config_dialog(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Configuration", "",
            "JSON Files (*.json);;All Files (*)",
        )
        if path:
            cfg = self._gather_config()
            save_config(cfg, path)
            log.info(f"Config saved to {path}")
            self._statusbar.showMessage(f"Saved to {path}", 3000)

    def _load_config_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Configuration", "",
            "JSON Files (*.json);;All Files (*)",
        )
        if path:
            cfg = load_config(path)
            self._apply_config(cfg)
            log.info(f"Config loaded from {path}")
            self._statusbar.showMessage(f"Loaded from {path}", 3000)

    # ── Auto-save on close ──────────────────────────────────────
    def closeEvent(self, event):
        self._stop_cyclic()
        self.backend.disconnect()
        try:
            save_config(self._gather_config())
            log.info(f"Auto-saved to {AUTO_SAVE_PATH}")
        except Exception as exc:
            log.error(f"Auto-save failed: {exc}")
        event.accept()
