# wire-adversary-correlator
**KU Leuven ESAT-COSIC | Master's Thesis**

> End-to-end flow correlation attack on anonymity systems (Nym, Tor, VPN) from the
> perspective of a passive wire adversary. Both vantage points are router-level
> pcap captures — the anonymity system is treated as a complete black box.

---

## Threat Model

```
[Client] ──► [Ingress Router*] ──► [ Black Box (Nym / Tor / VPN) ] ──► [Egress Router*] ──► [Server]
                    ↑                                                           ↑
             Adversary sniffer                                           Adversary sniffer
             (always-on pcap)                                            (always-on pcap)
```

`*` Ubuntu 22.04 VMs on Hetzner Cloud, private VPC, chrony-synchronized.  
The adversary is **passive** — captures headers only, never injects or modifies traffic.

---

## Repository Structure

```
wire-adversary-correlator/
│
├── collector/              # Data collection infrastructure
│   ├── coordinator.py      # SSH orchestrator: starts captures on both routers
│   ├── visit_trigger.py    # Runs on client VM: Playwright browser automation
│   ├── label_logger.py     # Records (t_start, t_end, url, mode) per visit
│   ├── router_setup.sh     # Bootstrap Hetzner router VMs
│   └── README.md
│
├── preprocessing/          # PCAP → model-ready tensors
│   ├── pcap_parser.py      # tshark wrapper: pcap → [{ts, size, direction}]
│   ├── kde.py              # Gaussian KDE: timestamps → density wave
│   ├── windower.py         # Sliding window slicer + time-window carving
│   ├── quartet_builder.py  # (ingress_up/down, egress_up/down) assembly
│   ├── dataset_builder.py  # metadata.jsonl + pcaps → .npz dataset
│   └── README.md
│
├── model/                  # Correlator
│   ├── cnn.py              # Dual-CNN correlator (ShYSh architecture)
│   ├── dataset.py          # PyTorch Dataset: positive/negative Quartet pairs
│   ├── train.py            # Training loop — primary metric: PR-AUC
│   ├── evaluate.py         # PR-AUC, ROC, PR curve, confusion matrix
│   └── README.md
│
├── analysis/               # Results and visualization
│   ├── visualize_shapes.py # Plot KDE shape signals for inspection
│   ├── ablation.py         # Sweep sigma / window_len / duration
│   ├── compare_systems.py  # Nym vs Tor vs VPN vs Baseline comparison
│   └── README.md
│
├── config/
│   ├── infrastructure.py   # Router/client IPs, interfaces, SSH keys
│   └── hyperparams.py      # KDE params, model hyperparams
│
├── scripts/
│   ├── collect_nym.sh
│   ├── collect_tor.sh
│   ├── collect_vpn.sh
│   ├── collect_baseline.sh
│   └── github_setup.sh     # git init + first push
│
├── data/                   # Raw pcaps + metadata     (gitignored)
├── logs/                   # Capture logs             (gitignored)
├── results/                # Models + eval outputs    (gitignored)
│
├── requirements.txt
├── .gitignore
└── README.md
```

---

## Supported Modes

| Mode       | Ingress transport       | Egress transport |
|------------|-------------------------|------------------|
| `nym`      | UDP (Sphinx packets)    | TCP/HTTPS        |
| `tor`      | TLS/TCP (Tor cells)     | TCP/HTTPS        |
| `vpn`      | UDP tunnel              | TCP/HTTPS        |
| `baseline` | TCP/HTTPS               | TCP/HTTPS        |

---

## Quick Start

```bash
# 1. Bootstrap router VMs (run on each Hetzner VM)
sudo bash collector/router_setup.sh

# 2. Fill in your IPs
vim config/infrastructure.py

# 3. Collect
bash scripts/collect_nym.sh --urls config/urls.txt --visits 80

# 4. Preprocess
python preprocessing/dataset_builder.py \
    --labels logs/labels_nym.jsonl \
    --ingress_dir data/raw/ingress \
    --egress_dir  data/raw/egress \
    --output data/nym_dataset.npz

# 5. Train
python model/train.py --dataset data/nym_dataset.npz --mode nym

# 6. Evaluate
python model/evaluate.py \
    --model results/nym_best.pt \
    --dataset data/nym_dataset.npz

# 7. Compare all systems
python analysis/compare_systems.py \
    --nym results/nym_eval.json --tor results/tor_eval.json \
    --vpn results/vpn_eval.json --baseline results/baseline_eval.json
```

---

## KDE Parameters

| Parameter       | Symbol | Default | Note                          |
|-----------------|--------|---------|-------------------------------|
| Kernel width    | σ      | 0.125s  | ShYSh baseline — tune for TCP |
| Sampling period | T      | 0.1s    | 10 samples/sec                |
| Window length   | l      | 30      | = 3 seconds per window        |
| Overlap         | —      | 50%     |                               |
| Flow duration   | —      | 60s     | First N seconds analyzed      |

---

## Infrastructure

- **Cloud:** Hetzner Cloud (Ubuntu 22.04)
- **Capture:** tshark, `--snapshot-length=96` (headers only)
- **Clock sync:** chrony → `time.cloudflare.com`
- **Browser automation:** Playwright (Firefox, headless)
- **ML framework:** PyTorch

---

*Master's Thesis — KU Leuven ESAT-COSIC*
