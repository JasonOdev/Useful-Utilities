"""
Network discovery engine for finding industrial devices.
Runs scans in background threads and emits results via Qt signals.
"""
import logging
import platform
import re
import socket
import struct
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from PySide6.QtCore import QObject, Signal

from oui_database import lookup_mac_vendor

log = logging.getLogger("modbus_tester")

# Ports to probe on each discovered IP
PROBE_PORTS = {
    502:   "Modbus TCP",
    28784: "Do-More Protocol",
    25425: "Click Programming",
    44818: "EtherNet/IP",
    80:    "HTTP",
    443:   "HTTPS",
    8080:  "Alt HTTP",
}


@dataclass
class DiscoveredDevice:
    ip: str = ""
    mac: str = ""
    vendor: str = ""
    open_ports: dict = field(default_factory=dict)
    hostname: str = ""
    # EtherNet/IP identity
    eip_vendor_id: int = 0
    eip_device_type: int = 0
    eip_product_name: str = ""
    eip_serial: str = ""
    eip_revision: str = ""
    # Modbus device ID
    modbus_vendor: str = ""
    modbus_product: str = ""
    modbus_revision: str = ""

    @property
    def protocols(self) -> str:
        parts = []
        if 502 in self.open_ports:
            parts.append("Modbus")
        if 44818 in self.open_ports or self.eip_product_name:
            parts.append("EtherNet/IP")
        if 28784 in self.open_ports:
            parts.append("Do-More")
        if 25425 in self.open_ports:
            parts.append("Click")
        if 80 in self.open_ports or 443 in self.open_ports:
            parts.append("HTTP")
        return ", ".join(parts)

    @property
    def device_label(self) -> str:
        """Best-guess description of the device."""
        if self.eip_product_name:
            return self.eip_product_name
        if self.modbus_product:
            return self.modbus_product
        if self.vendor and self.vendor != "Unknown":
            return self.vendor
        return ""


# ════════════════════════════════════════════════════════════════
#  Network utilities
# ════════════════════════════════════════════════════════════════

def get_local_interfaces() -> list[dict]:
    """Return list of {'ip': ..., 'subnet': ...} for local interfaces."""
    results = []
    try:
        hostname = socket.gethostname()
        addrs = socket.getaddrinfo(hostname, None, socket.AF_INET)
        seen = set()
        for info in addrs:
            ip = info[4][0]
            if ip.startswith("127.") or ip in seen:
                continue
            seen.add(ip)
            # Guess /24 subnet
            parts = ip.split(".")
            subnet = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
            results.append({"ip": ip, "subnet": subnet})
    except Exception as exc:
        log.error(f"Interface detection error: {exc}")
    if not results:
        results.append({"ip": "192.168.1.1", "subnet": "192.168.1.0/24"})
    return results


def _parse_subnet(subnet_str: str) -> list[str]:
    """Parse a CIDR subnet into a list of host IPs.
    Only supports /24 and /16 (capped at 254 hosts for speed)."""
    subnet_str = subnet_str.strip()
    if "/" not in subnet_str:
        # Single IP
        return [subnet_str]
    base, bits_str = subnet_str.rsplit("/", 1)
    bits = int(bits_str)
    parts = base.split(".")
    if bits == 24:
        return [f"{parts[0]}.{parts[1]}.{parts[2]}.{i}" for i in range(1, 255)]
    elif bits == 16:
        # Return just 254 IPs in the last octet to keep scans reasonable
        return [f"{parts[0]}.{parts[1]}.{parts[2]}.{i}" for i in range(1, 255)]
    else:
        return [f"{parts[0]}.{parts[1]}.{parts[2]}.{i}" for i in range(1, 255)]


def _ping_one(ip: str, timeout_ms: int = 500) -> bool:
    """Ping a single IP. Returns True if reachable."""
    try:
        if platform.system().lower() == "windows":
            cmd = ["ping", "-n", "1", "-w", str(timeout_ms), ip]
        else:
            timeout_s = max(1, timeout_ms // 1000)
            cmd = ["ping", "-c", "1", "-W", str(timeout_s), ip]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_ms / 1000 + 2
        )
        return result.returncode == 0
    except Exception:
        return False


