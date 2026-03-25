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
    "client1": {"host": "204.168.184.39",  "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.3"},
    "client2": {"host": "204.168.181.115", "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.4"},
}

BPF_INGRESS = {
    "nym":      "udp",
    "tor":      "tcp port 9001 or tcp port 443",
    "vpn":      "udp port 1194 or udp port 51820",
    "baseline": f"tcp port 80 and host 10.1.0.3",
}

BPF_EGRESS = "tcp port 80 and host 10.1.0.2"

# ── Proxy addresses on each client VM ─────────────────────────────────────────
PROXY_MAP = {
    "nym":      "socks5://127.0.0.1:1080",
    "tor":      "socks5://127.0.0.1:9050",
    "vpn":      None,
    "baseline": None,
}

# ── Capture settings ──────────────────────────────────────────────────────────
SNAPSHOT_LENGTH     = 96    # Headers only — never capture payload

# ── Clock sync ────────────────────────────────────────────────────────────────
MAX_CLOCK_DRIFT_MS = 5      # Abort if inter-router drift exceeds this


def get_client_private_ip(client_id: str) -> str:
    """Return the private LAN IP for the given client_id (e.g. 'client1')."""
    return CLIENTS[client_id]["private_ip"]