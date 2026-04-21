# Modbus Tester

A lightweight desktop utility for testing and debugging Modbus TCP communications, with a built-in network discovery tool. Built with PySide6.

## Installation

```
pip install PySide6 pymodbus
python main.py
```
Or just use the compiled exe

## Modbus Poller Tab

The main workspace for reading and writing Modbus registers.

**Connection bar** — Enter host IP, port (default 502), and unit ID. Select the byte order to match your device (Big Endian, Little Endian, Mid-Big, Mid-Little — this matters for 32-bit values). Click Connect. If you change the host or port while connected, it automatically reconnects.

**Adding registers** — Click **+ Add Row** to add a single register, or **++ Add Bulk** to create multiple consecutive rows at once (e.g. 16 holding registers starting at offset 100). Each row has a label, register type (coil, discrete input, input register, or holding register), an offset, and a data type. The Modbus address is calculated automatically from the type and offset. For 32-bit types like FLOAT32, the tool reads two registers behind the scenes and decodes them using the selected byte order.

**Reading** — Enable rows with the checkbox, then click **Read Selected** for a one-shot read, or **Start Cyclic** to poll continuously at the specified interval. Contiguous registers of the same type are automatically coalesced into a single Modbus request for efficiency. Each row shows a green or red status dot, and errors are logged with full detail.

**Writing** — Type a value in the Write Value column and click **W** to write a single row, or **Write All** to write every row that has a value entered (with confirmation). After every write, the tool automatically reads back the written registers to verify.

**Binary view** — Click the **BIN** button in the toolbar to toggle the Value column between decimal and binary display. Double-click any value cell to see a popup with decimal, hex, binary, signed, and unsigned representations.

**Configuration** — Save and load register configurations as JSON files. The current session auto-saves on exit and auto-loads on startup. Config files are stored next to the application.

**Transaction log** — The bottom panel shows every Modbus transaction with timestamps, function codes, raw register values, and decoded results. Toggle visibility with the **Log** button. Use Copy to grab the full log for sharing.

## Discovery Tab

Scan a network to find devices. The subnet dropdown auto-detects local interfaces and VPN/routed subnets from the routing table.

Scan methods (all optional, run in sequence):

- **Ping Sweep** — ICMP ping every IP in the subnet
- **Port Scan** — Probe ports 502, 28784, 25425, 44818, 80, 443 to fingerprint device roles
- **EtherNet/IP** — Broadcast ListIdentity to find EIP-capable devices (local networks only)
- **Modbus ID** — FC43 device identification on any device with port 502 open

On local networks, MAC addresses are resolved via ARP and matched against a built-in vendor database (AutomationDirect, Rockwell, Siemens, Schneider, ABB, Beckhoff, and others). On remote/VPN networks, ARP and broadcasts are skipped automatically since they don't work across routed connections.

Click any discovered device to see full details. **Use in Modbus Tab** populates the connection bar and connects in one click.
