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
    "client1":     {"host": "204.168.184.39",  "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.3"},
    "client2":     {"host": "204.168.181.115", "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.4"},
    "vpn-client1": {"host": "204.168.205.5",   "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.5"},
    "vpn-client2": {"host": "95.216.218.124",  "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.6"},
    "tor-client1": {"host": "89.167.102.181",  "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.7"},
    "tor-client2": {"host": "204.168.194.172", "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.8"},
    "nym-client1": {"host": "204.168.204.120", "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.9"},
    "nym-client2": {"host": "204.168.201.84",  "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.10"},
}

CLIENT_GROUPS = {
    "baseline": ["client1",     "client2"],
    "vpn":      ["vpn-client1", "vpn-client2"],
    "tor":      ["tor-client1", "tor-client2"],
    "nym":      ["nym-client1", "nym-client2"],
}

BPF_INGRESS = {
    "baseline": "tcp port 80 and host 10.1.0.3",
    "tor":      "tcp port 9001 or tcp port 443",
    "vpn":      "udp port 51820",
    "nym":      "udp",
}

BPF_EGRESS = "tcp port 80 and host 10.1.0.2"

PROXY_MAP = {
    "baseline": None,
    "tor":      "socks5://127.0.0.1:9050",
    "vpn":      None,
    "nym":      "socks5://127.0.0.1:1080",
}

WEB_SERVER_PRIVATE_URL = "http://10.1.0.3"       # baseline, vpn
WEB_SERVER_PUBLIC_URL  = "http://204.168.189.97"  # tor, nym

URL_BASE = {
    "baseline": WEB_SERVER_PRIVATE_URL,
    "vpn":      WEB_SERVER_PRIVATE_URL,
    "tor":      WEB_SERVER_PUBLIC_URL,
    "nym":      WEB_SERVER_PUBLIC_URL,
}
SNAPSHOT_LENGTH    = 96
MAX_CLOCK_DRIFT_MS = 5


def get_client_private_ip(client_id: str) -> str:
    """Returns the private IP for a given client_id.
    Used by quartet_builder for direction inference in pcap parsing."""
    return CLIENTS[client_id]["private_ip"]
