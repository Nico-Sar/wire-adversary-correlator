"""
preprocessing/pcap_parser.py
============================
Wraps tshark to extract per-packet metadata from a pcap file.
Output: list of dicts with keys {ts, size, direction}

direction: +1 = outbound (UP), -1 = inbound (DOWN)
Determined by comparing src IP to the known local IP of the capture point.
IPs are intentionally dropped from output — the ML pipeline never sees them.

local_ip must be passed explicitly by the caller:
  - For ingress captures: INGRESS_ROUTER["private_ip"]  → "10.0.0.2"
  - For egress  captures: EGRESS_ROUTER["private_ip"]   → "10.1.0.2"

Rationale: inferring local_ip from the first packet is unreliable for
Nym/Tor/VPN modes where the first egress packet may originate from an
exit node IP rather than the router's own private IP.
"""

import shutil
import subprocess


def extract_packets(pcap_path: str,
                    local_ip:  str) -> list[dict]:
    """
    Calls tshark on pcap_path and returns a list of packet dicts.

    Fields:
        ts        (float) — absolute epoch timestamp in seconds
        size      (int)   — packet size in bytes
        direction (int)   — +1 outbound (UP), -1 inbound (DOWN)

    Args:
        pcap_path: path to the .pcap file to parse
        local_ip:  IP address of the 'local' side at this capture point.
                   Packets where src == local_ip are UP (+1),
                   all others are DOWN (-1).
                   Use INGRESS_ROUTER["private_ip"] or
                       EGRESS_ROUTER["private_ip"] from infrastructure.py.

    NOTE: src/dst IPs are used only to determine direction and are
    then discarded. The output contains no IP addresses.
    """
    tshark = shutil.which("tshark") or "/usr/bin/tshark"

    result = subprocess.run(
        [
            tshark,
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

        direction = +1 if src == local_ip else -1

        # IPs are intentionally not included in the output dict
        packets.append({
            "ts":        ts,
            "size":      size,
            "direction": direction,
        })

    return packets