INGRESS_ROUTER = {
    "host":         "204.168.184.30",
    "user":         "root",
    "key_path":     "~/.ssh/nico-thesis",
    "iface_client": "enp7s0",
    # nym2 clients are standalone Hetzner VMs with their own public IPs — they
    # are not on the 10.0.0.x private subnet behind this router at all, so
    # their WireGuard traffic never transits here on any interface. Kept only
    # so start_ingress() has a value to read; nym2 is in EGRESS_ONLY_MODES.
    "iface_nym2_ingress": "enp7s0",
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
    "vpn-client1":  {"host": "204.168.205.5",   "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.5"},
    "vpn-client2":  {"host": "204.168.184.39",  "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.3"},
    "tor-client1":  {"host": "89.167.102.181",  "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.7"},
    "tor-client2":  {"host": "204.168.194.172", "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.8"},
    "nym5-client1": {"host": "204.168.204.120", "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.9"},
    "nym5-client2": {"host": "178.104.191.219", "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.10"},
    "nym2-client1": {"host": "95.216.218.124",  "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.4"},
    "nym2-client2": {"host": "178.104.184.192", "user": "root", "key_path": "~/.ssh/nico-thesis", "private_ip": "10.0.0.6"},
}

CLIENT_GROUPS = {
    "vpn":      ["vpn-client1", "vpn-client2"],
    "tor":      ["tor-client1", "tor-client2"],
    "nym5":     ["nym5-client1", "nym5-client2"],
    "nym2":     ["nym2-client1", "nym2-client2"],
}

# Modes whose clients physically never appear on the ingress router's private
# interface.  For these modes the ingress pcap will always be empty; the
# low-packet and zero-window guards in dataset_builder / analyze scripts must
# skip ingress stream checks so valid egress-only visits are not discarded.
# nym2/nym5 clients' outbound tunnel traffic exits via their own public eth0,
# but return traffic from the internet to their private IPs is still visible
# at the ingress router's enp7s0 — so both modes get real ingress capture.
EGRESS_ONLY_MODES = set()

# Port/protocol fragments only — NOT complete filters. A bare port-only (or
# even port+webserver-host) filter on a shared ingress interface captures
# ANY same-mode client's traffic that happens to be active concurrently:
# confirmed live (nym2-client1's port-51822 traffic leaked into a concurrent
# nym2-client2 capture). build_ingress_bpf() below ANDs each fragment with
# the requesting client's own enp7s0 private IP, so every ingress capture is
# scoped to exactly the client it's being collected for. Fragments are
# parenthesized so appending "and host <ip>" is always unambiguous, even
# when the fragment itself contains an "or".
BPF_INGRESS = {
    "tor":      "(tcp port 9001 or tcp port 443)",
    "vpn":      "(udp port 51820)",
    # nym5-client1's default route was migrated to transit the ingress router
    # on enp7s0 (see collector.coordinator._NYM_CLIENTS_VIA_INGRESS_ROUTER), so
    # the outer Sphinx-transport TCP flow itself is visible here. Verified
    # live across 8 rotations / 8 distinct entry mix-nodes — exactly one TCP
    # connection per rotation, always port 9000; port 9001 never observed,
    # so it is intentionally not included. A host-only filter was tried first
    # and rejected for noise (NTP/HTTPS once general traffic also routes via
    # enp7s0) — port+host together is what's actually used per client.
    "nym5":     "(tcp port 9000)",
    # nym2-client1's default route was migrated to transit the ingress
    # router on enp7s0 (see collector.coordinator._NYM_CLIENTS_VIA_INGRESS_ROUTER),
    # so the outer WireGuard UDP itself is now visible here, not just return
    # traffic. Verified stable at port 51822 across 8 rotations / 7 distinct
    # entry gateways.
    "nym2":     "(udp port 51822)",
}


def build_ingress_bpf(mode: str, client_id: str) -> str:
    """
    Returns the per-client ingress BPF filter: the mode's port/protocol
    fragment ANDed with the requesting client's own enp7s0 private IP.

    Without this, two same-mode clients (e.g. nym2-client1 and nym2-client2)
    running concurrently on the shared ingress interface would each capture
    the OTHER's traffic too — invisible contamination that still produces a
    non-zero pcap with the right port, just from the wrong client.
    """
    return f"{BPF_INGRESS[mode]} and host {CLIENTS[client_id]['private_ip']}"

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