def _read_arp_table() -> dict[str, str]:
    """Read the system ARP table. Returns {ip: mac}."""
    arp_map = {}
    try:
        result = subprocess.run(
            ["arp", "-a"], capture_output=True, text=True, timeout=5
        )
        log.debug(f"ARP raw output ({len(result.stdout)} chars):\n{result.stdout[:500]}")
        for line in result.stdout.splitlines():
            # Windows: "  192.168.1.1          00-90-e8-3c-a1-02     dynamic"
            # Also match MACs with colons or dashes
            m = re.search(
                r'(\d+\.\d+\.\d+\.\d+)\s+'
                r'([\da-fA-F]{2}[:-][\da-fA-F]{2}[:-][\da-fA-F]{2}[:-]'
                r'[\da-fA-F]{2}[:-][\da-fA-F]{2}[:-][\da-fA-F]{2})',
                line
            )
            if m:
                ip = m.group(1)
                mac = m.group(2).replace("-", ":").upper()
                if mac != "FF:FF:FF:FF:FF:FF":
                    arp_map[ip] = mac
                continue
            # Linux: "? (192.168.1.1) at 00:90:e8:3c:a1:02 [ether] on eth0"
            m = re.search(
                r'\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+'
                r'([\da-fA-F]{2}:[\da-fA-F]{2}:[\da-fA-F]{2}:'
                r'[\da-fA-F]{2}:[\da-fA-F]{2}:[\da-fA-F]{2})',
                line
            )
            if m:
                ip, mac = m.group(1), m.group(2).upper()
                arp_map[ip] = mac
        log.info(f"ARP table: {len(arp_map)} entries found")
    except Exception as exc:
        log.error(f"ARP read error: {exc}")
    return arp_map


def _arp_lookup_single(ip: str) -> str:
    """Try to get MAC for a single IP via arp command. Fallback method."""
    try:
        if platform.system().lower() == "windows":
            result = subprocess.run(
                ["arp", "-a", ip], capture_output=True, text=True, timeout=3
            )
        else:
            result = subprocess.run(
                ["arp", "-n", ip], capture_output=True, text=True, timeout=3
            )
        m = re.search(
            r'([\da-fA-F]{2}[:-][\da-fA-F]{2}[:-][\da-fA-F]{2}[:-]'
            r'[\da-fA-F]{2}[:-][\da-fA-F]{2}[:-][\da-fA-F]{2})',
            result.stdout
        )
        if m:
            mac = m.group(1).replace("-", ":").upper()
            if mac != "FF:FF:FF:FF:FF:FF":
                return mac
    except Exception:
        pass
    return ""


def _probe_tcp_port(ip: str, port: int, timeout: float = 0.5) -> bool:
    """Check if a TCP port is open."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def _probe_udp_port(ip: str, port: int, timeout: float = 0.5) -> bool:
    """Send a small UDP probe and see if we get a response.
    Not reliable for all services, but a best effort."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(b'\x00' * 4, (ip, port))
        try:
            sock.recvfrom(1024)
            sock.close()
            return True
        except socket.timeout:
            sock.close()
            return False
    except Exception:
        return False


def _reverse_dns(ip: str) -> str:
    """Try reverse DNS lookup."""
    try:
        host, _, _ = socket.gethostbyaddr(ip)
        return host
    except Exception:
        return ""


# ════════════════════════════════════════════════════════════════
#  EtherNet/IP ListIdentity
# ════════════════════════════════════════════════════════════════

def _eip_list_identity_broadcast(timeout: float = 2.0) -> list[DiscoveredDevice]:
    """Send EtherNet/IP ListIdentity broadcast and collect responses."""
    # Encapsulation header: Command=0x0063, Length=0, Session=0, Status=0, Context=0, Options=0
    packet = struct.pack('<HH I I 8s I',
                         0x0063, 0x0000,
                         0x00000000, 0x00000000,
                         b'\x00' * 8, 0x00000000)
    devices = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(timeout)
        sock.sendto(packet, ('255.255.255.255', 44818))
        log.info("EIP ListIdentity broadcast sent on port 44818")

        end_time = time.time() + timeout
        while time.time() < end_time:
            try:
                data, addr = sock.recvfrom(4096)
                dev = _parse_eip_identity(data, addr[0])
                if dev:
                    devices.append(dev)
                    log.info(f"EIP response from {addr[0]}: {dev.eip_product_name}")
            except socket.timeout:
                break
            except Exception as exc:
                log.debug(f"EIP parse error: {exc}")
        sock.close()
    except Exception as exc:
        log.error(f"EIP broadcast error: {exc}")
    return devices


