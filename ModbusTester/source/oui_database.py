"""
MAC OUI (Organizationally Unique Identifier) lookup for industrial devices.
Maps the first 3 bytes of a MAC address to a vendor name.
Focused on industrial automation, networking, and embedded device vendors.
"""

OUI_TABLE = {
    # ── AutomationDirect ────────────────────────────────
    "00:90:E8": "AutomationDirect",
    # ── Rockwell / Allen-Bradley ────────────────────────
    "00:00:BC": "Rockwell Automation",
    "00:1D:9C": "Rockwell Automation",
    "00:0C:DF": "Rockwell / Allen-Bradley",
    "AC:64:17": "Rockwell Automation",
    "40:9C:28": "Rockwell Automation",
    "E8:4E:06": "Rockwell Automation",
    "28:CD:C1": "Rockwell Automation",
    # ── Siemens ─────────────────────────────────────────
    "00:0E:8C": "Siemens",
    "00:1B:1B": "Siemens",
    "00:1C:06": "Siemens",
    "08:00:06": "Siemens",
    "A8:F7:E0": "Siemens",
    "4C:E1:75": "Siemens",
    "90:02:A9": "Siemens",
    "D8:D7:26": "Siemens",
    # ── Schneider Electric / Modicon ────────────────────
    "00:00:54": "Schneider Electric",
    "00:80:F4": "Schneider Electric (Telemecanique)",
    "CC:3F:1D": "Schneider Electric",
    "00:60:E9": "Schneider Electric",
    # ── ABB ─────────────────────────────────────────────
    "00:04:A5": "ABB",
    "00:20:D3": "ABB",
    "3C:61:04": "ABB",
    # ── Beckhoff ────────────────────────────────────────
    "00:01:05": "Beckhoff Automation",
    # ── Phoenix Contact ─────────────────────────────────
    "00:A0:45": "Phoenix Contact",
    "EC:E5:13": "Phoenix Contact",
    # ── Wago ────────────────────────────────────────────
    "00:30:DE": "Wago",
    # ── Moxa ────────────────────────────────────────────
    "00:90:E8": "AutomationDirect",  # Moxa uses many, AD uses this one
    "00:90:E8": "AutomationDirect",
    # ── B&R ─────────────────────────────────────────────
    "00:60:65": "B&R Industrial Automation",
    # ── Omron ───────────────────────────────────────────
    "00:00:0E": "Omron",
    # ── Mitsubishi ──────────────────────────────────────
    "00:01:CE": "Mitsubishi Electric",
    "00:0E:F3": "Mitsubishi Electric",
    # ── Keyence ─────────────────────────────────────────
    "00:01:1A": "Keyence",
    # ── Turck ───────────────────────────────────────────
    "00:07:46": "Turck",
    # ── Advantech ────────────────────────────────────────
    "00:D0:C9": "Advantech",
    "00:1E:C0": "Advantech",
    # ── Red Lion ────────────────────────────────────────
    "00:0E:7B": "Red Lion Controls",
    # ── Weidmuller ──────────────────────────────────────
    "00:10:72": "Weidmuller",
    # ── HMS (Anybus) ────────────────────────────────────
    "00:30:11": "HMS Industrial Networks",
    # ── IDEC ────────────────────────────────────────────
    "00:06:E3": "IDEC",
    # ── Digi International ──────────────────────────────
    "00:40:9D": "Digi International",
    # ── Raspberry Pi ────────────────────────────────────
    "B8:27:EB": "Raspberry Pi",
    "DC:A6:32": "Raspberry Pi",
    "D8:3A:DD": "Raspberry Pi",
    "E4:5F:01": "Raspberry Pi",
    # ── Common networking vendors ────────────────────────
    "00:1A:2B": "Cisco",
    "00:17:C5": "Cisco",
    "00:09:7C": "Cisco",
    "00:24:D7": "Cisco",
    "00:0C:29": "VMware",
    "00:50:56": "VMware",
    "D4:BE:D9": "Dell",
    "18:66:DA": "Dell",
    "48:2A:E3": "Netgear",
    "C4:3D:C7": "Netgear",
    "3C:37:86": "Netgear",
    "00:24:B2": "Netgear",
    "D8:07:B6": "TP-Link",
    "50:C7:BF": "TP-Link",
    "A0:F3:C1": "TP-Link",
    "60:E3:27": "TP-Link",
}


def lookup_mac_vendor(mac: str) -> str:
    """Look up the vendor for a MAC address.
    Accepts formats: '00:90:E8:xx:xx:xx', '00-90-E8-xx-xx-xx',
    '0090E8xxxxxx'
    Returns vendor name or 'Unknown'.
    """
    clean = mac.upper().replace("-", ":").replace(".", ":")
    # Handle no-separator format
    if ":" not in clean and len(clean) >= 6:
        clean = f"{clean[0:2]}:{clean[2:4]}:{clean[4:6]}"
    prefix = clean[:8]
    return OUI_TABLE.get(prefix, "Unknown")
