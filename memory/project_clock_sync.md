---
name: Clock synchronization between capture nodes
description: NTP/chrony offset for ingress and egress capture nodes — confirmed below KDE sigma threshold
type: project
---

Ingress and egress capture nodes verified via chrony as of 2026-04-21.

- Ingress RMS offset: 0.37 ms
- Egress RMS offset: 0.08 ms
- KDE bandwidth σ = 125 ms (baseline/VPN), 250 ms (Tor), 500 ms (Nym)

Both nodes are well below the σ threshold — timestamp alignment is NOT a source of correlation error.

**Why:** ShYSh aligns both sides to the same physical event on one machine; our setup uses wall-clock labels. Clock skew > σ would smear the KDE correlation signal.

**How to apply:** Clock sync is not a current concern. Only revisit if capture hardware changes or chrony is reconfigured.
