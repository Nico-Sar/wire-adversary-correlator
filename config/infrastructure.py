INGRESS_ROUTER = {
    "host":         "204.168.184.30",
    "user":         "root",
    "key_path":     "~/.ssh/nico-thesis",
    "iface_client": "enp7s0",
    "capture_dir":  "/tmp/captures",
    "private_ip":   "10.0.0.2",
}

EGRESS_ROUTER = {
    "host":         "204.168.189.97",
    "user":         "root",
    "key_path":     "~/.ssh/nico-thesis",
    "iface_server": "enp7s0",
    "capture_dir":  "/tmp/captures",
    "private_ip":   "10.1.0.2",
}

WEB_SERVER = {
    "host":       "204.168.163.45",
    "user":       "root",
    "key_path":   "~/.ssh/nico-thesis",
    "private_ip": "10.1.0.3",
}

CLIENTS = {
    "client1":      {"host": "204.168.184.39",  "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.3"},
    "vpn-client1":  {"host": "204.168.205.5",   "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.5"},
    "tor-client1":  {"host": "89.167.102.181",  "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.7"},
    "tor-client2":  {"host": "204.168.194.172", "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.8"},
    "nym5-client1": {"host": "204.168.204.120", "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.9"},
    "nym5-client2": {"host": "204.168.201.84",  "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.10"},
    "nym2-client1": {"host": "204.168.181.115",  "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.4"},
    "nym2-client2": {"host": "95.216.218.124",   "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.6"},
}

CLIENT_GROUPS = {
    "baseline": ["client1"],
    "vpn":      ["vpn-client1"],
    "tor":      ["tor-client1", "tor-client2"],
    "nym5":     ["nym5-client1", "nym5-client2"],
    "nym2":     ["nym2-client1", "nym2-client2"],
}

BPF_INGRESS = {
    "baseline": "tcp port 80 and host 10.1.0.3",
    "tor":      "tcp port 9001 or tcp port 443",
    "vpn":      "udp port 51820",
    # NymVPN v1.27.0 uses TCP port 9000/9001 for Sphinx packet transport
    # to entry gateways. Traffic confirmed visible at ingress router enp7s0.
    # Host guard restricts to per-mode private IPs to avoid cross-mode capture.
    "nym5":     "(tcp port 9000 or tcp port 9001) and (host 10.0.0.9  or host 10.0.0.10)",
    # nym2 (2-hop WireGuard): capture outer UDP packets at the ingress router.
    # Web traffic is encapsulated in WireGuard UDP from the physical private IPs.
    "nym2":     "udp and (host 10.0.0.4 or host 10.0.0.6)",
}

BPF_EGRESS = {
    "baseline": "tcp port 80   and host 10.1.0.2",
    "vpn":      "tcp port 8080 and host 10.1.0.2",
    "tor":      "tcp port 8081 and host 10.1.0.2",
    "nym5":     "tcp port 8082 and host 10.1.0.2",
    "nym2":     "tcp port 80   and host 10.1.0.2",
}

PROXY_MAP = {
    "baseline": None,
    "tor":      "socks5://127.0.0.1:9050",
    "vpn":      None,
    "nym5":     "socks5://127.0.0.1:1080",
    "nym2":     None,
}

URL_BASE = {
    "baseline": "http://10.1.0.3",        # port 80 (default)
    "vpn":      "http://10.1.0.3:8080",
    "tor":      "http://204.168.189.97:8081",
    "nym5":     "http://204.168.189.97:8082",
    "nym2":     "http://204.168.189.97",
}
TOR_CONTROL_PASSWORD  = "thesis2026"
# Kept as a fallback constant; rotation now uses --exit-random by default.
NYM_EXIT_GATEWAY_ID   = "2xU4CBE6QiiYt6EyBXSALwxkNvM7gqJfjHXaMkjiFmYW"

SNAPSHOT_LENGTH    = 96
MAX_CLOCK_DRIFT_MS = 5


def get_client_private_ip(client_id: str) -> str:
    """Returns the private IP for a given client_id.
    Used by quartet_builder for direction inference in pcap parsing."""
    return CLIENTS[client_id]["private_ip"]
