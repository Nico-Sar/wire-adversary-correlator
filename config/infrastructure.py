"""
config/infrastructure.py
========================
Central configuration for all VMs and network interfaces.
Copy this file to infrastructure_local.py and fill in your values.
infrastructure_local.py is gitignored — never commit real IPs or key paths.
"""

# ── Router VMs ────────────────────────────────────────────────────────────────

INGRESS_ROUTER = {
    "host":         "INGRESS_ROUTER_IP",    # Hetzner public IP
    "user":         "root",
    "key_path":     "~/.ssh/id_ed25519",
    "iface_client": "eth1",                 # Interface facing client VPC
    "iface_wan":    "eth0",                 # Interface facing internet / black box
    "capture_dir":  "/tmp/captures",
}

EGRESS_ROUTER = {
    "host":         "EGRESS_ROUTER_IP",     # Hetzner public IP
    "user":         "root",
    "key_path":     "~/.ssh/id_ed25519",
    "iface_wan":    "eth0",                 # Interface facing privacy network exit
    "iface_server": "eth1",                 # Interface facing target server
    "capture_dir":  "/tmp/captures",
}

# ── Client VMs ────────────────────────────────────────────────────────────────

CLIENTS = {
    "nym":      {"host": "CLIENT_NYM_IP",      "user": "user", "key_path": "~/.ssh/id_ed25519"},
    "tor":      {"host": "CLIENT_TOR_IP",      "user": "user", "key_path": "~/.ssh/id_ed25519"},
    "vpn":      {"host": "CLIENT_VPN_IP",      "user": "user", "key_path": "~/.ssh/id_ed25519"},
    "baseline": {"host": "CLIENT_BASELINE_IP", "user": "user", "key_path": "~/.ssh/id_ed25519"},
}

# ── Proxy addresses on each client VM ─────────────────────────────────────────

PROXY_MAP = {
    "nym":      "socks5://127.0.0.1:1080",  # Nym socks5-client
    "tor":      "socks5://127.0.0.1:9050",  # Tor SOCKS5
    "vpn":      None,                        # VPN is transparent
    "baseline": None,
}

# ── BPF filters ───────────────────────────────────────────────────────────────
# Ingress WAN interface: capture outbound tunnel traffic entering the black box

BPF_INGRESS = {
    "nym":      "udp",                              # Sphinx packets are UDP
    "tor":      "tcp port 9001 or tcp port 443",    # Tor OR-port
    "vpn":      "udp port 1194 or udp port 51820",  # OpenVPN / WireGuard
    "baseline": "tcp port 443",
}

# Egress server interface: always reconstructed HTTPS
BPF_EGRESS = "tcp port 443"

# ── Capture settings ──────────────────────────────────────────────────────────

SNAPSHOT_LENGTH = 96        # Headers only — never capture payload
PCAP_ROTATE_SECONDS = 300   # Rotate pcap files every N seconds (always-on mode)

# ── Clock sync ────────────────────────────────────────────────────────────────

MAX_CLOCK_DRIFT_MS = 5      # Abort capture run if inter-router drift exceeds this
