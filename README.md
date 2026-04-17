# wire-adversary-correlator
**KU Leuven ESAT-COSIC | Master's Thesis**

> End-to-end flow correlation attack on anonymity systems (Nym, Tor, VPN, Baseline) from the
> perspective of a passive wire adversary. Both vantage points are router-level
> pcap captures — the anonymity system is treated as a complete black box.

---

## Threat Model

```
[Client] ──► [Ingress Router*] ──► [ Black Box (Nym / Tor / VPN) ] ──► [Egress Router*] ──► [Server]
                    ↑                                                           ↑
             Adversary sniffer                                           Adversary sniffer
             (per-visit pcap)                                            (per-visit pcap)
```

`*` Ubuntu 22.04 VMs on Hetzner Cloud private VPC, clocks synchronized via chrony
(`< 5 ms` inter-router drift enforced before every collection run).  
The adversary is **passive** — captures headers only (`--snapshot-length=96`), never injects or modifies traffic.

---

## Supported Modes

| Mode       | Ingress transport                        | Timing obfuscation |
|------------|------------------------------------------|--------------------|
| `baseline` | Direct TCP/80                            | None               |
| `vpn`      | WireGuard UDP tunnel (port 51820)        | None               |
| `tor`      | Tor relay cells — TCP/9001 or TCP/443    | Medium (3-hop mix) |
| `nym`      | Sphinx-over-TCP (ports 9000/9001) †      | High (mixnet + cover traffic) |

† NymVPN CLI v1.2.7 uses TCP for Sphinx packet transport to entry gateways,
confirmed at ingress router on `nym-client → gateway:9000/9001` connections.
BPF filter includes host guards to prevent collision with Tor's ORPort (9001).

---

## Repository Structure

```
wire-adversary-correlator/
│
├── collector/              # Data collection infrastructure
│   ├── coordinator.py      # SSH orchestrator: starts tshark on both routers,
│   │                       # triggers browser visits, pulls pcaps, logs VisitRecord
│   ├── visit_trigger.py    # Runs on client VM: Playwright (HTML) / curl (binary)
│   ├── label_logger.py     # Context manager for visit timing (kept for future use)
│   └── README.md
│
├── preprocessing/          # PCAP → model-ready tensors
│   ├── pcap_parser.py      # tshark wrapper: pcap → [{ts, size, direction}]
│   ├── kde.py              # Gaussian KDE: timestamps → density wave (padded boundaries)
│   ├── windower.py         # Sliding window slicer + time-window carving
│   ├── quartet_builder.py  # (ingress_up/down, egress_up/down) assembly per visit
│   ├── dataset_builder.py  # coordinator JSONL + pcaps → .npz; independent shuffle
│   └── README.md
│
├── model/                  # Correlator
│   ├── cnn.py              # Dual-CNN correlator (ShYSh architecture)
│   ├── dataset.py          # PyTorch Dataset: positive + hard/soft negative pairs
│   ├── train.py            # Training loop — primary metric: PR-AUC
│   ├── evaluate.py         # PR-AUC, ROC-AUC, PR curve, confusion matrix
│   └── README.md
│
├── analysis/               # Results and visualization
│   ├── visualize_shapes.py # Plot KDE shape signals for inspection
│   ├── ablation.py         # Sweep sigma / window_len / duration
│   ├── compare_systems.py  # Nym vs Tor vs VPN vs Baseline comparison
│   └── README.md
│
├── config/
│   ├── infrastructure.py   # Router/client IPs, BPF filters, SSH keys
│   └── hyperparams.py      # KDE params, model hyperparams, visit timeouts
│
├── scripts/
│   ├── collect_{baseline,vpn,tor,nym}.sh  # Per-mode collection wrappers
│   ├── preprocess_{pilot,all}.sh          # Preprocessing pipeline runners
│   ├── plot_kde_shapes.py  # KDE shape comparison figure (ingress vs egress)
│   ├── check_pilot_npz.py  # Inspect .npz dataset contents
│   └── router_setup.sh     # Bootstrap Hetzner router VMs
│
├── tests/                  # pytest test suite (35 tests)
│   ├── test_kde.py
│   ├── test_windower.py
│   └── test_dataset.py
│
├── data/                   # Raw pcaps + metadata     (gitignored)
├── logs/                   # Capture logs             (gitignored)
├── results/                # Models + eval outputs    (gitignored)
├── figures/                # Generated plots          (gitignored)
│
├── PIPELINE_AUDIT.md       # Correctness audit: 7 bugs found and fixed
├── pyproject.toml
└── README.md
```

---

## Pipeline

