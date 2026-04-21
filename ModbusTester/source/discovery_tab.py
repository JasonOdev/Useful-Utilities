"""
Discovery tab — scan the network for industrial devices.
"""
import webbrowser
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QLineEdit,
    QPushButton, QComboBox, QCheckBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QProgressBar, QSplitter, QTextEdit, QAbstractItemView,
    QApplication,
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont

from styles import COLORS
from discovery_engine import (
    DiscoveryWorker, DiscoveredDevice, get_local_interfaces,
)


# ── Column indices ──────────────────────────────────────────────
C_IP       = 0
C_MAC      = 1
C_VENDOR   = 2
C_HOSTNAME = 3
C_PROTO    = 4
C_DEVICE   = 5
NUM_COLS   = 6

COL_HEADERS = ["IP Address", "MAC Address", "Vendor", "Hostname",
               "Protocols", "Device"]


class DiscoveryTab(QWidget):
    # Signal to tell the main window to connect to a device
    connect_device = Signal(str, int)   # host, port

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scan_thread: QThread | None = None
        self._scan_worker: DiscoveryWorker | None = None
        self._devices: dict[str, DiscoveredDevice] = {}
        self._row_map: dict[str, int] = {}  # ip → table row

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 6)
        layout.setSpacing(10)

        self._build_scan_bar(layout)
        self._splitter = QSplitter(Qt.Vertical)
        self._build_results_table()
        self._build_detail_panel()
        self._splitter.setSizes([500, 200])
        layout.addWidget(self._splitter, stretch=1)
        self._build_progress_bar(layout)

    # ── Scan bar ────────────────────────────────────────────────
    def _build_scan_bar(self, parent_layout):
        grp = QGroupBox("NETWORK SCAN")
        lay = QHBoxLayout(grp)
        lay.setSpacing(10)

        # Subnet selector
        lbl = QLabel("Subnet")
        lbl.setProperty("cssClass", "dim")
        lay.addWidget(lbl)

        self._iface_combo = QComboBox()
        self._iface_combo.setFixedWidth(140)
        interfaces = get_local_interfaces()
        for iface in interfaces:
            self._iface_combo.addItem(iface["subnet"], iface)
        self._iface_combo.currentIndexChanged.connect(self._on_iface_changed)
        lay.addWidget(self._iface_combo)

        self._subnet_edit = QLineEdit()
        self._subnet_edit.setFixedWidth(160)
        self._subnet_edit.setPlaceholderText("e.g. 192.168.1.0/24")
        if interfaces:
            self._subnet_edit.setText(interfaces[0]["subnet"])
        lay.addWidget(self._subnet_edit)

        lay.addWidget(self._vsep())

        # Scan method checkboxes
        self._chk_ping = QCheckBox("Ping Sweep")
        self._chk_ping.setChecked(True)
        self._chk_ping.setToolTip("ICMP ping every IP in the subnet")
        lay.addWidget(self._chk_ping)

        self._chk_ports = QCheckBox("Port Scan")
        self._chk_ports.setChecked(False)
        self._chk_ports.setToolTip(
            "Probe known ports: 502, 28784, 25425, 44818, 80, 443"
        )
        lay.addWidget(self._chk_ports)

        self._chk_eip = QCheckBox("EtherNet/IP")
        self._chk_eip.setChecked(False)
        self._chk_eip.setToolTip("EtherNet/IP ListIdentity broadcast")
        lay.addWidget(self._chk_eip)

        self._chk_modbus = QCheckBox("Modbus ID")
        self._chk_modbus.setChecked(False)
        self._chk_modbus.setToolTip("Modbus FC43 device identification")
        lay.addWidget(self._chk_modbus)

        lay.addStretch()

        # Buttons
        self._scan_btn = QPushButton("🔍  Scan")
        self._scan_btn.setProperty("cssClass", "accent")
        self._scan_btn.setFixedWidth(100)
        self._scan_btn.clicked.connect(self._start_scan)
        lay.addWidget(self._scan_btn)

        self._stop_btn = QPushButton("■  Stop")
        self._stop_btn.setFixedWidth(80)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_scan)
        lay.addWidget(self._stop_btn)

        parent_layout.addWidget(grp)

    def _vsep(self):
        sep = QWidget()
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background-color: {COLORS['border']};")
        sep.setFixedHeight(24)
        return sep

    def _on_iface_changed(self, index):
        data = self._iface_combo.currentData()
        if data:
            self._subnet_edit.setText(data["subnet"])

    # ── Results table ───────────────────────────────────────────
    def _build_results_table(self):
        self._table = QTableWidget(0, NUM_COLS)
        self._table.setHorizontalHeaderLabels(COL_HEADERS)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setDefaultSectionSize(32)
        self._table.verticalHeader().hide()

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(C_IP,       QHeaderView.Fixed)
        header.setSectionResizeMode(C_MAC,      QHeaderView.Fixed)
        header.setSectionResizeMode(C_VENDOR,   QHeaderView.Stretch)
        header.setSectionResizeMode(C_HOSTNAME, QHeaderView.Stretch)
        header.setSectionResizeMode(C_PROTO,    QHeaderView.Fixed)
        header.setSectionResizeMode(C_DEVICE,   QHeaderView.Stretch)

        self._table.setColumnWidth(C_IP,    130)
        self._table.setColumnWidth(C_MAC,   140)
        self._table.setColumnWidth(C_PROTO, 180)

        self._table.currentCellChanged.connect(self._on_row_selected)

        self._splitter.addWidget(self._table)

    # ── Detail panel ────────────────────────────────────────────
    def _build_detail_panel(self):
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(4)

        # Header with action buttons
        header = QWidget()
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(4, 2, 4, 2)
        header_lay.setSpacing(6)

        title = QLabel("DEVICE DETAIL")
        title.setProperty("cssClass", "dim")
        title.setStyleSheet(
            f"color: {COLORS['text_dim']}; font-weight: 600; font-size: 11px;"
        )
        header_lay.addWidget(title)
        header_lay.addStretch()

        self._btn_use_modbus = QPushButton("Use in Modbus Tab")
        self._btn_use_modbus.setProperty("cssClass", "accent")
        self._btn_use_modbus.setFixedHeight(32)
        self._btn_use_modbus.setEnabled(False)
        self._btn_use_modbus.clicked.connect(self._use_in_modbus)
        header_lay.addWidget(self._btn_use_modbus)

        self._btn_open_web = QPushButton("Open Web Config")
        self._btn_open_web.setFixedHeight(32)
        self._btn_open_web.setEnabled(False)
        self._btn_open_web.clicked.connect(self._open_web_config)
        header_lay.addWidget(self._btn_open_web)

        btn_copy = QPushButton("Copy IP")
        btn_copy.setFixedHeight(32)
        btn_copy.clicked.connect(self._copy_selected_ip)
        header_lay.addWidget(btn_copy)

        detail_layout.addWidget(header)

        # Detail text
        self._detail_text = QTextEdit()
        self._detail_text.setReadOnly(True)
        self._detail_text.setFont(QFont("Consolas", 10))
        self._detail_text.setStyleSheet(
            f"QTextEdit {{"
            f"  background-color: {COLORS['input_bg']};"
            f"  border: 1px solid {COLORS['border']};"
            f"  border-radius: 4px;"
            f"  color: {COLORS['text']};"
            f"  padding: 6px;"
            f"}}"
        )
        detail_layout.addWidget(self._detail_text, stretch=1)

        self._splitter.addWidget(detail)

    # ── Progress bar ────────────────────────────────────────────
    def _build_progress_bar(self, parent_layout):
        bar = QWidget()
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(0, 0, 0, 0)
        bar_lay.setSpacing(8)

        self._progress = QProgressBar()
        self._progress.setFixedHeight(18)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet(
            f"QProgressBar {{"
            f"  background-color: {COLORS['surface_alt']};"
            f"  border: 1px solid {COLORS['border']};"
            f"  border-radius: 4px;"
            f"}}"
            f"QProgressBar::chunk {{"
            f"  background-color: {COLORS['accent']};"
            f"  border-radius: 3px;"
            f"}}"
        )
        bar_lay.addWidget(self._progress, stretch=1)

        self._progress_label = QLabel("")
        self._progress_label.setProperty("cssClass", "dim")
        self._progress_label.setFixedWidth(300)
        bar_lay.addWidget(self._progress_label)

        self._devices_count_label = QLabel("0 devices found")
        self._devices_count_label.setStyleSheet(
            f"color: {COLORS['accent']}; font-weight: 600;"
        )
        bar_lay.addWidget(self._devices_count_label)

        parent_layout.addWidget(bar)

    # ═══════════════════════════════════════════════════════════
    #  SCANNING
    # ═══════════════════════════════════════════════════════════

    def _start_scan(self):
        if self._scan_thread and self._scan_thread.isRunning():
            return

        # Clear previous results
        self._table.setRowCount(0)
        self._devices.clear()
        self._row_map.clear()
        self._detail_text.clear()

        subnet = self._subnet_edit.text().strip()
        if not subnet:
            return

        self._scan_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._progress.setValue(0)

        worker = DiscoveryWorker(
            subnet=subnet,
            do_ping=self._chk_ping.isChecked(),
            do_ports=self._chk_ports.isChecked(),
            do_eip=self._chk_eip.isChecked(),
            do_modbus_id=self._chk_modbus.isChecked(),
        )
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.device_found.connect(self._on_device_found)
        worker.device_updated.connect(self._on_device_updated)
        worker.progress.connect(self._on_progress)
        worker.scan_finished.connect(self._on_scan_finished)
        worker.scan_finished.connect(thread.quit)

        self._scan_worker = worker
        self._scan_thread = thread
        thread.start()

    def _stop_scan(self):
        if self._scan_worker:
            self._scan_worker.cancel()
        self._stop_btn.setEnabled(False)
        self._progress_label.setText("Stopping...")

    def _on_device_found(self, dev: DiscoveredDevice):
        ip = dev.ip
        if ip in self._row_map:
            return
        self._devices[ip] = dev
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._row_map[ip] = row
        self._populate_row(row, dev)
        self._devices_count_label.setText(
            f"{len(self._devices)} device{'s' if len(self._devices) != 1 else ''} found"
        )

    def _on_device_updated(self, dev: DiscoveredDevice):
        ip = dev.ip
        self._devices[ip] = dev
        row = self._row_map.get(ip)
        if row is not None:
            self._populate_row(row, dev)
        # Refresh detail if this device is selected
        sel_row = self._table.currentRow()
        if sel_row >= 0 and sel_row == row:
            self._show_detail(dev)

    def _on_progress(self, current, total, phase):
        if total > 0:
            pct = int(100 * current / total)
            self._progress.setValue(pct)
        self._progress_label.setText(phase)

    def _on_scan_finished(self, elapsed_ms):
        self._scan_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._progress.setValue(100)
        self._progress_label.setText(
            f"Scan complete — {elapsed_ms / 1000:.1f}s"
        )

    # ═══════════════════════════════════════════════════════════
    #  TABLE MANAGEMENT
    # ═══════════════════════════════════════════════════════════

    def _populate_row(self, row: int, dev: DiscoveredDevice):
        mono = QFont("Consolas", 10)

        ip_item = QTableWidgetItem(dev.ip)
        ip_item.setFont(mono)
        self._table.setItem(row, C_IP, ip_item)

        mac_item = QTableWidgetItem(dev.mac)
        mac_item.setFont(mono)
        mac_item.setForeground(QColor(COLORS["text_dim"]))
        self._table.setItem(row, C_MAC, mac_item)

        vendor_item = QTableWidgetItem(dev.vendor)
        if dev.vendor and dev.vendor != "Unknown":
            vendor_item.setForeground(QColor(COLORS["accent"]))
            vendor_item.setFont(QFont(self.font().family(), -1, QFont.Bold))
        self._table.setItem(row, C_VENDOR, vendor_item)

        self._table.setItem(row, C_HOSTNAME, QTableWidgetItem(dev.hostname))

        proto_item = QTableWidgetItem(dev.protocols)
        if dev.protocols:
            proto_item.setForeground(QColor(COLORS["green"]))
        self._table.setItem(row, C_PROTO, proto_item)

        device_item = QTableWidgetItem(dev.device_label)
        if dev.device_label:
            device_item.setForeground(QColor(COLORS["accent"]))
        self._table.setItem(row, C_DEVICE, device_item)

    def _on_row_selected(self, row, col, prev_row, prev_col):
        if row < 0:
            self._btn_use_modbus.setEnabled(False)
            self._btn_open_web.setEnabled(False)
            return
        ip_item = self._table.item(row, C_IP)
        if not ip_item:
            return
        ip = ip_item.text()
        dev = self._devices.get(ip)
        if dev:
            self._show_detail(dev)
            self._btn_use_modbus.setEnabled(502 in dev.open_ports)
            self._btn_open_web.setEnabled(
                80 in dev.open_ports or 443 in dev.open_ports
            )

    def _show_detail(self, dev: DiscoveredDevice):
        lines = []
        lines.append(f"<b>IP Address:</b>  {dev.ip}")
        if dev.mac:
            lines.append(f"<b>MAC Address:</b>  {dev.mac}")
        if dev.vendor and dev.vendor != "Unknown":
            lines.append(f"<b>MAC Vendor:</b>  {dev.vendor}")
        if dev.hostname:
            lines.append(f"<b>Hostname:</b>  {dev.hostname}")

        if dev.open_ports:
            ports_str = ", ".join(
                f"{p} ({n})" for p, n in sorted(dev.open_ports.items())
            )
            lines.append(f"<b>Open Ports:</b>  {ports_str}")

        if dev.eip_product_name:
            lines.append("")
            lines.append(
                f"<b>EtherNet/IP Identity:</b>"
            )
            lines.append(
                f"  Product: {dev.eip_product_name}  "
                f"rev {dev.eip_revision}"
            )
            lines.append(
                f"  Vendor ID: {dev.eip_vendor_id}  "
                f"Serial: {dev.eip_serial}"
            )

        if dev.modbus_vendor or dev.modbus_product:
            lines.append("")
            lines.append(f"<b>Modbus Device ID (FC43):</b>")
            if dev.modbus_vendor:
                lines.append(f"  Vendor: {dev.modbus_vendor}")
            if dev.modbus_product:
                lines.append(f"  Product: {dev.modbus_product}")
            if dev.modbus_revision:
                lines.append(f"  Revision: {dev.modbus_revision}")

        self._detail_text.setHtml(
            f'<div style="font-family: Consolas, monospace; '
            f'font-size: 12px; color: {COLORS["text"]};">'
            + "<br>".join(lines)
            + "</div>"
        )

    # ═══════════════════════════════════════════════════════════
    #  ACTIONS
    # ═══════════════════════════════════════════════════════════

    def _get_selected_device(self) -> DiscoveredDevice | None:
        row = self._table.currentRow()
        if row < 0:
            return None
        ip_item = self._table.item(row, C_IP)
        if not ip_item:
            return None
        return self._devices.get(ip_item.text())

    def _use_in_modbus(self):
        dev = self._get_selected_device()
        if dev and 502 in dev.open_ports:
            self.connect_device.emit(dev.ip, 502)

    def _open_web_config(self):
        dev = self._get_selected_device()
        if dev:
            if 443 in dev.open_ports:
                webbrowser.open(f"https://{dev.ip}")
            elif 80 in dev.open_ports:
                webbrowser.open(f"http://{dev.ip}")

    def _copy_selected_ip(self):
        dev = self._get_selected_device()
        if dev:
            clipboard = QApplication.clipboard()
            clipboard.setText(dev.ip)
