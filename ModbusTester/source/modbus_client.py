"""
Communication backends for Modbus TCP (and future protocols).
Uses struct directly for encoding/decoding — no dependency on pymodbus.payload
which was removed in pymodbus 3.7+.

All transactions are logged via Python's logging module under the
'modbus_tester' logger so the GUI can capture and display them.
"""
import logging
import struct
import traceback
from abc import ABC, abstractmethod
from pymodbus.client import ModbusTcpClient

log = logging.getLogger("modbus_tester")


# ── Modbus function code names (for readable logs) ─────────────
FC_NAMES = {
    1: "Read Coils (FC01)",
    2: "Read Discrete Inputs (FC02)",
    3: "Read Holding Registers (FC03)",
    4: "Read Input Registers (FC04)",
    5: "Write Single Coil (FC05)",
    6: "Write Single Register (FC06)",
    15: "Write Multiple Coils (FC15)",
    16: "Write Multiple Registers (FC16)",
}

# ── Byte-order helpers ──────────────────────────────────────────

BYTE_ORDER_LABELS = {
    "big":        "Big Endian (AB CD)",
    "little":     "Little Endian (DC BA)",
    "mid_big":    "Mid-Big / Word Swap (CD AB)",
    "mid_little": "Mid-Little / Byte Swap (BA DC)",
}


def _regs_to_bytes(regs: list[int], byte_order: str) -> bytes:
    """Convert 16-bit register values to bytes with byte/word ordering."""
    raw = struct.pack(">" + "H" * len(regs), *regs)
    if byte_order == "big":
        return raw
    elif byte_order == "little":
        return raw[::-1]
    elif byte_order == "mid_big":
        words = [raw[i : i + 2] for i in range(0, len(raw), 2)]
        return b"".join(reversed(words))
    elif byte_order == "mid_little":
        out = bytearray()
        for i in range(0, len(raw), 2):
            out.append(raw[i + 1])
            out.append(raw[i])
        return bytes(out)
    return raw


