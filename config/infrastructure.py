INGRESS_ROUTER = {
    "host":         "204.168.184.30",
    "user":         "root",
    "key_path":     "~/.ssh/nico-thesis",
    "iface_wan":    "eth0",
    "iface_client": "enp7s0",
    "capture_dir":  "/tmp/captures",
    "private_ip":   "10.0.0.2",
}

EGRESS_ROUTER = {
    "host":         "204.168.189.97",
    "user":         "root",
    "key_path":     "~/.ssh/nico-thesis",
    "iface_wan":    "eth0",
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
    "baseline": "tcp port 80",
}

BPF_EGRESS = "tcp port 80"

# ── Proxy addresses on each client VM ─────────────────────────────────────────
PROXY_MAP = {
    "nym":      "socks5://127.0.0.1:1080",
    "tor":      "socks5://127.0.0.1:9050",
    "vpn":      None,
    "baseline": None,
}

# ── Capture settings ──────────────────────────────────────────────────────────
SNAPSHOT_LENGTH     = 96    # Headers only — never capture payload
PCAP_ROTATE_SECONDS = 300   # Rotate pcap files every 5 minutes

# ── Clock sync ────────────────────────────────────────────────────────────────
MAX_CLOCK_DRIFT_MS = 5      # Abort if inter-router drift exceeds this

# ── Network interface names (same on all VMs) ─────────────────────────────────
PRIVATE_IFACE = "enp7s0"
PUBLIC_IFACE  = "eth0"