def _parse_eip_identity(data: bytes, ip: str) -> DiscoveredDevice | None:
    """Parse an EtherNet/IP ListIdentity response."""
    if len(data) < 26:
        return None

    try:
        # Encapsulation header (24 bytes)
        cmd, length = struct.unpack_from('<HH', data, 0)
        if cmd != 0x0063:
            return None

        # Item count at offset 24
        item_count = struct.unpack_from('<H', data, 24)[0]
        if item_count < 1:
            return None

        # First item: type (2) + length (2) = 4 bytes header at offset 26
        item_type, item_len = struct.unpack_from('<HH', data, 26)

        # Identity data starts at offset 30
        pos = 30

        # Protocol version (2)
        pos += 2

        # Socket address: family(2) + port(2) + ip(4) + zero(8) = 16 bytes
        pos += 16

        # Vendor ID (2), Device Type (2), Product Code (2)
        vendor_id, device_type, product_code = struct.unpack_from('<HHH', data, pos)
        pos += 6

        # Revision major (1), minor (1)
        rev_major, rev_minor = struct.unpack_from('BB', data, pos)
        pos += 2

        # Status (2)
        pos += 2

        # Serial Number (4)
        serial = struct.unpack_from('<I', data, pos)[0]
        pos += 4

        # Product Name: length (1) + string
        name_len = struct.unpack_from('B', data, pos)[0]
        pos += 1
        product_name = data[pos:pos + name_len].decode('ascii', errors='replace')

        dev = DiscoveredDevice(ip=ip)
        dev.eip_vendor_id = vendor_id
        dev.eip_device_type = device_type
        dev.eip_product_name = product_name.strip()
        dev.eip_serial = f"{serial:08X}"
        dev.eip_revision = f"{rev_major}.{rev_minor}"
        dev.open_ports[44818] = "EtherNet/IP"
        return dev

    except Exception as exc:
        log.debug(f"EIP identity parse error for {ip}: {exc}")
        return None


# ════════════════════════════════════════════════════════════════
#  Modbus Device Identification (FC43 / MEI 14)
# ════════════════════════════════════════════════════════════════