def _bytes_to_regs(data: bytes, byte_order: str) -> list[int]:
    """Reverse of _regs_to_bytes."""
    if byte_order == "big":
        raw = data
    elif byte_order == "little":
        raw = data[::-1]
    elif byte_order == "mid_big":
        words = [data[i : i + 2] for i in range(0, len(data), 2)]
        raw = b"".join(reversed(words))
    elif byte_order == "mid_little":
        out = bytearray()
        for i in range(0, len(data), 2):
            out.append(data[i + 1])
            out.append(data[i])
        raw = bytes(out)
    else:
        raw = data
    return list(struct.unpack(">" + "H" * (len(raw) // 2), raw))


def _hex(data: bytes) -> str:
    """Format bytes as hex string for logging."""
    return " ".join(f"{b:02X}" for b in data)


def _regs_hex(regs: list[int]) -> str:
    """Format register values as hex for logging."""
    return " ".join(f"0x{r:04X}" for r in regs)


# ── Register metadata ──────────────────────────────────────────

REG_COUNTS = {
    "BOOL": 1, "UINT16": 1, "INT16": 1,
    "UINT32": 2, "INT32": 2, "FLOAT32": 2,
    # ASCII is variable-length; reg_count comes from the row config, not here.
    # A sentinel of 1 is stored so callers that need a fallback don't crash.
    "ASCII": 1,
}

DATA_TYPES = list(REG_COUNTS.keys())

REG_TYPES = {
    "coil":     {"prefix": "0", "label": "Coil (0x)",             "writable": True,  "fc_read": 1, "fc_write": 5},
    "discrete": {"prefix": "1", "label": "Discrete Input (1x)",   "writable": False, "fc_read": 2, "fc_write": None},
    "input":    {"prefix": "3", "label": "Input Register (3x)",   "writable": False, "fc_read": 4, "fc_write": None},
    "holding":  {"prefix": "4", "label": "Holding Register (4x)", "writable": True,  "fc_read": 3, "fc_write": 6},
}


def regs_to_bytes_ordered(regs: list[int], byte_order: str) -> bytes:
    """Public wrapper around _regs_to_bytes for use in the GUI."""
    return _regs_to_bytes(regs, byte_order)


def compute_address_str(reg_type: str, offset: int) -> str:
    prefix = REG_TYPES.get(reg_type, {}).get("prefix", "?")
    return f"{prefix}{offset + 1:04d}"


def is_writable(reg_type: str) -> bool:
    return REG_TYPES.get(reg_type, {}).get("writable", False)


# ── Decode / encode helpers ─────────────────────────────────────

def _decode_registers(registers: list[int], data_type: str,
                      byte_order: str, reg_count: int = 0):
    if data_type == "ASCII":
        # Each 16-bit register holds two ASCII bytes (high byte first).
        # reg_count overrides len(registers) if provided.
        count = reg_count if reg_count > 0 else len(registers)
        raw = b"".join(struct.pack(">H", r) for r in registers[:count])
        # Decode, stopping at the first null byte.
        text = raw.split(b"\x00")[0].decode("ascii", errors="replace")
        return text
    if data_type == "UINT16":
        return registers[0]
    elif data_type == "INT16":
        raw = struct.pack(">H", registers[0])
        return struct.unpack(">h", raw)[0]
    else:
        raw = _regs_to_bytes(registers[:2], byte_order)
        fmt_map = {"UINT32": ">I", "INT32": ">i", "FLOAT32": ">f"}
        return struct.unpack(fmt_map[data_type], raw)[0]


def _encode_value(value, data_type: str, byte_order: str) -> list[int]:
    if data_type == "BOOL":
        return [1 if str(value).strip().lower() in ("true", "1", "on", "yes") else 0]
    elif data_type == "UINT16":
        return [int(value) & 0xFFFF]
    elif data_type == "INT16":
        raw = struct.pack(">h", int(value))
        return [struct.unpack(">H", raw)[0]]
    else:
        fmt_map = {"UINT32": ">I", "INT32": ">i", "FLOAT32": ">f"}
        if data_type == "FLOAT32":
            raw = struct.pack(fmt_map[data_type], float(value))
        else:
            raw = struct.pack(fmt_map[data_type], int(value))
        return _bytes_to_regs(raw, byte_order)


# ════════════════════════════════════════════════════════════════
#  Abstract base
# ════════════════════════════════════════════════════════════════

class CommunicationBackend(ABC):
    @abstractmethod
    def connect(self, **kwargs) -> bool: ...
    @abstractmethod
    def disconnect(self): ...
    @abstractmethod
    def is_connected(self) -> bool: ...
    @abstractmethod
    def read_item(self, reg_type: str, offset: int, data_type: str,
                  byte_order: str = "big"): ...
    @abstractmethod
    def write_item(self, reg_type: str, offset: int, data_type: str,
                   byte_order: str, value) -> bool: ...


# ════════════════════════════════════════════════════════════════
#  Modbus TCP
# ════════════════════════════════════════════════════════════════

class ModbusTcpBackend(CommunicationBackend):

    def __init__(self):
        self._client: ModbusTcpClient | None = None
        self._unit_id: int = 1
        self._host: str = ""
        self._port: int = 502

    # ── connection ──────────────────────────────────────────────
    def connect(self, host="127.0.0.1", port=502, unit_id=1,
                timeout=3, **_kw) -> bool:
        self.disconnect()
        self._host = host
        self._port = port
        self._unit_id = unit_id

        log.info(f"CONNECT  → {host}:{port}  unit_id={unit_id}  timeout={timeout}s")
        try:
            # pymodbus 3.12+: slave/unit is set on the client, not per-call
            self._client = ModbusTcpClient(host, port=port, timeout=timeout)
            self._client.slave = unit_id
            result = self._client.connect()
            if result:
                log.info(f"CONNECT  ✓ Connected to {host}:{port}")
            else:
                log.error(f"CONNECT  ✗ Connection refused — {host}:{port}")
            return result
        except Exception as exc:
            log.error(f"CONNECT  ✗ Exception: {exc}")
            log.debug(traceback.format_exc())
            return False

    def disconnect(self):
        if self._client:
            log.info(f"DISCONNECT  Closing connection to {self._host}:{self._port}")
            self._client.close()
            self._client = None

    def is_connected(self) -> bool:
        if self._client is None:
            return False
        try:
            return self._client.is_socket_open()
        except Exception:
            return False

    # ── read ────────────────────────────────────────────────────
    def read_item(self, reg_type, offset, data_type, byte_order="big",
                  reg_count: int = 0):
        if not self.is_connected():
            log.error("READ  ✗ Not connected")
            raise ConnectionError("Not connected")

        # For ASCII, reg_count is supplied by the caller; for all others use REG_COUNTS.
        count = reg_count if (data_type == "ASCII" and reg_count > 0) else REG_COUNTS.get(data_type, 1)
        uid = self._unit_id
        fc = REG_TYPES.get(reg_type, {}).get("fc_read", "?")
        fc_name = FC_NAMES.get(fc, f"FC{fc}")
        addr_str = compute_address_str(reg_type, offset)

        log.info(
            f"READ  → {fc_name}  addr={addr_str}  "
            f"offset={offset}  count={count}  unit={uid}  "
            f"type={data_type}  byte_order={byte_order}"
        )

        try:
            if reg_type == "coil":
                rr = self._client.read_coils(offset, count=count)
            elif reg_type == "discrete":
                rr = self._client.read_discrete_inputs(offset, count=count)
            elif reg_type == "input":
                rr = self._client.read_input_registers(offset, count=count)
            elif reg_type == "holding":
                rr = self._client.read_holding_registers(offset, count=count)
            else:
                raise ValueError(f"Unknown register type: {reg_type}")
        except Exception as exc:
            log.error(f"READ  ✗ Transport error: {type(exc).__name__}: {exc}")
            log.debug(traceback.format_exc())
            raise

        # Check for Modbus error response
        if rr.isError():
            # Try to extract exception code for more detail
            error_detail = str(rr)
            log.error(f"READ  ✗ Modbus error response: {error_detail}")
            raise IOError(f"Modbus error: {error_detail}")

        # Successful read — log raw data
        if reg_type in ("coil", "discrete"):
            bits = rr.bits[:count]
            val = bool(bits[0])
            log.info(f"READ  ✓ Raw bits: {bits}  →  decoded: {val}")
            return val

        raw_regs = rr.registers
        log.info(f"READ  ✓ Raw registers: {_regs_hex(raw_regs)}  (decimal: {raw_regs})")

        value = _decode_registers(raw_regs, data_type, byte_order, reg_count=count)

        if count > 1:
            raw_bytes = _regs_to_bytes(raw_regs[:2], byte_order)
            log.info(
                f"READ     Decoded {data_type}: regs {_regs_hex(raw_regs[:2])}  "
                f"→  bytes [{_hex(raw_bytes)}]  →  value = {value}"
            )
        else:
            log.info(f"READ     Decoded {data_type}: {value}")

        return value

    # ── bulk read (for coalescing) ──────────────────────────────
    def read_bulk(self, reg_type, start_offset, count):
        """Read `count` raw 16-bit registers starting at `start_offset`.
        Returns a list of int register values."""
        if not self.is_connected():
            raise ConnectionError("Not connected")

        addr_str = compute_address_str(reg_type, start_offset)
        log.info(
            f"BULK READ → {reg_type}  start={addr_str}  "
            f"offset={start_offset}  count={count}"
        )

        try:
            if reg_type == "coil":
                rr = self._client.read_coils(start_offset, count=count)
            elif reg_type == "discrete":
                rr = self._client.read_discrete_inputs(start_offset, count=count)
            elif reg_type == "input":
                rr = self._client.read_input_registers(start_offset, count=count)
            elif reg_type == "holding":
                rr = self._client.read_holding_registers(start_offset, count=count)
            else:
                raise ValueError(f"Unknown register type: {reg_type}")
        except Exception as exc:
            log.error(f"BULK READ ✗ {type(exc).__name__}: {exc}")
            raise

        if rr.isError():
            log.error(f"BULK READ ✗ {rr}")
            raise IOError(f"Modbus error: {rr}")

        if reg_type in ("coil", "discrete"):
            result = rr.bits[:count]
            log.info(f"BULK READ ✓ {count} bits: {result}")
            return result

        log.info(f"BULK READ ✓ {count} regs: {_regs_hex(rr.registers)}")
        return rr.registers

    # ── write ───────────────────────────────────────────────────
    def write_item(self, reg_type, offset, data_type, byte_order, value):
        if not self.is_connected():
            log.error("WRITE ✗ Not connected")
            raise ConnectionError("Not connected")

        uid = self._unit_id
        addr_str = compute_address_str(reg_type, offset)

        log.info(
            f"WRITE → addr={addr_str}  offset={offset}  unit={uid}  "
            f"type={data_type}  value={value!r}  byte_order={byte_order}"
        )

        try:
            if reg_type == "coil":
                bool_val = str(value).strip().lower() in ("true", "1", "on", "yes")
                log.info(f"WRITE   {FC_NAMES[5]}  offset={offset}  value={bool_val}")
                rr = self._client.write_coil(offset, value=bool_val)

            elif reg_type == "holding":
                regs = _encode_value(value, data_type, byte_order)
                log.info(
                    f"WRITE   Encoded: {value!r} → registers {_regs_hex(regs)}  "
                    f"(decimal: {regs})"
                )
                if len(regs) == 1:
                    log.info(f"WRITE   {FC_NAMES[6]}  offset={offset}  value={regs[0]}")
                    rr = self._client.write_register(offset, value=regs[0])
                else:
                    log.info(
                        f"WRITE   {FC_NAMES[16]}  offset={offset}  "
                        f"count={len(regs)}  values={regs}"
                    )
                    rr = self._client.write_registers(offset, values=regs)
            else:
                log.error(f"WRITE ✗ Cannot write to {reg_type} registers")
                raise ValueError(f"Cannot write to {reg_type} registers")

        except (ConnectionError, ValueError):
            raise
        except Exception as exc:
            log.error(f"WRITE ✗ Transport error: {type(exc).__name__}: {exc}")
            log.debug(traceback.format_exc())
            raise

        if rr.isError():
            error_detail = str(rr)
            log.error(f"WRITE ✗ Modbus error response: {error_detail}")
            raise IOError(f"Modbus write error: {error_detail}")

        log.info(f"WRITE ✓ Success")
        return True
