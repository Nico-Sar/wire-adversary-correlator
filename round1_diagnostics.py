#!/usr/bin/env python3
"""round1_diagnostics.py — diagnostic plots for a completed campaign round."""
import sys, json, os, re
from pathlib import Path
from collections import defaultdict
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

MODES = ["vpn", "tor", "nym5", "nym2"]
COLORS = {"vpn": "#2563eb", "tor": "#7c3aed", "nym5": "#dc2626", "nym2": "#059669"}

def load_visits(round_dir, mode):
    f = Path(round_dir) / f"{mode}_visits.jsonl"
    if not f.exists(): return []
    out = []
    for line in f.open():
        try: out.append(json.loads(line))
        except: pass
    return out

def main(round_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    visits = {m: load_visits(round_dir, m) for m in MODES}

    # 1. throughput curves
    plt.figure(figsize=(10, 6))
    for m in MODES:
        succ = [v for v in visits[m] if v.get("visit_status")=="success" and "t_visit_start" in v]
        if not succ: continue
        ts = sorted(v["t_visit_start"] for v in succ); t0 = ts[0]
        hrs = [(t-t0)/3600 for t in ts]
        plt.plot(hrs, range(1,len(hrs)+1), label=f"{m} ({len(hrs)})", color=COLORS[m], lw=2)
    plt.xlabel("Hours since round start"); plt.ylabel("Cumulative successful flows")
    plt.title("Throughput per mode"); plt.legend(); plt.grid(alpha=0.3)
    plt.savefig(f"{out_dir}/throughput_curves.png", dpi=130, bbox_inches="tight"); plt.close()

    # 2. packet histograms
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    for ax, m in zip(axes.flat, MODES):
        pk = [v.get("ingress_packets",0) for v in visits[m] if v.get("visit_status")=="success"]
        pk = [p for p in pk if p > 0]
        if not pk: ax.set_title(f"{m}: no data"); continue
        ax.hist(pk, bins=np.logspace(0, np.log10(max(pk)+1), 40), color=COLORS[m], alpha=0.8)
        ax.set_xscale("log"); ax.axvline(50, color="k", ls="--", alpha=0.5, label="50-pkt")
        ax.set_title(f"{m}: n={len(pk)}, min={min(pk)}, med={int(np.median(pk))}")
        ax.set_xlabel("ingress packets (log)"); ax.legend(fontsize=8)
    plt.suptitle("Ingress-packet distribution per mode"); plt.tight_layout()
    plt.savefig(f"{out_dir}/packet_histograms.png", dpi=130); plt.close()

    # 3. yield/loss
    plt.figure(figsize=(9, 5))
    succ_c, unrec_c = [], []
    for m in MODES:
        s = sum(1 for v in visits[m] if v.get("visit_status")=="success")
        u = sum(1 for v in visits[m] if v.get("visit_status")=="WEDGE_UNRECOVERABLE")
        succ_c.append(s); unrec_c.append(u)
    x = range(len(MODES))
    plt.bar(x, succ_c, color=[COLORS[m] for m in MODES], label="success")
    plt.bar(x, unrec_c, bottom=succ_c, color="black", alpha=0.7, label="unrecoverable")
    for i,(s,u) in enumerate(zip(succ_c,unrec_c)):
        r = 100*u/(s+u) if (s+u) else 0
        plt.text(i, s+u, f"{r:.2f}% loss", ha="center", va="bottom", fontsize=9)
    plt.xticks(x, MODES); plt.ylabel("flows"); plt.title("Yield vs unrecoverable loss")
    plt.legend(); plt.savefig(f"{out_dir}/yield_loss.png", dpi=130, bbox_inches="tight"); plt.close()

    # 4. hang frequency
    alerts = Path(round_dir) / "ALERTS.log"
    if alerts.exists():
        from datetime import datetime
        per_client = defaultdict(list)
        for line in alerts.open():
            mo = re.search(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\].*VM-hang detected on (\S+)", line)
            if mo:
                per_client[mo.group(2)].append(datetime.strptime(mo.group(1), "%Y-%m-%d %H:%M:%S"))
        if per_client:
            plt.figure(figsize=(11, 5))
            t0 = min(t for v in per_client.values() for t in v)
            for c, times in sorted(per_client.items()):
                hrs = sorted((t-t0).total_seconds()/3600 for t in times)
                plt.plot(hrs, range(1,len(hrs)+1), marker=".", label=f"{c} ({len(hrs)})")
            plt.xlabel("Hours since first hang"); plt.ylabel("Cumulative hangs")
            plt.title("VM-hang frequency per client"); plt.legend(fontsize=8); plt.grid(alpha=0.3)
            plt.savefig(f"{out_dir}/hang_frequency.png", dpi=130, bbox_inches="tight"); plt.close()

    # 5. flow duration
    plt.figure(figsize=(9, 5))
    for m in MODES:
        durs = [v["t_visit_end"]-v["t_visit_start"] for v in visits[m]
                if v.get("visit_status")=="success" and "t_visit_end" in v and "t_visit_start" in v]
        if durs:
            plt.hist(durs, bins=40, alpha=0.5, label=f"{m} (med {np.median(durs):.1f}s)", color=COLORS[m])
    plt.xlabel("visit duration (s)"); plt.ylabel("flows"); plt.title("Flow duration per mode")
    plt.legend(); plt.savefig(f"{out_dir}/flow_duration.png", dpi=130, bbox_inches="tight"); plt.close()

    print(f"Plots written to {out_dir}/")
    print("Summary:")
    for m in MODES:
        s = sum(1 for v in visits[m] if v.get("visit_status")=="success")
        pk = [v.get("ingress_packets",0) for v in visits[m] if v.get("visit_status")=="success" and v.get("ingress_packets",0)>0]
        thin = sum(1 for p in pk if p < 50)
        print(f"  {m}: {s} success, {thin} thin(<50pkt, {100*thin/max(len(pk),1):.1f}%), med {int(np.median(pk)) if pk else 0} pkts")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: round1_diagnostics.py <round_dir> <out_dir>"); sys.exit(1)
    main(sys.argv[1], sys.argv[2])