def _modbus_read_device_id(ip: str, port: int = 502,
                           timeout: float = 1.0) -> dict:
    """Try Modbus FC43 (Read Device Identification) on a device.
    Returns dict with 'vendor', 'product', 'revision' or empty dict."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))

        # MBAP header + FC43 request
        # Transaction ID(2) + Protocol ID(2) + Length(2) + Unit ID(1)
        # + FC(1) + MEI Type(1) + Read ID Code(1) + Object ID(1)
        transaction_id = 0x0001
        protocol_id = 0x0000
        length = 5  # Unit(1) + FC(1) + MEI(1) + DevIdCode(1) + ObjId(1)
        unit_id = 1

        request = struct.pack('>HHH B B B B B',
                              transaction_id, protocol_id, length,
                              unit_id,
                              0x2B,  # FC43
                              0x0E,  # MEI type 14
                              0x01,  # Basic device identification
                              0x00)  # Start at object 0
        sock.send(request)

        response = sock.recv(256)
        sock.close()

        if len(response) < 12:
            return {}

        # Parse MBAP + response
        # Skip MBAP (7 bytes) + FC(1) + MEI(1) + ReadDevIdCode(1)
        # + ConformityLevel(1) + MoreFollows(1) + NextObjId(1)
        # + NumberOfObjects(1)
        offset = 7
        fc = response[offset]
        if fc & 0x80:  # Exception response
            return {}
        if fc != 0x2B:
            return {}

        # offset 8: MEI type, 9: read dev id code, 10: conformity,
        # 11: more follows, 12: next obj id, 13: num objects
        if len(response) < 14:
            return {}
        num_objects = response[13]

        objects = {}
        pos = 14
        for _ in range(num_objects):
            if pos + 2 > len(response):
                break
            obj_id = response[pos]
            obj_len = response[pos + 1]
            pos += 2
            if pos + obj_len > len(response):
                break
            obj_val = response[pos:pos + obj_len].decode('ascii', errors='replace')
            objects[obj_id] = obj_val.strip()
            pos += obj_len

        return {
            "vendor":   objects.get(0, ""),
            "product":  objects.get(1, ""),
            "revision": objects.get(2, ""),
        }

    except Exception as exc:
        log.debug(f"Modbus FC43 failed for {ip}: {exc}")
        return {}


# ════════════════════════════════════════════════════════════════
#  Discovery Worker (runs in background thread)
# ════════════════════════════════════════════════════════════════

class DiscoveryWorker(QObject):
    """Runs network discovery in a background thread."""
    # Signals
    device_found = Signal(object)       # DiscoveredDevice
    device_updated = Signal(object)     # DiscoveredDevice (with new info)
    progress = Signal(int, int, str)    # current, total, phase_description
    scan_finished = Signal(float)       # elapsed_ms
    log_message = Signal(str, str)      # message, level

    def __init__(self, subnet: str, do_ping: bool = True,
                 do_ports: bool = True, do_eip: bool = True,
                 do_modbus_id: bool = True, ping_timeout_ms: int = 500,
                 max_threads: int = 50):
        super().__init__()
        self.subnet = subnet
        self.do_ping = do_ping
        self.do_ports = do_ports
        self.do_eip = do_eip
        self.do_modbus_id = do_modbus_id
        self.ping_timeout_ms = ping_timeout_ms
        self.max_threads = max_threads
        self._cancelled = False
        self.devices: dict[str, DiscoveredDevice] = {}

    def cancel(self):
        self._cancelled = True

    def run(self):
        t0 = time.perf_counter()
        log.info(f"═══ Discovery scan started: {self.subnet} ═══")

        # Phase 1: EtherNet/IP broadcast (fast, do it first)
        if self.do_eip and not self._cancelled:
            self.progress.emit(0, 100, "Sending EtherNet/IP broadcast...")
            eip_devices = _eip_list_identity_broadcast(timeout=2.0)
            for dev in eip_devices:
                self.devices[dev.ip] = dev
                self.device_found.emit(dev)

        if self._cancelled:
            self.scan_finished.emit(0)
            return

        # Phase 2: Ping sweep
        alive_ips = set(self.devices.keys())
        if self.do_ping:
            ips = _parse_subnet(self.subnet)
            total = len(ips)
            self.progress.emit(0, total, "Ping sweep...")

            with ThreadPoolExecutor(max_workers=self.max_threads) as pool:
                futures = {pool.submit(_ping_one, ip, self.ping_timeout_ms): ip
                           for ip in ips}
                done = 0
                for future in as_completed(futures):
                    if self._cancelled:
                        pool.shutdown(wait=False, cancel_futures=True)
                        self.scan_finished.emit(0)
                        return
                    ip = futures[future]
                    done += 1
                    if done % 10 == 0:
                        self.progress.emit(done, total, f"Ping sweep... {done}/{total}")
                    try:
                        if future.result():
                            alive_ips.add(ip)
                            if ip not in self.devices:
                                dev = DiscoveredDevice(ip=ip)
                                self.devices[ip] = dev
                                self.device_found.emit(dev)
                    except Exception:
                        pass

            log.info(f"Ping sweep complete: {len(alive_ips)} hosts found")

        if self._cancelled:
            self.scan_finished.emit(0)
            return

        # Phase 3: ARP table → MAC + vendor lookup
        self.progress.emit(0, 1, "Reading ARP table...")
        # Brief pause to let the OS ARP cache settle after the ping sweep
        time.sleep(1.0)
        arp_table = _read_arp_table()
        missing_mac = []
        for ip, dev in self.devices.items():
            mac = arp_table.get(ip, "")
            if mac:
                dev.mac = mac
                dev.vendor = lookup_mac_vendor(mac)
                self.device_updated.emit(dev)
            else:
                missing_mac.append(ip)

        # Fallback: try per-IP ARP lookup for any we missed
        if missing_mac:
            log.info(f"ARP fallback: looking up {len(missing_mac)} device(s) individually")
            for ip in missing_mac:
                if self._cancelled:
                    break
                mac = _arp_lookup_single(ip)
                if mac:
                    dev = self.devices[ip]
                    dev.mac = mac
                    dev.vendor = lookup_mac_vendor(mac)
                    self.device_updated.emit(dev)

        # Phase 4: Port probing
        if self.do_ports and not self._cancelled:
            alive_list = list(alive_ips)
            total = len(alive_list)
            self.progress.emit(0, total, "Port scanning...")

            for idx, ip in enumerate(alive_list):
                if self._cancelled:
                    break
                self.progress.emit(idx + 1, total, f"Port scan {ip}")
                dev = self.devices.get(ip)
                if not dev:
                    continue

                for port, name in PROBE_PORTS.items():
                    if self._cancelled:
                        break
                    if port in (80, 443, 502, 44818, 8080):
                        # TCP ports
                        if _probe_tcp_port(ip, port, timeout=0.3):
                            dev.open_ports[port] = name
                    else:
                        # UDP ports (28784, 25425)
                        if _probe_udp_port(ip, port, timeout=0.3):
                            dev.open_ports[port] = name

                # Try hostname
                dev.hostname = _reverse_dns(ip)
                self.device_updated.emit(dev)

            log.info("Port scanning complete")

        # Phase 5: Modbus Device ID on port-502 devices
        if self.do_modbus_id and not self._cancelled:
            modbus_ips = [ip for ip, dev in self.devices.items()
                          if 502 in dev.open_ports]
            total = len(modbus_ips)
            if total:
                self.progress.emit(0, total, "Modbus identification...")
                for idx, ip in enumerate(modbus_ips):
                    if self._cancelled:
                        break
                    self.progress.emit(idx + 1, total, f"Modbus ID {ip}")
                    dev = self.devices[ip]
                    info = _modbus_read_device_id(ip)
                    if info:
                        dev.modbus_vendor = info.get("vendor", "")
                        dev.modbus_product = info.get("product", "")
                        dev.modbus_revision = info.get("revision", "")
                        self.device_updated.emit(dev)

        elapsed = (time.perf_counter() - t0) * 1000
        log.info(f"═══ Discovery complete: {len(self.devices)} device(s) "
                 f"in {elapsed:.0f} ms ═══")
        self.progress.emit(1, 1, "Done")
        self.scan_finished.emit(elapsed)
