"""
preprocessing/pcap_parser.py
============================
Wraps tshark to extract per-packet metadata from a pcap file.
Output: list of dicts with keys {ts, size, direction}
direction: +1 = outbound (UP), -1 = inbound (DOWN)
Determined by comparing src IP to the known local IP of the capture point.
IPs are intentionally dropped from output — the ML pipeline never sees them.
"""

import subprocess
from typing import Optional


def extract_packets(pcap_path: str,
                    local_ip: Optional[str] = None) -> list[dict]:
    """
    Calls tshark on pcap_path and returns a list of packet dicts.
    Fields: ts (float, epoch), size (int, bytes), direction (+1/-1).
    local_ip: IP of the 'client side' machine at this capture point.
    If None, the first source IP seen is used (reasonable for most cases).

    NOTE: src/dst IPs are used only to determine direction and are
    then discarded. The output contains no IP addresses.
    """
    result = subprocess.run(
        [
            "tshark",
            "-r", pcap_path,
            "-T", "fields",
            "-E", "separator=,",
            "-e", "frame.time_epoch",
            "-e", "frame.len",
            "-e", "ip.src",
            "-e", "ip.dst",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    packets = []
    inferred_local_ip = local_ip

    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue

        parts = line.split(",")
        if len(parts) < 4:
            continue

        ts_str, size_str, src, dst = parts[0], parts[1], parts[2], parts[3]

        if not src or not dst:
            continue

        try:
            ts   = float(ts_str)
            size = int(size_str)
        except ValueError:
            continue

        # Infer local IP from first packet if not provided
        if inferred_local_ip is None:
            inferred_local_ip = src

        direction = +1 if src == inferred_local_ip else -1

        # IPs are intentionally not included in the output dict
        packets.append({
            "ts":        ts,
            "size":      size,
            "direction": direction,
        })

    return packets