```
config/urls.txt
      ↓
coordinator.py ──SSH──► Ingress Router: tshark → {visit_id}_ingress.pcap
               ──SSH──► Egress Router:  tshark → {visit_id}_egress.pcap
               ──SSH──► Client VM:      visit_trigger.py (Playwright / curl)
      ↓
data/{mode}_visits.jsonl  +  data/{mode}/{visit_id}_{side}.pcap

      ↓  preprocessing/dataset_builder.py
data/{mode}_dataset.npz
  ├── X_ingress_up/down   float32  (N, max_windows, 30)
  ├── X_egress_up/down    float32  (N, max_windows, 30)
  ├── pairs               int32    (N, 3)  [ingress_idx, egress_idx, label=1]
  ├── ingress/egress_visit_ids, ingress/egress_urls, modes
      ↓
Dual-CNN correlator  →  correlation score per pair  →  PR-AUC evaluation
```

**Leakage prevention:** ingress and egress arrays are shuffled with independent
permutations. The `pairs` array records which row indices correspond to the same
visit. Negative pairs are constructed at training time in `model/dataset.py`.

**Split strategy:** 70/15/15 train/val/test by URL (not by visit), following
ShYSh — a URL seen in training is never in the test set.

---

## Quick Start

```bash
# Install (makes `from config.X import Y` work from any script)
pip install -e .

# Run tests
pytest tests/ -v

# Collect one mode (runs on control machine, SSHes to cloud VMs)
python -m collector.coordinator \
    --mode baseline \
    --urls config/urls.txt \
    --visits 80 \
    --client client1 \
    --output data/

# Preprocess to .npz
python -m preprocessing.dataset_builder \
    --labels data/baseline_visits.jsonl \
    --data_dir data/baseline/ \
    --output data/baseline_dataset.npz \
    --mode baseline

# Train
python model/train.py --dataset data/baseline_dataset.npz

# Evaluate
python model/evaluate.py \
    --model results/baseline_best.pt \
    --dataset data/baseline_dataset.npz

# Plot KDE shape comparison
python scripts/plot_kde_shapes.py \
    --baseline data/pilot/baseline_dataset.npz \
    --tor      data/pilot/tor_dataset.npz \
    --vpn      data/pilot/vpn_dataset.npz \
    --nym      data/pilot/nym_dataset.npz \
    --output   figures/kde_shape_comparison.png
```

---

## KDE Hyperparameters

Default values (from `config/hyperparams.py`):

| Parameter       | Symbol | Default | Note                     |
|-----------------|--------|---------|--------------------------|
| Kernel width    | σ      | 0.125 s | Tuned per mode (see below) |
| Sampling period | T      | 0.1 s   | 10 samples/sec           |
| Window length   | l      | 30      | = 3 seconds per window   |
| Overlap         | —      | 50%     |                          |
| Min packets     | —      | 5       | Per stream (not total)   |

Per-mode overrides (duration and σ scale with anonymity latency):

| Mode       | Duration | σ      | KDE samples |
|------------|----------|--------|-------------|
| `baseline` | 30 s     | 0.125 s | 300        |
| `vpn`      | 30 s     | 0.125 s | 300        |
| `tor`      | 60 s     | 0.25 s  | 600        |
| `nym`      | 120 s    | 0.5 s   | 1200       |

Boundary truncation is avoided by evaluating kernels on a `±3σ`-padded grid
and cropping back to `[0, duration]` (see `preprocessing/kde.py`).

---

## Infrastructure

| Host           | Public IP        | Private IP | Role                        |
|----------------|------------------|------------|-----------------------------|
| Ingress router | 204.168.184.30   | 10.0.0.2   | Client-side tshark capture  |
| Egress router  | 204.168.189.97   | 10.1.0.2   | Server-side tshark capture  |
| Web server     | 204.168.163.45   | 10.1.0.3   | Static resource host        |
| client1/2      | 10.0.0.3–4       | —          | Baseline clients            |
| vpn-client1/2  | 10.0.0.5–6       | —          | WireGuard clients           |
| tor-client1/2  | 10.0.0.7–8       | —          | Tor clients                 |
| nym-client1/2  | 10.0.0.9–10      | —          | NymVPN clients              |

- **Cloud:** Hetzner Cloud (CX22, Ubuntu 22.04)
- **Capture:** tshark, `--snapshot-length=96` (headers only — no payload)
- **Clock sync:** chrony → `time.cloudflare.com` (< 5 ms inter-router drift)
- **Browser:** Playwright (Firefox, headless) for HTML; curl for binary resources
- **ML:** PyTorch, primary metric PR-AUC (ShYSh methodology)

---

*Master's Thesis — KU Leuven ESAT-COSIC*
