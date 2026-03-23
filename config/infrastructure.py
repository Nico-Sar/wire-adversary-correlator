"""
config/infrastructure.py
========================
Central configuration for all VMs and network interfaces.

VM Map:
  ubuntu-4gb-hel1-6   204.168.184.30   10.0.0.2   INGRESS ROUTER
  ubuntu-4gb-hel1-8   204.168.189.97   10.0.0.3   EGRESS ROUTER
  ubuntu-4gb-hel1-7   204.168.163.45   10.0.0.4   WEB SERVER
  ubuntu-4gb-hel1-10  204.168.184.39   10.0.0.5   CLIENT 1
  ubuntu-4gb-hel1-9   204.168.181.115  10.0.0.6   CLIENT 2

NOTE: Never commit infrastructure_local.py — it is gitignored.
This file contains real IPs and is safe to commit since the project
is academic and the IPs are short-lived Hetzner instances.
"""

# ── Router VMs ────────────────────────────────────────────────────────────────

INGRESS_ROUTER = {
    "host":         "204.168.184.30",   # ubuntu-4gb-hel1-6
    "user":         "root",
    "key_path":     "~/.ssh/nico-thesis",
    "iface_wan":    "eth0",             # Public interface (toward internet / black box)
    "iface_client": "enp7s0",           # Private VPC interface (toward clients)
    "capture_dir":  "/tmp/captures",
    "private_ip":   "10.0.0.2",
}

EGRESS_ROUTER = {
    "host":         "204.168.189.97",   # ubuntu-4gb-hel1-8
    "user":         "root",
    "key_path":     "~/.ssh/nico-thesis",
    "iface_wan":    "eth0",             # Public interface (toward internet / black box)
    "iface_server": "enp7s0",           # Private VPC interface (toward web server)
    "capture_dir":  "/tmp/captures",
    "private_ip":   "10.0.0.3",
}

# ── Web Server ────────────────────────────────────────────────────────────────

WEB_SERVER = {
    "host":       "204.168.163.45",     # ubuntu-4gb-hel1-7
    "user":       "root",
    "key_path":   "~/.ssh/nico-thesis",
    "private_ip": "10.0.0.4",
}

# ── Client VMs ────────────────────────────────────────────────────────────────
# With 2 clients we run one anonymity system per client simultaneously.
# Client 1 runs the active collection session.
# Client 2 runs background/noise traffic or a second session in parallel.

CLIENTS = {
    "client1": {
        "host":       "204.168.184.39",  # ubuntu-4gb-hel1-10
        "user":       "root",
        "key_path":   "~/.ssh/nico-thesis",
        "private_ip": "10.0.0.5",
    },
    "client2": {
        "host":       "204.168.181.115", # ubuntu-4gb-hel1-9
        "user":       "root",
        "key_path":   "~/.ssh/nico-thesis",
        "private_ip": "10.0.0.6",
    },
}

# ── Proxy addresses on each client VM ─────────────────────────────────────────

PROXY_MAP = {
    "nym":      "socks5://127.0.0.1:1080",  # Nym socks5-client
    "tor":      "socks5://127.0.0.1:9050",  # Tor SOCKS5
    "vpn":      None,                        # VPN is transparent
    "baseline": None,
}

# ── BPF filters ───────────────────────────────────────────────────────────────
# Applied on the INGRESS router WAN interface (eth0).
# Captures outbound tunnel traffic entering the black box.

BPF_INGRESS = {
    "nym":      "udp",                               # Sphinx packets are UDP
    "tor":      "tcp port 9001 or tcp port 443",     # Tor OR-port
    "vpn":      "udp port 1194 or udp port 51820",   # OpenVPN / WireGuard
    "baseline": "tcp port 443",
}

# Applied on the EGRESS router private interface (enp7s0).
# Always reconstructed HTTPS toward the web server.
BPF_EGRESS = "tcp port 443"

# ── Capture settings ──────────────────────────────────────────────────────────

SNAPSHOT_LENGTH    = 96     # Headers only — never capture payload
PCAP_ROTATE_SECONDS = 300   # Rotate pcap files every 5 minutes (always-on mode)

# ── Clock sync ────────────────────────────────────────────────────────────────

MAX_CLOCK_DRIFT_MS = 5      # Abort capture run if inter-router drift exceeds this

# ── Network interface names (verified from ip addr show) ──────────────────────

PRIVATE_IFACE = "enp7s0"   # Private VPC interface name on all 5 VMs
PUBLIC_IFACE  = "eth0"      # Public internet interface on all 5 VMs