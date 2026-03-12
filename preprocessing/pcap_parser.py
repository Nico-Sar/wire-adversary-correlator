"""
preprocessing/pcap_parser.py
============================
Wraps tshark to extract per-packet metadata from a pcap file.
Output: list of dicts with keys {ts, size, direction}

direction: +1 = outbound (UP), -1 = inbound (DOWN)
Determined by comparing src IP to the known local IP of the capture point.
"""

from typing import Optional


def extract_packets(pcap_path: str,
                    local_ip: Optional[str] = None) -> list[dict]:
    """
    Calls tshark on pcap_path and returns a list of packet dicts.
    Fields: ts (float, epoch), size (int, bytes), direction (+1/-1).

    local_ip: IP of the 'client side' machine at this capture point.
    If None, the first source IP seen is used (reasonable for most cases).
    """
    raise NotImplementedError
